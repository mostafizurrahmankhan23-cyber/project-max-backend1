# app/finance_agent.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


# ============================================================
# Small utilities
# ============================================================

def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _safe_str(x: Any) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    if s.lower() in {"nan", "nat", "none"}:
        return ""
    return s


def _to_num(x: Any) -> float:
    s = _safe_str(x).replace(",", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def _to_num_or_none(x: Any) -> Optional[float]:
    s = _safe_str(x).replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _first_existing(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    cmap = {_norm(c): c for c in df.columns}
    for cand in candidates:
        c = cmap.get(_norm(cand))
        if c:
            return c
    return None


def _normalize_id(x: Any) -> str:
    s = _safe_str(x).upper()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^A-Z0-9\.]", "", s)
    return s


def _normalize_name(x: Any) -> str:
    s = _safe_str(x).lower()
    s = re.sub(r"[^a-z0-9\s\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _normalize_vintage(x: Any) -> str:
    s = _safe_str(x).upper().replace(" ", "")
    m = re.search(r"(V\d{2}Q[1-4])", s)
    return m.group(1) if m else ""


def _extract_expected_mwh_from_filename(source_pdf: Any) -> Optional[float]:
    s = _safe_str(source_pdf)
    if not s:
        return None
    m = re.search(r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)\s*MWH", s, flags=re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except Exception:
        return None


def _add_issue(
    issues: List[Dict[str, Any]],
    *,
    issue_type: str,
    tab: str,
    row: Optional[int] = None,
    priority: str = "Medium",
    source_pdf: str = "",
    client_name: str = "",
    device_id: str = "",
    device_name: str = "",
    vintage: str = "",
    summary: str = "",
    likely_cause: str = "",
    suggested_fix: str = "",
) -> None:
    issues.append({
        "Issue Type": issue_type,
        "Tab": tab,
        "Row No": row if row is not None else "",
        "Source PDF": source_pdf,
        "Client Name": client_name,
        "Device ID": device_id,
        "Device Name": device_name,
        "Vintage": vintage,
        "Issue Summary": summary,
        "Likely Cause": likely_cause,
        "Suggested Fix": suggested_fix,
        "Priority": priority,
    })


# ============================================================
# Snapshot -> DataFrame
# ============================================================

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
        vals = list(r.get("values", []))
        vals = vals[: len(header)]
        while len(vals) < len(header):
            vals.append("")
        data.append(vals)

    if not header:
        return pd.DataFrame()

    df = pd.DataFrame(data, columns=header)
    df.insert(0, "__row", row_ids)
    df.columns = [str(c).strip() for c in df.columns]
    return _drop_all_blank_rows(df)


def _build_tables(snapshot: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
    tabs = snapshot.get("tabs", {})
    out: Dict[str, pd.DataFrame] = {}
    for name, tab in tabs.items():
        try:
            out[name] = _to_df(tab)
        except Exception:
            continue
    return out


# ============================================================
# Transfer Status parser
# ============================================================

def _parse_transfer_status(df_raw: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    if df_raw.empty:
        return {}

    df = df_raw.copy()
    cols = list(df.columns)

    # identify "OUT" split column
    out_idx = None
    for i, c in enumerate(cols):
        if _norm(c) == "out":
            out_idx = i
            break

    if out_idx is None or len(df) < 1:
        return {"Transfer Status": df_raw}

    header_row = df.iloc[0].to_dict()

    def build_block(block_cols: List[str], tab_name: str) -> pd.DataFrame:
        block = df[block_cols].copy()

        new_cols = []
        for c in block_cols:
            hv = _safe_str(header_row.get(c, ""))
            new_cols.append(hv if hv else c)

        block.columns = [str(c).strip() for c in new_cols]
        block = block.iloc[1:].copy()
        block.insert(0, "__row", df["__row"].iloc[1:].astype(int).tolist())
        block = _drop_all_blank_rows(block)

        # normalize columns
        rename_map = {
            "Plant ID": "Plant ID",
            "Period Starts": "Period Starts",
            "Period Ends": "Period Ends",
            "Vintage": "Vintage",
            "MWh": "MWh",
            "Total Cost": "Total Cost",
            "Cost per MWh": "Cost per MWh",
            "Cost/MWh": "Cost per MWh",
            "Plant Owner Cost (USD)": "Plant Owner Cost (USD)",
        }

        cols2 = list(block.columns)
        for old in cols2:
            for k, v in rename_map.items():
                if _norm(old) == _norm(k):
                    block.rename(columns={old: v}, inplace=True)

        return block

    left_cols = cols[:out_idx]
    right_cols = cols[out_idx:]

    return {
        "Transfer Status (IN)": build_block(left_cols, "Transfer Status (IN)"),
        "Transfer Status (OUT)": build_block(right_cols, "Transfer Status (OUT)"),
    }


# ============================================================
# Registry lookup
# ============================================================

@dataclass
class RegistryLookup:
    ids: set[str]
    id_to_name: Dict[str, str]
    name_to_id: Dict[str, str]


def _build_registry_lookup(tables: Dict[str, pd.DataFrame]) -> RegistryLookup:
    ids: set[str] = set()
    id_to_name: Dict[str, str] = {}
    name_to_id: Dict[str, str] = {}

    for tab_name in ["Registration Data-GRIT", "Registration Data-Delnotic"]:
        df = tables.get(tab_name)
        if df is None or df.empty:
            continue

        col_id = _first_existing(df, ["Device ID", "Plant ID", "Plant Id"])
        col_name = _first_existing(df, ["Name", "Device Name", "Plant Name", "Project Name"])
        if not col_id:
            continue

        for _, row in df.iterrows():
            did = _normalize_id(row.get(col_id, ""))
            if not did:
                continue
            ids.add(did)

            if col_name:
                nm = _safe_str(row.get(col_name, ""))
                if did not in id_to_name and nm:
                    id_to_name[did] = nm
                nn = _normalize_name(nm)
                if nn and nn not in name_to_id:
                    name_to_id[nn] = did

    return RegistryLookup(ids=ids, id_to_name=id_to_name, name_to_id=name_to_id)


# ============================================================
# Compute-mode support (keep your old capability)
# ============================================================

@dataclass
class Query:
    tab_hint: Optional[str] = None
    metric: Optional[str] = None
    dims: List[str] = None
    op: str = "sum"
    top_n: Optional[int] = None

    def __post_init__(self):
        self.dims = self.dims or []


_METRIC_SYNONYMS = {
    "number of certificate": ["number of certificate", "number of certificates", "certificates", "irec"],
    "mwh": ["mwh", "energy"],
    "total cost": ["total cost", "plant owner cost (bdt)", "redemption cost", "issuance cost"],
    "cost per mwh": ["cost per mwh", "cost/mwh", "cost of mwh"],
    "total (bdt)": ["total (bdt)", "total amount (bdt)", "sales", "revenue"],
}

_DIM_SYNONYMS = {
    "client name": ["client name", "client"],
    "device id": ["device id", "plant id"],
    "device name": ["device name", "device"],
    "vintage": ["vintage", "quarter"],
    "date": ["date"],
}


def _parse_compute_question(question: str) -> Query:
    ql = question.lower()

    op = "sum"
    if any(k in ql for k in ["average", "avg", "mean"]):
        op = "avg"
    elif any(k in ql for k in ["count", "how many"]):
        op = "count"

    top_n = None
    m_top = re.search(r"\btop\s+(\d+)\b", question, flags=re.I)
    if m_top:
        top_n = int(m_top.group(1))

    tab_hint = None
    m_tab = re.search(r"\btab\s*:\s*([A-Za-z0-9 _\-/\(\)]+)", question, flags=re.I)
    if m_tab:
        tab_hint = m_tab.group(1).strip()

    metric = None
    for canon, syns in _METRIC_SYNONYMS.items():
        if any(s in ql for s in syns):
            metric = canon
            break

    dims: List[str] = []
    m_by = re.search(r"\bby\s+(.+)$", question, flags=re.I)
    if m_by:
        tail = re.split(r"\b(where|for)\b", m_by.group(1), flags=re.I)[0]
        parts = re.split(r",| and ", tail, flags=re.I)
        for p in parts:
            pp = _norm(p)
            if not pp:
                continue
            for canon, syns in _DIM_SYNONYMS.items():
                if any(pp == _norm(s) or pp in _norm(s) or _norm(s) in pp for s in syns):
                    dims.append(canon)
                    break

    return Query(tab_hint=tab_hint, metric=metric, dims=dims, op=op, top_n=top_n)


def _pick_metric_col(df: pd.DataFrame, metric: str) -> Optional[str]:
    syns = _METRIC_SYNONYMS.get(metric, [metric])
    cmap = {_norm(c): c for c in df.columns}
    for s in syns:
        if _norm(s) in cmap:
            return cmap[_norm(s)]
    for c in df.columns:
        if metric and _norm(metric) in _norm(c):
            return c
    return None


def _pick_dim_col(df: pd.DataFrame, dim: str) -> Optional[str]:
    syns = _DIM_SYNONYMS.get(dim, [dim])
    cmap = {_norm(c): c for c in df.columns}
    for s in syns:
        if _norm(s) in cmap:
            return cmap[_norm(s)]
    return None


def _tab_score_for_compute(tab_name: str, df: pd.DataFrame, q: Query) -> int:
    score = 0
    if q.tab_hint and _norm(q.tab_hint) in _norm(tab_name):
        score += 50
    if q.metric and _pick_metric_col(df, q.metric):
        score += 20
    for d in q.dims:
        if _pick_dim_col(df, d):
            score += 5
    return score


def _run_compute(question: str, tables: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    q = _parse_compute_question(question)

    scored: List[Tuple[int, str]] = []
    for name, df in tables.items():
        if df.empty:
            continue
        scored.append((_tab_score_for_compute(name, df, q), name))
    scored.sort(reverse=True)

    for _, name in scored[:8]:
        df = tables[name].copy()
        if df.empty:
            continue

        if q.op == "count":
            if q.dims:
                dim_cols = [_pick_dim_col(df, d) for d in q.dims]
                dim_cols = [c for c in dim_cols if c]
                if not dim_cols:
                    continue
                res = df.groupby(dim_cols, dropna=False).size().reset_index(name="count")
            else:
                res = pd.DataFrame({"count": [int(df.shape[0])]})
            preview = res.head(12).to_string(index=False)
            return {
                "mode": "compute",
                "answer": f"Computed from tab '{name}'.",
                "table_preview": preview,
                "audit": [{"tab": name, "row": int(r)} for r in df["__row"].head(40).tolist()],
                "meta": {"tab_used": name, "op": q.op},
            }

        metric_col = _pick_metric_col(df, q.metric or "")
        if not metric_col:
            continue

        x = pd.to_numeric(df[metric_col], errors="coerce").fillna(0.0)

        dim_cols = [_pick_dim_col(df, d) for d in q.dims]
        dim_cols = [c for c in dim_cols if c]

        if dim_cols:
            if q.op == "avg":
                res = df.assign(__x=x).groupby(dim_cols, dropna=False)["__x"].mean().reset_index(name=f"avg_{metric_col}")
            else:
                res = df.assign(__x=x).groupby(dim_cols, dropna=False)["__x"].sum().reset_index(name=f"sum_{metric_col}")
            if q.top_n:
                metric_name = res.columns[-1]
                res = res.sort_values(metric_name, ascending=False).head(q.top_n)
        else:
            if q.op == "avg":
                res = pd.DataFrame({f"avg_{metric_col}": [float(x.mean())]})
            else:
                res = pd.DataFrame({f"sum_{metric_col}": [float(x.sum())]})

        preview = res.head(12).to_string(index=False)
        return {
            "mode": "compute",
            "answer": f"Computed from tab '{name}'.",
            "table_preview": preview,
            "audit": [{"tab": name, "row": int(r)} for r in df["__row"].head(40).tolist()],
            "meta": {"tab_used": name, "op": q.op, "metric_col": metric_col},
        }

    return {
        "mode": "compute",
        "answer": "Could not match your compute query to a suitable tab/metric.",
        "table_preview": "",
        "audit": [],
        "meta": {},
    }


# ============================================================
# Diagnostic-mode routing
# ============================================================

def _detect_intent(question: str) -> str:
    ql = question.lower()

    diag_words = [
        "problem", "issue", "wrong", "missing", "mismatch", "flow", "not updated",
        "why blank", "why is", "diagnose", "reconcile", "reconciliation",
        "sold > issued", "sold greater than issued", "helper tab", "fix"
    ]
    compute_words = ["total", "sum", "avg", "average", "count", "top", "by", "trend"]

    if any(w in ql for w in diag_words):
        return "diagnostic"
    if any(w in ql for w in compute_words):
        return "compute"
    return "diagnostic"


def _detect_tab(question: str) -> Optional[str]:
    ql = question.lower()
    if "redemption" in ql:
        return "Redemption Status"
    if "exchange" in ql:
        return "Exchange"
    if "transfer" in ql:
        return "Transfer Status"
    if "device wise sales" in ql:
        return "Device Wise Sales Status"
    if "issuance status" in ql:
        return "Issuance Status"
    return None


# ============================================================
# Diagnostics: Redemption Status
# ============================================================

def _diag_redemption(tables: Dict[str, pd.DataFrame], registry: RegistryLookup) -> Dict[str, Any]:
    df = tables.get("Redemption Status")
    issues: List[Dict[str, Any]] = []

    if df is None or df.empty:
        return {
            "mode": "diagnostic",
            "answer": "Redemption Status is empty or missing.",
            "issues": [],
            "helper_tab": {"name": "AI Exceptions - Redemption Status", "rows": []},
            "meta": {"tab_used": "Redemption Status"},
        }

    c_pdf = _first_existing(df, ["Source PDF"])
    c_client = _first_existing(df, ["Client Name"])
    c_did = _first_existing(df, ["Device ID", "Plant ID"])
    c_dname = _first_existing(df, ["Device Name", "Plant Name"])
    c_vintage = _first_existing(df, ["Vintage"])
    c_cert = _first_existing(df, ["Number of Certificate", "Number of Certificates"])
    c_cost = _first_existing(df, ["Cost of MWh"])
    c_rate_usd = _first_existing(df, ["Rate (USD)", "Sales Rate (USD)"])
    c_rate_bdt = _first_existing(df, ["Rate (BDT)", "Sales Rate / Exchange Rate (BDT)"])
    c_total_cost = _first_existing(df, ["Total Plant Owner Cost (BDT)", "Plant Owner Cost (BDT)"])
    c_total_amt = _first_existing(df, ["Total Amount (BDT)", "Total (BDT)"])

    # 1) PDF total mismatch at Source PDF level
    if c_pdf and c_cert:
        grp = (
            df.assign(__cert=df[c_cert].map(_to_num))
              .groupby(c_pdf, dropna=False)["__cert"].sum()
              .reset_index()
        )
        for _, row in grp.iterrows():
            pdf = _safe_str(row[c_pdf])
            if not pdf:
                continue
            expected = _extract_expected_mwh_from_filename(pdf)
            if expected is None:
                continue
            actual = float(row["__cert"])
            diff = round(actual - expected, 6)
            if abs(diff) > 1e-9:
                _add_issue(
                    issues,
                    issue_type="PDF_TOTAL_MISMATCH",
                    tab="Redemption Status",
                    priority="High",
                    source_pdf=pdf,
                    summary=f"Expected {expected:g} MWh from filename, but summed Number of Certificate = {actual:g}.",
                    likely_cause="Missing, duplicated, or mis-entered allocation rows under the same Source PDF.",
                    suggested_fix="Compare filename MWh total with summed Number of Certificate for this Source PDF and locate missing, duplicated, or mis-entered sale lines.",
                )

    # 2) Missing / invalid Device ID
    for _, row in df.iterrows():
        row_no = int(row["__row"]) if "__row" in row else None
        pdf = _safe_str(row.get(c_pdf, "")) if c_pdf else ""
        client = _safe_str(row.get(c_client, "")) if c_client else ""
        did_raw = _safe_str(row.get(c_did, "")) if c_did else ""
        did = _normalize_id(did_raw)
        dname = _safe_str(row.get(c_dname, "")) if c_dname else ""
        vintage = _safe_str(row.get(c_vintage, "")) if c_vintage else ""

        if c_did and not did:
            _add_issue(
                issues,
                issue_type="MISSING_DEVICE_ID",
                tab="Redemption Status",
                row=row_no,
                priority="High",
                source_pdf=pdf,
                client_name=client,
                device_name=dname,
                vintage=vintage,
                summary="Device ID is blank.",
                likely_cause="Device mapping from plant name failed.",
                suggested_fix="Match Device Name against Registration Data-GRIT first, then Registration Data-Delnotic. If still unmatched, review plant naming or add the missing plant in registration source.",
            )
        elif c_did and did and did not in registry.ids:
            _add_issue(
                issues,
                issue_type="INVALID_DEVICE_ID",
                tab="Redemption Status",
                row=row_no,
                priority="High",
                source_pdf=pdf,
                client_name=client,
                device_id=did_raw,
                device_name=dname,
                vintage=vintage,
                summary="Device ID does not exist in registration source.",
                likely_cause="Typo, outdated ID, or wrong plant mapping.",
                suggested_fix="Validate Device ID in Registration Data-GRIT first, then Registration Data-Delnotic. If absent in both, remap from plant identity.",
            )

        # Device Name mismatch only when ID exists in registry
        if did and did in registry.id_to_name and dname:
            reg_name = _safe_str(registry.id_to_name.get(did, ""))
            if reg_name and _normalize_name(reg_name) != _normalize_name(dname):
                _add_issue(
                    issues,
                    issue_type="DEVICE_NAME_MISMATCH",
                    tab="Redemption Status",
                    row=row_no,
                    priority="Medium",
                    source_pdf=pdf,
                    client_name=client,
                    device_id=did_raw,
                    device_name=dname,
                    vintage=vintage,
                    summary=f"Device Name does not match registration source name '{reg_name}'.",
                    likely_cause="Wrong or blank Device ID previously mapped, or stale device name text.",
                    suggested_fix="Fix the Device ID first. Then align Device Name to the registered plant name from GRIT/Delnotic.",
                )

        # Missing cost of MWh
        if c_cost and _safe_str(row.get(c_cost, "")) == "":
            _add_issue(
                issues,
                issue_type="MISSING_COST_OF_MWH",
                tab="Redemption Status",
                row=row_no,
                priority="Medium",
                source_pdf=pdf,
                client_name=client,
                device_id=did_raw,
                device_name=dname,
                vintage=vintage,
                summary="Cost of MWh is blank.",
                likely_cause="Missing/invalid Device ID, missing vintage, or missing price setup for that vintage in registration source.",
                suggested_fix="Check Device ID, Vintage, and matching price columns in Registration Data-GRIT first, then Registration Data-Delnotic.",
            )

        # Missing rates
        rate_usd_missing = c_rate_usd and _safe_str(row.get(c_rate_usd, "")) == ""
        rate_bdt_missing = c_rate_bdt and _safe_str(row.get(c_rate_bdt, "")) == ""
        if rate_usd_missing or rate_bdt_missing:
            _add_issue(
                issues,
                issue_type="RATE_MISSING",
                tab="Redemption Status",
                row=row_no,
                priority="Low",
                source_pdf=pdf,
                client_name=client,
                device_id=did_raw,
                device_name=dname,
                vintage=vintage,
                summary="One or more manual rate fields are blank.",
                likely_cause="Rate (USD) and/or Rate (BDT) not entered manually.",
                suggested_fix="Fill the missing Rate (USD) and/or Rate (BDT) manually from agreed client transaction terms before relying on Total Amount (BDT).",
            )

        # Formula output missing
        total_cost_missing = c_total_cost and _safe_str(row.get(c_total_cost, "")) == ""
        total_amt_missing = c_total_amt and _safe_str(row.get(c_total_amt, "")) == ""
        if total_cost_missing or total_amt_missing:
            _add_issue(
                issues,
                issue_type="FORMULA_OUTPUT_MISSING",
                tab="Redemption Status",
                row=row_no,
                priority="Low",
                source_pdf=pdf,
                client_name=client,
                device_id=did_raw,
                device_name=dname,
                vintage=vintage,
                summary="One or more formula-driven finance outputs are blank.",
                likely_cause="Driver inputs are blank or the row formula is broken.",
                suggested_fix="Check Number of Certificate, Cost of MWh, Rate (USD), Rate (BDT), and conversion references before treating it as a formula error.",
            )

    # PARSE_MISMATCH is separate from total mismatch: only emit if Source PDF has no parseable expected number
    if c_pdf:
        seen = set()
        for _, row in df.iterrows():
            pdf = _safe_str(row.get(c_pdf, ""))
            if not pdf or pdf in seen:
                continue
            seen.add(pdf)
            expected = _extract_expected_mwh_from_filename(pdf)
            if expected is None:
                _add_issue(
                    issues,
                    issue_type="PARSE_MISMATCH",
                    tab="Redemption Status",
                    priority="High",
                    source_pdf=pdf,
                    summary="Could not derive expected MWh from PDF filename.",
                    likely_cause="Filename format is inconsistent or parsing of source naming convention failed.",
                    suggested_fix="Recheck the PDF filename format and re-run parse validation. Use a consistent '<date> <mwh> MWh.pdf' style naming pattern.",
                )

    return {
        "mode": "diagnostic",
        "answer": _summarize_issue_set("Redemption Status", issues),
        "issues": issues,
        "helper_tab": {"name": "AI Exceptions - Redemption Status", "rows": issues},
        "meta": {"tab_used": "Redemption Status"},
    }


# ============================================================
# Diagnostics: Exchange
# ============================================================

def _diag_exchange(tables: Dict[str, pd.DataFrame], registry: RegistryLookup) -> Dict[str, Any]:
    exc = tables.get("Exchange")
    dws = tables.get("Device Wise Sales Status")
    issues: List[Dict[str, Any]] = []

    if exc is None or exc.empty:
        return {
            "mode": "diagnostic",
            "answer": "Exchange is empty or missing.",
            "issues": [],
            "helper_tab": {"name": "AI Exceptions - Exchange", "rows": []},
            "meta": {"tab_used": "Exchange"},
        }

    c_did = _first_existing(exc, ["Device ID", "Plant ID"])
    c_dname = _first_existing(exc, ["Device Name", "Plant Name"])
    c_vintage = _first_existing(exc, ["Vintage"])
    c_cert = _first_existing(exc, ["Number of Certificate", "Number of Certificates"])
    c_cost = _first_existing(exc, ["Cost of MWh"])
    c_rate_usd = _first_existing(exc, ["Rate (USD)", "Sales Rate (USD)"])
    c_rate_bdt = _first_existing(exc, ["Rate (BDT)", "Sales Rate / Exchange Rate (BDT)"])
    c_total_cost = _first_existing(exc, ["Total Plant Owner Cost (BDT)", "Plant Owner Cost (BDT)"])
    c_total_amt = _first_existing(exc, ["Total Amount (BDT)", "Total (BDT)"])

    dws_ids: set[str] = set()
    dws_has_row = dws is not None and not dws.empty
    if dws_has_row:
        dws_plant = _first_existing(dws, ["Plant ID"])
        if dws_plant:
            dws_ids = {_normalize_id(v) for v in dws[dws_plant]}

    for _, row in exc.iterrows():
        row_no = int(row["__row"]) if "__row" in row else None
        did_raw = _safe_str(row.get(c_did, "")) if c_did else ""
        did = _normalize_id(did_raw)
        dname = _safe_str(row.get(c_dname, "")) if c_dname else ""
        vintage = _safe_str(row.get(c_vintage, "")) if c_vintage else ""

        if did and dws_has_row and did not in dws_ids:
            _add_issue(
                issues,
                issue_type="SOLD_NOT_FLOWING_TO_DEVICE_WISE_SALES",
                tab="Exchange",
                row=row_no,
                priority="High",
                device_id=did_raw,
                device_name=dname,
                vintage=vintage,
                summary="Exchange Device ID does not exist as Plant ID in Device Wise Sales Status.",
                likely_cause="Missing plant row in Device Wise Sales Status or wrong Device ID in Exchange.",
                suggested_fix="Trust Exchange first. Add the missing plant row to Device Wise Sales Status if the ID is valid; otherwise correct the Device ID in Exchange.",
            )

        if not did:
            _add_issue(
                issues,
                issue_type="MISSING_DEVICE_ID",
                tab="Exchange",
                row=row_no,
                priority="High",
                device_name=dname,
                vintage=vintage,
                summary="Device ID is blank.",
                likely_cause="Manual import left Device ID empty.",
                suggested_fix="Fill Device ID from the correct plant reference before relying on downstream sold flow.",
            )
        elif did not in registry.ids:
            _add_issue(
                issues,
                issue_type="INVALID_DEVICE_ID",
                tab="Exchange",
                row=row_no,
                priority="High",
                device_id=did_raw,
                device_name=dname,
                vintage=vintage,
                summary="Device ID does not exist in registration source.",
                likely_cause="Wrong manual import value.",
                suggested_fix="Correct the Device ID in Exchange. Validate against Registration Data-GRIT first, then Registration Data-Delnotic.",
            )

        if c_cost and _safe_str(row.get(c_cost, "")) == "":
            _add_issue(
                issues,
                issue_type="MISSING_COST_OF_MWH",
                tab="Exchange",
                row=row_no,
                priority="Medium",
                device_id=did_raw,
                device_name=dname,
                vintage=vintage,
                summary="Cost of MWh is blank.",
                likely_cause="Manual import missed cost-side setup or Device ID/vintage does not map cleanly.",
                suggested_fix="Fill or verify Cost of MWh using the same logic as Redemption Status.",
            )

        rate_usd_missing = c_rate_usd and _safe_str(row.get(c_rate_usd, "")) == ""
        rate_bdt_missing = c_rate_bdt and _safe_str(row.get(c_rate_bdt, "")) == ""
        if rate_usd_missing or rate_bdt_missing:
            _add_issue(
                issues,
                issue_type="RATE_MISSING",
                tab="Exchange",
                row=row_no,
                priority="Low",
                device_id=did_raw,
                device_name=dname,
                vintage=vintage,
                summary="One or more sales rate fields are blank.",
                likely_cause="Manual import missed Sales Rate (USD) and/or BDT rate.",
                suggested_fix="Fill Sales Rate (USD) and Sales Rate / Exchange Rate (BDT) manually before using Total (BDT).",
            )

        total_cost_missing = c_total_cost and _safe_str(row.get(c_total_cost, "")) == ""
        total_amt_missing = c_total_amt and _safe_str(row.get(c_total_amt, "")) == ""
        if total_cost_missing or total_amt_missing:
            _add_issue(
                issues,
                issue_type="FORMULA_OUTPUT_MISSING",
                tab="Exchange",
                row=row_no,
                priority="Low",
                device_id=did_raw,
                device_name=dname,
                vintage=vintage,
                summary="One or more formula outputs are blank.",
                likely_cause="Missing driver inputs or broken formula cells.",
                suggested_fix="Check Number of Certificate, Cost of MWh, Sales Rate (USD), and BDT rate first.",
            )

    return {
        "mode": "diagnostic",
        "answer": _summarize_issue_set("Exchange", issues),
        "issues": issues,
        "helper_tab": {"name": "AI Exceptions - Exchange", "rows": issues},
        "meta": {"tab_used": "Exchange"},
    }


# ============================================================
# Diagnostics: Transfer Status
# ============================================================

def _diag_transfer(tables: Dict[str, pd.DataFrame], registry: RegistryLookup) -> Dict[str, Any]:
    out_df = tables.get("Transfer Status (OUT)")
    dws = tables.get("Device Wise Sales Status")
    issues: List[Dict[str, Any]] = []

    if out_df is None or out_df.empty:
        return {
            "mode": "diagnostic",
            "answer": "Transfer Status (OUT) is empty or missing.",
            "issues": [],
            "helper_tab": {"name": "AI Exceptions - Transfer Status", "rows": []},
            "meta": {"tab_used": "Transfer Status"},
        }

    c_pid = _first_existing(out_df, ["Plant ID", "Device ID"])
    c_vintage = _first_existing(out_df, ["Vintage"])
    c_mwh = _first_existing(out_df, ["MWh"])

    dws_ids: set[str] = set()
    if dws is not None and not dws.empty:
        dws_plant = _first_existing(dws, ["Plant ID"])
        if dws_plant:
            dws_ids = {_normalize_id(v) for v in dws[dws_plant]}

    for _, row in out_df.iterrows():
        row_no = int(row["__row"]) if "__row" in row else None
        pid_raw = _safe_str(row.get(c_pid, "")) if c_pid else ""
        pid = _normalize_id(pid_raw)
        vintage = _safe_str(row.get(c_vintage, "")) if c_vintage else ""
        mwh_raw = row.get(c_mwh, "") if c_mwh else ""
        mwh_val = _to_num_or_none(mwh_raw)

        if not pid:
            _add_issue(
                issues,
                issue_type="WRONG_PLANT_ID",
                tab="Transfer Status",
                row=row_no,
                priority="High",
                device_id=pid_raw,
                vintage=vintage,
                summary="Plant ID is blank on Transfer OUT row.",
                likely_cause="Transfer OUT cannot map downstream without Plant ID.",
                suggested_fix="Correct Plant ID in Transfer Status using Registration Data-GRIT first, then Registration Data-Delnotic.",
            )
        elif pid not in registry.ids:
            _add_issue(
                issues,
                issue_type="WRONG_PLANT_ID",
                tab="Transfer Status",
                row=row_no,
                priority="High",
                device_id=pid_raw,
                vintage=vintage,
                summary="Plant ID does not exist in registration source.",
                likely_cause="Wrong transfer plant mapping.",
                suggested_fix="Correct Plant ID in Transfer Status and validate against registration data.",
            )

        if pid and dws_ids and pid not in dws_ids:
            _add_issue(
                issues,
                issue_type="OUT_NOT_FLOWING_TO_DEVICE_WISE_SALES",
                tab="Transfer Status",
                row=row_no,
                priority="High",
                device_id=pid_raw,
                vintage=vintage,
                summary="Transfer OUT Plant ID does not exist in Device Wise Sales Status.",
                likely_cause="Missing plant row in Device Wise Sales Status or wrong Plant ID in Transfer Status.",
                suggested_fix="Validate Plant ID against registration data. If valid, add the missing plant row to Device Wise Sales Status; otherwise correct Plant ID in Transfer Status.",
            )

        if c_mwh and (mwh_val is None or abs(mwh_val) < 1e-12):
            _add_issue(
                issues,
                issue_type="WRONG_MWH",
                tab="Transfer Status",
                row=row_no,
                priority="Medium",
                device_id=pid_raw,
                vintage=vintage,
                summary="Transfer OUT MWh is blank or zero.",
                likely_cause="MWh value was not entered correctly.",
                suggested_fix="Correct MWh in Transfer Status before relying on downstream sold calculations.",
            )

    return {
        "mode": "diagnostic",
        "answer": _summarize_issue_set("Transfer Status", issues),
        "issues": issues,
        "helper_tab": {"name": "AI Exceptions - Transfer Status", "rows": issues},
        "meta": {"tab_used": "Transfer Status"},
    }


# ============================================================
# Diagnostics: Device Wise Sales Status
# ============================================================

def _sum_by_id_vintage(df: pd.DataFrame, id_candidates: List[str], vintage_candidates: List[str], qty_candidates: List[str]) -> Dict[Tuple[str, str], float]:
    if df is None or df.empty:
        return {}

    c_id = _first_existing(df, id_candidates)
    c_v = _first_existing(df, vintage_candidates)
    c_q = _first_existing(df, qty_candidates)
    if not c_id or not c_v or not c_q:
        return {}

    tmp = df.copy()
    tmp["__id"] = tmp[c_id].map(_normalize_id)
    tmp["__v"] = tmp[c_v].map(_normalize_vintage)
    tmp["__q"] = tmp[c_q].map(_to_num)

    tmp = tmp[(tmp["__id"] != "") & (tmp["__v"] != "")]
    grp = tmp.groupby(["__id", "__v"], dropna=False)["__q"].sum().reset_index()
    return {(r["__id"], r["__v"]): float(r["__q"]) for _, r in grp.iterrows()}


def _extract_vintage_from_header(col: str, keyword: str) -> Optional[str]:
    if keyword.lower() not in col.lower():
        return None
    m = re.search(r"(V\d{2}\s*Q[1-4])", col, flags=re.I)
    if not m:
        return None
    return m.group(1).replace(" ", "").upper()


def _diag_device_wise_sales(tables: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    dws = tables.get("Device Wise Sales Status")
    issuance = tables.get("Issuance Status")
    redemption = tables.get("Redemption Status")
    exchange = tables.get("Exchange")
    transfer_out = tables.get("Transfer Status (OUT)")
    issues: List[Dict[str, Any]] = []

    if dws is None or dws.empty:
        return {
            "mode": "diagnostic",
            "answer": "Device Wise Sales Status is empty or missing.",
            "issues": [],
            "helper_tab": {"name": "AI Exceptions - Device Wise Sales Status", "rows": []},
            "meta": {"tab_used": "Device Wise Sales Status"},
        }

    c_pid = _first_existing(dws, ["Plant ID"])
    if not c_pid:
        return {
            "mode": "diagnostic",
            "answer": "Device Wise Sales Status does not contain Plant ID.",
            "issues": [],
            "helper_tab": {"name": "AI Exceptions - Device Wise Sales Status", "rows": []},
            "meta": {"tab_used": "Device Wise Sales Status"},
        }

    # upstream lookups
    issued_lookup = _sum_by_id_vintage(
        issuance,
        ["Device ID", "Plant ID"],
        ["Vintage"],
        ["MWh"],
    )
    red_lookup = _sum_by_id_vintage(
        redemption,
        ["Device ID", "Plant ID"],
        ["Vintage"],
        ["Number of Certificate", "Number of Certificates"],
    )
    exc_lookup = _sum_by_id_vintage(
        exchange,
        ["Device ID", "Plant ID"],
        ["Vintage"],
        ["Number of Certificate", "Number of Certificates"],
    )
    out_lookup = _sum_by_id_vintage(
        transfer_out,
        ["Plant ID", "Device ID"],
        ["Vintage"],
        ["MWh"],
    )

    issued_cols = [c for c in dws.columns if "issued" in _norm(c)]
    sold_cols = [c for c in dws.columns if "sold" in _norm(c)]

    for _, row in dws.iterrows():
        row_no = int(row["__row"]) if "__row" in row else None
        pid_raw = _safe_str(row.get(c_pid, ""))
        pid = _normalize_id(pid_raw)
        if not pid:
            continue

        # issued checks
        for col in issued_cols:
            vint = _extract_vintage_from_header(col, "issued")
            if not vint:
                continue
            actual = _to_num(row.get(col, ""))
            expected = issued_lookup.get((pid, vint), 0.0)
            if expected > 0 and abs(actual - expected) > 1e-9:
                _add_issue(
                    issues,
                    issue_type="ISSUED_NOT_UPDATED",
                    tab="Device Wise Sales Status",
                    row=row_no,
                    priority="High",
                    device_id=pid_raw,
                    vintage=vint,
                    summary=f"Issued column '{col}' = {actual:g}, but Issuance Status implies {expected:g}.",
                    likely_cause="Plant ID mismatch between Issuance Status and Device Wise Sales Status, or missing plant row here.",
                    suggested_fix="Check whether Plant ID matches Device ID from Issuance Status. If the plant is valid but missing here, add the missing plant row.",
                )

        # sold checks
        for col in sold_cols:
            vint = _extract_vintage_from_header(col, "sold")
            if not vint:
                continue
            actual = _to_num(row.get(col, ""))
            expected_red = red_lookup.get((pid, vint), 0.0)
            expected_out = out_lookup.get((pid, vint), 0.0)
            expected_exc = exc_lookup.get((pid, vint), 0.0)
            expected = expected_red + expected_out + expected_exc

            if expected > 0 and abs(actual - expected) > 1e-9:
                _add_issue(
                    issues,
                    issue_type="SOLD_NOT_UPDATED",
                    tab="Device Wise Sales Status",
                    row=row_no,
                    priority="High",
                    device_id=pid_raw,
                    vintage=vint,
                    summary=(
                        f"Sold column '{col}' = {actual:g}, but upstream sources imply {expected:g} "
                        f"(Redemption={expected_red:g}, Transfer OUT={expected_out:g}, Exchange={expected_exc:g})."
                    ),
                    likely_cause="Missing flow from one or more sold sources, Plant ID mismatch, or missing plant row in Device Wise Sales Status.",
                    suggested_fix="Trace all three sold sources: Redemption Status, Transfer OUT, and Exchange. Then check Plant ID matching and whether the plant row exists in Device Wise Sales Status.",
                )

        # sold > issued
        for scol in sold_cols:
            vint = _extract_vintage_from_header(scol, "sold")
            if not vint:
                continue

            # find matching issued column with same vintage
            matching_issued = None
            for icol in issued_cols:
                if _extract_vintage_from_header(icol, "issued") == vint:
                    matching_issued = icol
                    break
            if not matching_issued:
                continue

            sold_val = _to_num(row.get(scol, ""))
            issued_val = _to_num(row.get(matching_issued, ""))
            if sold_val > issued_val + 1e-9:
                _add_issue(
                    issues,
                    issue_type="SOLD_GT_ISSUED",
                    tab="Device Wise Sales Status",
                    row=row_no,
                    priority="High",
                    device_id=pid_raw,
                    vintage=vint,
                    summary=f"Sold {sold_val:g} exceeds Issued {issued_val:g}.",
                    likely_cause="Issued side may be incomplete, though downstream double-counting or manual source issues are also possible.",
                    suggested_fix="First validate whether Issuance Status is complete for this plant/vintage before concluding over-selling or duplicate sold-side flow.",
                )

    return {
        "mode": "diagnostic",
        "answer": _summarize_issue_set("Device Wise Sales Status", issues),
        "issues": issues,
        "helper_tab": {"name": "AI Exceptions - Device Wise Sales Status", "rows": issues},
        "meta": {"tab_used": "Device Wise Sales Status"},
    }


# ============================================================
# Diagnostics: Issuance Status (minimal, since you stopped early)
# ============================================================

def _diag_issuance_status(tables: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    df = tables.get("Issuance Status")
    issues: List[Dict[str, Any]] = []

    if df is None or df.empty:
        return {
            "mode": "diagnostic",
            "answer": "Issuance Status is empty or missing.",
            "issues": [],
            "helper_tab": {"name": "AI Exceptions - Issuance Status", "rows": []},
            "meta": {"tab_used": "Issuance Status"},
        }

    c_id = _first_existing(df, ["Device ID", "Plant ID"])
    c_v = _first_existing(df, ["Vintage"])
    c_mwh = _first_existing(df, ["MWh"])

    for _, row in df.iterrows():
        row_no = int(row["__row"]) if "__row" in row else None
        did = _safe_str(row.get(c_id, "")) if c_id else ""
        vint = _safe_str(row.get(c_v, "")) if c_v else ""
        mwh = _to_num_or_none(row.get(c_mwh, "")) if c_mwh else None

        if c_id and not _normalize_id(did):
            _add_issue(
                issues,
                issue_type="MISSING_DEVICE_ID",
                tab="Issuance Status",
                row=row_no,
                priority="High",
                device_id=did,
                vintage=vint,
                summary="Device ID is blank.",
                likely_cause="Plant-vintage issuance record cannot map downstream.",
                suggested_fix="Fill or correct Device ID before expecting Issued columns to update in Device Wise Sales Status.",
            )
        if c_v and not _normalize_vintage(vint):
            _add_issue(
                issues,
                issue_type="WRONG_VINTAGE",
                tab="Issuance Status",
                row=row_no,
                priority="Medium",
                device_id=did,
                vintage=vint,
                summary="Vintage is blank or malformed.",
                likely_cause="Vintage derivation or source dates are inconsistent.",
                suggested_fix="Check production start/end dates and re-derive the correct vintage.",
            )
        if c_mwh and (mwh is None or abs(mwh) < 1e-12):
            _add_issue(
                issues,
                issue_type="WRONG_MWH",
                tab="Issuance Status",
                row=row_no,
                priority="Medium",
                device_id=did,
                vintage=vint,
                summary="MWh is blank or zero.",
                likely_cause="Source issuance row not processed correctly.",
                suggested_fix="Check the source issuance row and refresh Issuance Status build.",
            )

    return {
        "mode": "diagnostic",
        "answer": _summarize_issue_set("Issuance Status", issues),
        "issues": issues,
        "helper_tab": {"name": "AI Exceptions - Issuance Status", "rows": issues},
        "meta": {"tab_used": "Issuance Status"},
    }


# ============================================================
# Router + formatting
# ============================================================

def _summarize_issue_set(tab_name: str, issues: List[Dict[str, Any]]) -> str:
    if not issues:
        return f"No major issues detected in '{tab_name}'."

    counts: Dict[str, int] = {}
    for it in issues:
        counts[it["Issue Type"]] = counts.get(it["Issue Type"], 0) + 1

    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    lines = [f"Detected {len(issues)} issue(s) in '{tab_name}'."]
    for k, v in ordered[:8]:
        lines.append(f"- {k}: {v}")

    top = issues[0]
    lines.append(
        f"Top priority example: {top['Issue Type']} on row {top['Row No'] or '(sheet-level)'} — {top['Issue Summary']}"
    )
    return "\n".join(lines)


def _run_diagnostic(question: str, tables: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    # enrich transfer status into IN/OUT logical tabs
    if "Transfer Status" in tables:
        parsed = _parse_transfer_status(tables["Transfer Status"])
        for k, v in parsed.items():
            tables[k] = v

    registry = _build_registry_lookup(tables)
    tab = _detect_tab(question)

    if tab == "Redemption Status":
        return _diag_redemption(tables, registry)
    if tab == "Exchange":
        return _diag_exchange(tables, registry)
    if tab == "Transfer Status":
        return _diag_transfer(tables, registry)
    if tab == "Device Wise Sales Status":
        return _diag_device_wise_sales(tables)
    if tab == "Issuance Status":
        return _diag_issuance_status(tables)

    # workbook-level default summary
    sections = [
        _diag_redemption(tables, registry),
        _diag_exchange(tables, registry),
        _diag_transfer(tables, registry),
        _diag_device_wise_sales(tables),
    ]
    all_issues: List[Dict[str, Any]] = []
    for s in sections:
        all_issues.extend(s.get("issues", []))

    answer = _summarize_issue_set("Workbook", all_issues)
    return {
        "mode": "diagnostic",
        "answer": answer,
        "issues": all_issues,
        "helper_tab": {"name": "AI Exceptions - Workbook", "rows": all_issues},
        "meta": {"tab_used": "Workbook"},
    }


# ============================================================
# Public API
# ============================================================

def answer_finance(question: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    tabs = snapshot.get("tabs", {})
    if not isinstance(tabs, dict) or not tabs:
        return {"answer": "No tabs provided in snapshot."}

    tables = _build_tables(snapshot)
    intent = _detect_intent(question)

    if intent == "compute":
        return _run_compute(question, tables)
    return _run_diagnostic(question, tables)
