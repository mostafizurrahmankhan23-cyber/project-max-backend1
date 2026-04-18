import os
import json
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

MAX_TABS = 8
DEFAULT_MAX_ROWS_PER_TAB = 120
REDEMPTION_MAX_ROWS = 1000
MAX_COLS_PER_TAB = 20
MAX_CELL_LEN = 120

def _trim_cell(x):
    s = "" if x is None else str(x)
    s = s.strip()
    if len(s) > MAX_CELL_LEN:
        s = s[:MAX_CELL_LEN] + "..."
    return s

def _select_relevant_tabs(question: str, tabs: dict) -> dict:
    q = question.lower()

    keyword_map = {
        "redemption": ["Redemption Status", "Parse Check", "Cost Redemption Stats"],
        "issuance": ["Issuance Status", "GRIT-Issuance Status", "Delnotic-Issuance Status"],
        "transfer": ["Transfer Status"],
        "exchange": ["Exchange"],
        "device wise sales": ["Device Wise Sales Status"],
        "device status": ["Device Status"],
        "cogs": ["COGS"],
        "registration": ["Registration Data-GRIT", "Registration Data-Delnotic", "Device Registration Status"],
    }

    selected_names = []
    for kw, names in keyword_map.items():
        if kw in q:
            selected_names.extend(names)

    if not selected_names:
        selected_names = [
            "Redemption Status",
            "Exchange",
            "Transfer Status",
            "Device Wise Sales Status",
            "COGS",
            "Registration Data-GRIT",
            "Registration Data-Delnotic",
        ]

    out = {}
    for name in selected_names:
        if name in tabs and name not in out:
            out[name] = tabs[name]
        if len(out) >= MAX_TABS:
            break

    if not out:
        for k, v in tabs.items():
            out[k] = v
            if len(out) >= MAX_TABS:
                break

    return out

def _compress_snapshot(snapshot: dict, question: str) -> dict:
    tabs = snapshot.get("tabs", {}) or {}
    tabs = _select_relevant_tabs(question, tabs)

    compact_tabs = {}
    for tab_name, tab in tabs.items():
        header = [_trim_cell(h) for h in (tab.get("header", [])[:MAX_COLS_PER_TAB])]

        max_rows_for_this_tab = REDEMPTION_MAX_ROWS if tab_name == "Redemption Status" else DEFAULT_MAX_ROWS_PER_TAB

        rows = []
        for r in (tab.get("rows", [])[:max_rows_for_this_tab]):
            vals = [_trim_cell(v) for v in (r.get("values", [])[:MAX_COLS_PER_TAB])]
            rows.append({
                "row": r.get("row"),
                "values": vals
            })

        compact_tabs[tab_name] = {
            "header": header,
            "rows": rows
        }

    return {
        "generated_at": snapshot.get("generated_at"),
        "tabs": compact_tabs
    }

def ask_about_snapshot(question: str, snapshot: dict, previous_response_id: str | None = None) -> dict:
    instructions = (
        "You are a finance spreadsheet assistant. "
        "The user is asking about a live spreadsheet snapshot from Google Sheets. "
        "Answer directly from the spreadsheet content and normal reasoning. "
        "If something is ambiguous, ask a clarification question. "
        "If the user explains the meaning of a tab or column, use that explanation in later turns of the same conversation. "
        "Mention relevant tab names and columns when helpful. "
        "Be concise and concrete. "
        "Do calculations when possible from the provided rows. "
        "If the visible data is insufficient, clearly say what additional tab or rows are needed."
    )

    compact_snapshot = _compress_snapshot(snapshot, question)
    snapshot_text = json.dumps(compact_snapshot, ensure_ascii=False)

    kwargs = {
        "model": MODEL,
        "instructions": instructions,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"Spreadsheet snapshot:\n{snapshot_text}\n\nQuestion: {question}"
                    }
                ],
            }
        ],
    }

    if previous_response_id:
        kwargs["previous_response_id"] = previous_response_id

    response = client.responses.create(**kwargs)

    return {
        "answer": response.output_text,
        "response_id": response.id,
    }
