# app/finance_agent.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd


# ----------------------------
# Utilities
# ----------------------------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())

def _safe_str(x: Any) -> str:
    if x is None:
        return ""
    s = str(x)
    if s.lower() in {"nan", "nat", "none"}:
        return ""
    return s.strip()

def _maybe_to_datetime(s: pd.Series) -> pd.Series:
    # tolerate mixed formats
    return pd.to_datetime(s, errors="coerce", infer_datetime_format=True)

def _maybe_to_numeric(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")

def _drop_all_blank_rows(df: pd.DataFrame) -> pd.DataFrame:
    tmp = df.copy()
    for c in tmp.columns:
        tmp[c] = tmp[c].map(_safe_str)
    blank = tmp.apply(lambda r: all(_safe_str(v) == "" for v in r.values), axis=1)
    return df.loc[~blank].copy()

def _to_df(tab: Dict[str, Any]) -> pd.DataFrame:
    header = [str(h).strip() for h in tab.get("header", [])]
    rows = tab.get("rows", [])
    data: List[List[Any]] = []
    row_ids: List[int] = []

    for r in rows:
        row_ids.append(int(r.get("row", 0)))
        vals = r.get("values", [])
        data.append(vals[: len(header)])

    df = pd.DataFrame(data, columns=header)
    df.insert(0, "__row", row_ids)  # audit row index in Google Sheet
    df.columns = [c.strip().lower() for c in df.columns]
    df = _drop_all_blank_rows(df)
    return df

def _find_col(df: pd.DataFrame, *cands: str) -> Optional[str]:
    cols = list(df.columns)
    cmap = {_norm(c): c for c in cols}
    for cand in cands:
        k = _norm(cand)
        if k in cmap:
            return cmap[k]
    return None

def _has_cols(df: pd.DataFrame, cols_any: List[str]) -> bool:
    existing = set(df.columns)
    for c in cols_any:
        if _norm(c) in {_norm(x) for x in existing}:
            return True
    return False


# ----------------------------
# Transfer Status parser (IN/OUT blocks)
# Your Transfer Status sheet contains two tables side-by-side with headers in row 1.
# We convert it into two normalized dataframes: transfer_in, transfer_out
# ----------------------------

def _parse_transfer_status(df_raw: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """
    Accepts df produced from _to_df(tab) where columns are 'in', 'unnamed: 1', etc.
    The first row contains headers for IN block and OUT block.
    """
    # We need the first row (header row) as actual headers
    if df_raw.empty:
        return {}

    # Remove audit col for processing, keep __row to reattach later
    df = df_raw.copy()

    # The first data row in Transfer Status often contains headers like:
    # IN side: Sl, Plant ID, Period Starts, Period Ends, Vintage, MWh, Total Cost, Cost per MWh
    # OUT side: Sl, Plant ID, Period Starts, Period Ends, Vintage, MWh, Cost/MWh, Plant Owner Cost (USD)
    # We detect this by checking row 0 (after dropping blanks)
    header_row = df.iloc[0].to_dict()

    # Split columns into left and right blocks by finding the 'out' marker column (often named "out" or "sl" after unnamed gap)
    # In your sample, columns were: 'in', 'unnamed: 1'...'unnamed: 8', 'out', 'unnamed:10'...
    cols = list(df.columns)
    # locate column named 'out' (normalized)
    out_col_idx = None
    for i, c in enumerate(cols):
        if _norm(c) == "out":
            out_col_idx = i
            break
    if out_col_idx is None:
        # If can't detect, return raw as fallback
        return {"transfer": df_raw}

    left_cols = cols[:out_col_idx]          # includes 'in'
    right_cols = cols[out_col_idx:]         # includes 'out'

    def build_block(block_cols: List[str], block_name: str) -> pd.DataFrame:
        block = df[block_cols].copy()
        # Rename using header_row values (first row)
        new_cols = []
        for c in block_cols:
            hv = _safe_str(header_row.get(c, ""))
            new_cols.append(hv if hv else c)
        block.columns = [str(x).strip() for x in new_cols]
        # Drop the header row itself
        block = block.iloc[1:].copy()
        # Bring back audit row numbers
        block.insert(0, "__row", df["__row"].iloc[1:].astype(int).tolist())
        # Normalize columns
        block.columns = [str(c).strip().lower() for c in block.columns]
        block = _drop_all_blank_rows(block)

        # Normalize expected column names
        rename_map = {
            "sl": "sl",
            "plant id": "device id",
            "period starts": "start date",
            "period ends": "end date",
            "mwh": "mwh",
            "total cost": "total cost",
            "cost per mwh": "cost per mwh",
            "cost/mwh": "cost per mwh",
            "plant owner cost (usd)": "plant owner cost (usd)",
            "vintage": "vintage",
        }
        for k, v in rename_map.items():
            if k in block.columns and v not in block.columns:
                block.rename(columns={k: v}, inplace=True)

        # Type conversions
        for dc in ["start date", "end date"]:
            if dc in block.columns:
                block[dc] = _maybe_to_datetime(block[dc])
        for nc in ["mwh", "total cost", "cost per mwh", "plant owner cost (usd)"]:
            if nc in block.columns:
                block[nc] = _maybe_to_numeric(block[nc])

        block["__tab"] = block_name
        return block

    transfer_in = build_block(left_cols, "Transfer Status (IN)")
    transfer_out = build_block(right_cols, "Transfer Status (OUT)")
    return {"transfer_in": transfer_in, "transfer_out": transfer_out}


# ----------------------------
# Query parsing
# ----------------------------

@dataclass
class Query:
    tab_hint: Optional[str] = None
    metric: Optional[str] = None
    dims: List[str] = None
    op: str = "sum"  # sum, avg, count
    top_n: Optional[int] = None
    filters: List[Tuple[str, str, str]] = None  # (field, operator, value)
    date_from: Optional[pd.Timestamp] = None
    date_to: Optional[pd.Timestamp] = None

    def __post_init__(self):
        self.dims = self.dims or []
        self.filters = self.filters or []


_METRIC_SYNONYMS = {
    "number of certificate": ["number of certificate", "number of certificates", "certificates", "irec", "issued irec", "sold irec"],
    "mwh": ["mwh", "energy", "generation"],
    "total cost": ["total cost", "issuance cost", "redemption cost", "cost"],
    "cost per mwh": ["cost per mwh", "cost/mwh", "cost of mwh"],
    "total (bdt)": ["total (bdt)", "revenue", "sales", "total bdt"],
    "sales rate (usd)": ["sales rate (usd)", "price usd", "selling price"],
    "plant owner cost (usd)": ["plant owner cost (usd)", "owner cost", "cost usd"],
}

_DIM_SYNONYMS = {
    "client name": ["client name", "client"],
    "device id": ["device id", "plant id"],
    "device name": ["device name", "device"],
    "vendor name": ["vendor name", "vendor"],
    "vintage": ["vintage", "quarter"],
    "date": ["date"],
    "start date": ["start date", "production starts", "period starts"],
    "end date": ["end date", "production ends", "period ends"],
}

_KEYWORDS_SUM = ["total", "sum"]
_KEYWORDS_AVG = ["average", "avg", "mean"]
_KEYWORDS_COUNT = ["count", "how many", "number of rows"]
_KEYWORDS_TOP = ["top", "highest", "largest", "most"]

def _extract_tab_hint(q: str) -> Optional[str]:
    # allow: tab: Redemption Status
    m = re.search(r"\btab\s*:\s*([A-Za-z0-9 _\-/]+)", q, flags=re.I)
    return m.group(1).strip() if m else None

def _extract_top_n(q: str) -> Optional[int]:
    m = re.search(r"\btop\s+(\d+)\b", q, flags=re.I)
    return int(m.group(1)) if m else None

def _extract_op(q: str) -> str:
    ql = q.lower()
    if any(k in ql for k in _KEYWORDS_AVG):
        return "avg"
    if any(k in ql for k in _KEYWORDS_COUNT):
        return "count"
    return "sum"

def _extract_metric(q: str) -> Optional[str]:
    ql = q.lower()
    for canon, syns in _METRIC_SYNONYMS.items():
        for s in syns:
            if s in ql:
                return canon
    # fallback: try patterns like "total <col>"
    m = re.search(r"\b(total|sum|avg|average|count)\s+([a-zA-Z0-9 _/%\-\(\)]+)", q, flags=re.I)
    if m:
        return _norm(m.group(2))
    return None

def _extract_dims(q: str) -> List[str]:
    # "by <dim>" possibly multiple: "by client name and vintage"
    m = re.search(r"\bby\s+(.+)$", q, flags=re.I)
    if not m:
        return []
    tail = m.group(1)
    # stop if contains "where" like clause
    tail = re.split(r"\b(where|filter|for)\b", tail, flags=re.I)[0]
    parts = re.split(r",| and ", tail, flags=re.I)
    dims = []
    for p in parts:
        pp = _norm(p)
        if not pp:
            continue
        # map synonyms
        for canon, syns in _DIM_SYNONYMS.items():
            if any(_norm(s) == pp or pp in _norm(s) or _norm(s) in pp for s in syns):
                dims.append(canon)
                break
        else:
            dims.append(pp)
    # unique preserve order
    out = []
    for d in dims:
        if d not in out:
            out.append(d)
    return out

def _extract_date_range(q: str) -> Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
    # supports: between 2024-01-01 and 2024-12-31
    m = re.search(r"\bbetween\s+([0-9/\-]+)\s+and\s+([0-9/\-]+)\b", q, flags=re.I)
    if m:
        d1 = pd.to_datetime(m.group(1), errors="coerce")
        d2 = pd.to_datetime(m.group(2), errors="coerce")
        return (d1 if pd.notna(d1) else None, d2 if pd.notna(d2) else None)
    # supports: from X to Y
    m = re.search(r"\bfrom\s+([0-9/\-]+)\s+to\s+([0-9/\-]+)\b", q, flags=re.I)
    if m:
        d1 = pd.to_datetime(m.group(1), errors="coerce")
        d2 = pd.to_datetime(m.group(2), errors="coerce")
        return (d1 if pd.notna(d1) else None, d2 if pd.notna(d2) else None)
    return (None, None)

def _extract_filters(q: str) -> List[Tuple[str, str, str]]:
    """
    Supports:
      client name = ABM
      cost per mwh > 3.5
      vintage = V24Q1
      device id contains 1.7
    """
    filters: List[Tuple[str, str, str]] = []

    # explicit contains
    for m in re.finditer(r"([A-Za-z0-9 _/%]+)\s+contains\s+'?([^']+)'?", q, flags=re.I):
        filters.append((m.group(1).strip(), "contains", m.group(2).strip()))

    # comparators
    for m in re.finditer(r"([A-Za-z0-9 _/%\.\-]+)\s*(=|!=|>=|<=|>|<)\s*'?(.*?)'?(?:\s|$)", q, flags=re.I):
        field = m.group(1).strip()
        op = m.group(2)
        val = m.group(3).strip()
        # avoid catching "tab:" or "top 5"
        if _norm(field) in {"tab", "top"}:
            continue
        # avoid empty
        if not field or not val:
            continue
        filters.append((field, op, val))

    # also allow: "for ABM FASHIONS" -> interpret as client name contains
    m = re.search(r"\bfor\s+(.+)$", q, flags=re.I)
    if m:
        val = m.group(1).strip()
        if val and len(val) >= 3:
            filters.append(("client name", "contains", val))

    # de-dup
    out = []
    for f in filters:
        if f not in out:
            out.append(f)
    return out

def parse_question(question: str) -> Query:
    q = question.strip()
    tab_hint = _extract_tab_hint(q)
    top_n = _extract_top_n(q)
    op = _extract_op(q)
    metric = _extract_metric(q)
    dims = _extract_dims(q)
    d1, d2 = _extract_date_range(q)
    filters = _extract_filters(q)
    return Query(
        tab_hint=tab_hint,
        metric=metric,
        dims=dims,
        op=op,
        top_n=top_n,
        filters=filters,
        date_from=d1,
        date_to=d2,
    )


# ----------------------------
# Tab selection & execution
# ----------------------------

def _pick_metric_col(df: pd.DataFrame, metric_canon: str) -> Optional[str]:
    # Map canonical metric name to likely actual columns
    syns = _METRIC_SYNONYMS.get(metric_canon, [metric_canon])
    # direct match
    for s in syns:
        col = _find_col(df, s)
        if col:
            return col
    # heuristic fallback: find any col that includes tokens
    mc = _norm(metric_canon)
    for c in df.columns:
        if mc in _norm(c):
            return c
    return None

def _pick_dim_col(df: pd.DataFrame, dim_canon: str) -> Optional[str]:
    syns = _DIM_SYNONYMS.get(dim_canon, [dim_canon])
    for s in syns:
        col = _find_col(df, s)
        if col:
            return col
    dc = _norm(dim_canon)
    for c in df.columns:
        if dc in _norm(c):
            return c
    return None

def _apply_filters(df: pd.DataFrame, filters: List[Tuple[str, str, str]]) -> pd.DataFrame:
    out = df.copy()
    for field, op, value in filters:
        col = _pick_dim_col(out, field) or _find_col(out, field)
        if not col:
            # can't apply this filter
            continue

        if op == "contains":
            out = out[out[col].astype(str).str.contains(value, case=False, na=False)]
            continue

        # numeric compare if possible
        s = out[col]
        s_num = _maybe_to_numeric(s)
        is_num = s_num.notna().any()

        if is_num and re.fullmatch(r"[+-]?\d+(\.\d+)?", value):
            v = float(value)
            if op == "=":
                out = out[s_num == v]
            elif op == "!=":
                out = out[s_num != v]
            elif op == ">":
                out = out[s_num > v]
            elif op == "<":
                out = out[s_num < v]
            elif op == ">=":
                out = out[s_num >= v]
            elif op == "<=":
                out = out[s_num <= v]
            continue

        # string compare
        sv = out[col].astype(str)
        if op == "=":
            out = out[sv.str.strip().str.lower() == value.strip().lower()]
        elif op == "!=":
            out = out[sv.str.strip().str.lower() != value.strip().lower()]
        else:
            # unsupported string compare
            pass
    return out

def _apply_date_range(df: pd.DataFrame, q: Query) -> pd.DataFrame:
    if q.date_from is None and q.date_to is None:
        return df

    out = df.copy()

    # choose best date column
    date_col = _find_col(out, "date") or _find_col(out, "start date") or _find_col(out, "production starts") or _find_col(out, "period starts")
    if not date_col:
        return out

    dt = _maybe_to_datetime(out[date_col])
    mask = pd.Series(True, index=out.index)
    if q.date_from is not None and pd.notna(q.date_from):
        mask &= dt >= q.date_from
    if q.date_to is not None and pd.notna(q.date_to):
        mask &= dt <= q.date_to
    return out.loc[mask].copy()

def _tab_score_for_query(tab_name: str, df: pd.DataFrame, q: Query) -> int:
    score = 0

    # tab hint boost
    if q.tab_hint and _norm(q.tab_hint) in _norm(tab_name):
        score += 50

    # metric boost
    if q.metric:
        mc = _pick_metric_col(df, q.metric)
        if mc:
            score += 20

    # dims boost
    for d in q.dims:
        if _pick_dim_col(df, d):
            score += 5

    # common finance tabs
    tn = _norm(tab_name)
    if "exchange" in tn and q.metric in {"total (bdt)", "sales rate (usd)"}:
        score += 10
    if "cogs" in tn and q.metric in {"total cost", "issuance cost", "redemption cost"}:
        score += 10
    if "redemption status" in tn and q.metric in {"number of certificate", "cost per mwh"}:
        score += 10
    if "issuance status" in tn and q.metric == "mwh":
        score += 10

    return score

def _compute(df: pd.DataFrame, q: Query, tab_name: str) -> Dict[str, Any]:
    df0 = df.copy()
    df0["__tab"] = tab_name

    # Apply date range first, then other filters
    df1 = _apply_date_range(df0, q)
    df2 = _apply_filters(df1, q.filters)

    # Dimensions
    dim_cols: List[str] = []
    for d in q.dims:
        c = _pick_dim_col(df2, d)
        if c:
            dim_cols.append(c)

    # Metric
    metric_col = None
    if q.op != "count":
        metric_col = _pick_metric_col(df2, q.metric or "number of certificate")
        if not metric_col:
            # fallback: try any numeric col if metric missing
            for c in df2.columns:
                if c in {"__row", "__tab"}:
                    continue
                if _maybe_to_numeric(df2[c]).notna().any():
                    metric_col = c
                    break

    # Compute
    if q.op == "count":
        # count rows, or count distinct dim if asked
        if dim_cols:
            res = df2.groupby(dim_cols, dropna=False).size().reset_index(name="count")
        else:
            res = pd.DataFrame({"count": [int(df2.shape[0])]})
        result_metric_name = "count"
    else:
        x = _maybe_to_numeric(df2[metric_col]).fillna(0.0)
        if dim_cols:
            if q.op == "avg":
                res = df2.assign(__x=x).groupby(dim_cols, dropna=False)["__x"].mean().reset_index(name=f"avg_{metric_col}")
                result_metric_name = f"avg_{metric_col}"
            else:
                res = df2.assign(__x=x).groupby(dim_cols, dropna=False)["__x"].sum().reset_index(name=f"sum_{metric_col}")
                result_metric_name = f"sum_{metric_col}"
        else:
            if q.op == "avg":
                res = pd.DataFrame({f"avg_{metric_col}": [float(x.mean())]})
                result_metric_name = f"avg_{metric_col}"
            else:
                res = pd.DataFrame({f"sum_{metric_col}": [float(x.sum())]})
                result_metric_name = f"sum_{metric_col}"

    # Top N
    if q.top_n and dim_cols and result_metric_name in res.columns:
        res = res.sort_values(result_metric_name, ascending=False).head(q.top_n)

    # Audit rows: show first 40 contributing rows from df2
    audit = [{"tab": tab_name, "row": int(r)} for r in df2["__row"].head(40).tolist() if pd.notna(r)]

    # Preview
    table_preview = res.head(12).to_string(index=False)

    return {
        "answer": _format_answer(tab_name, q, res, result_metric_name, dim_cols),
        "table_preview": table_preview,
        "audit": audit,
        "meta": {
            "tab_used": tab_name,
            "op": q.op,
            "metric": q.metric,
            "metric_col": metric_col,
            "dims": dim_cols,
            "filtered_rows": int(df2.shape[0]),
            "top_n": q.top_n,
        }
    }

def _format_answer(tab_name: str, q: Query, res: pd.DataFrame, metric_name: str, dim_cols: List[str]) -> str:
    # Human-readable answer
    base = f"Computed from tab '{tab_name}'."
    if not dim_cols and metric_name in res.columns and len(res) == 1:
        v = res.iloc[0][metric_name]
        return f"{base} {metric_name} = {v:.6g}" if isinstance(v, (int, float)) else f"{base} {metric_name} = {v}"
    if dim_cols:
        return f"{base} Showing {metric_name} by {', '.join(dim_cols)}."
    return f"{base} Result computed."

# ----------------------------
# Public API: answer_finance
# ----------------------------

def answer_finance(question: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    q = parse_question(question)

    tabs = snapshot.get("tabs", {})
    if not isinstance(tabs, dict) or not tabs:
        return {"answer": "No tabs provided in snapshot."}

    # Build dfs from all tabs
    dfs: Dict[str, pd.DataFrame] = {}
    for name, tab in tabs.items():
        try:
            df = _to_df(tab)
            # Special-case Transfer Status
            if _norm(name) == "transfer status":
                parsed = _parse_transfer_status(df)
                for k, v in parsed.items():
                    dfs[k] = v
            else:
                dfs[name] = df
        except Exception:
            continue

    # Quick guard: if question is not compute-like
    ql = question.lower()
    compute_words = ["total", "sum", "avg", "average", "count", "top", "by", "group", "compare", "trend"]
    if not any(w in ql for w in compute_words):
        return {
            "answer": "Ask a compute-style finance question.\nExamples:\n"
                      "- total number of certificate by client name\n"
                      "- top 5 client name by number of certificate\n"
                      "- sum mwh by vintage between 2024-01-01 and 2024-12-31\n"
                      "- total (bdt) by client name tab: Exchange"
        }

    # Score tabs for this query and pick best
    scored: List[Tuple[int, str]] = []
    for tname, df in dfs.items():
        scored.append((_tab_score_for_query(tname, df, q), tname))
    scored.sort(reverse=True)

    # Try best few tabs until one works
    last_err = None
    for score, tname in scored[:6]:
        df = dfs[tname]
        try:
            # Ensure metric exists when needed
            if q.op != "count" and q.metric:
                mc = _pick_metric_col(df, q.metric)
                if not mc:
                    # skip tab that doesn't have metric
                    continue
            return _compute(df, q, tname)
        except Exception as e:
            last_err = e
            continue

    # If nothing matched, provide guidance
    msg = "Could not match your question to a suitable tab/metric.\n"
    if q.metric:
        msg += f"- Metric requested: {q.metric}\n"
    if q.dims:
        msg += f"- Grouping: {q.dims}\n"
    if q.tab_hint:
        msg += f"- Tab hint: {q.tab_hint}\n"
    if last_err:
        msg += f"- Last error: {last_err}\n"
    msg += "\nTip: Add 'tab: <Tab Name>' to force a tab, e.g. 'tab: Exchange total (bdt) by client name'."
    return {"answer": msg}
