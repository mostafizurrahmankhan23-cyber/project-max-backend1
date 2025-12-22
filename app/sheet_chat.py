# app/sheet_chat.py
from __future__ import annotations
import os, re, math
from typing import List, Dict, Tuple

_word = re.compile(r"[a-z0-9]+", re.I)

def tok(s: str) -> List[str]:
    return _word.findall((s or "").lower())

def tf(tokens):
    if not tokens: return {}
    c = {}
    for t in tokens: c[t] = c.get(t, 0) + 1
    n = float(len(tokens))
    return {k: v/n for k,v in c.items()}

def idf(docs_tokens):
    N = len(docs_tokens)
    df = {}
    for toks in docs_tokens:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    return {t: math.log((N+1)/(d+1))+1.0 for t,d in df.items()}

def tfidf(tokens, idf_map):
    t = tf(tokens)
    return {k: v*idf_map.get(k,0.0) for k,v in t.items()}

def cos(a,b):
    if not a or not b: return 0.0
    if len(a) > len(b): a,b = b,a
    dot = sum(v*b.get(k,0.0) for k,v in a.items())
    na = math.sqrt(sum(v*v for v in a.values()))
    nb = math.sqrt(sum(v*v for v in b.values()))
    return 0.0 if na==0 or nb==0 else dot/(na*nb)

def chunk_csv(tab_name: str, csv_text: str, max_chars: int = 1800, overlap: int = 200) -> List[str]:
    txt = f"=== TAB: {tab_name} ===\n" + (csv_text or "")
    txt = txt.strip()
    if not txt: return []
    out = []
    i = 0
    while i < len(txt):
        j = min(len(txt), i + max_chars)
        out.append(txt[i:j])
        if j == len(txt): break
        i = max(0, j - overlap)
    return out

def retrieve(tabs: List[Dict], question: str, k: int = 5) -> List[Tuple[float, str]]:
    chunks = []
    for t in tabs:
        chunks += chunk_csv(t.get("name",""), t.get("csv",""))
    toks = [tok(c) for c in chunks]
    idf_map = idf(toks) if toks else {}
    vecs = [tfidf(x, idf_map) for x in toks]
    qv = tfidf(tok(question), idf_map)

    scored = []
    for i, v in enumerate(vecs):
        s = cos(qv, v)
        if s > 0:
            scored.append((s, chunks[i]))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:k]

def use_gemini() -> bool:
    return bool(os.getenv("GEMINI_API_KEY","").strip())

def answer_with_gemini(question: str, contexts: List[str]) -> str:
    import google.generativeai as genai
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    model = genai.GenerativeModel(os.getenv("GEMINI_MODEL","gemini-1.5-flash"))

    ctx = "\n\n---\n\n".join(contexts[:5])
    prompt = f"""You are Project Max Sheet AI.
Answer ONLY using the context from the spreadsheet below.
If the answer isn't in the context, say: "Not found in this spreadsheet."

QUESTION:
{question}

SPREADSHEET CONTEXT:
{ctx}
"""
    r = model.generate_content(prompt)
    return (getattr(r, "text", "") or "").strip()

def answer(question: str, tabs: List[Dict]) -> Dict:
    hits = retrieve(tabs, question, k=5)
    contexts = [h[1] for h in hits]

    if use_gemini():
        try:
            return {"answer": answer_with_gemini(question, contexts), "sources": contexts}
        except Exception as e:
            # fallback
            pass

    if not contexts:
        return {"answer": "Not found in this spreadsheet.", "sources": []}

    return {
        "answer": "Top matching excerpt from this spreadsheet:\n\n" + contexts[0],
        "sources": contexts
    }
