# m1_pipeline/parse_check.py
from __future__ import annotations

import re
from typing import Dict, Any, Optional, Tuple

import numpy as np
import pandas as pd


def _pick(cols, *cands):
    m = {str(c).strip().lower(): c for c in cols}
    for cand in cands:
        k = str(cand).strip().lower()
        if k in m:
            return m[k]
    raise KeyError(f"Missing column. Needed one of {cands}. Found: {list(cols)}")


def _to_num(x) -> float:
    if x is None:
        return np.nan
    s = str(x).strip()
    if not s or s.lower() in {"nan", "none", "nat"}:
        return np.nan
    # remove commas
    s = s.replace(",", "")
    try:
        return float(s)
    except Exception:
        return np.nan


_MWH_RE = re.compile(r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)\s*mwh", re.I)

def extract_expected_mwh_from_filename(source_pdf: str) -> Optional[float]:
    """
    Extract number before 'MWh' from strings like:
      '21 Nov 2024 5000 MWh.pdf'
      '22 November 2025 7,020 MWh (Old).pdf'
    """
    if not source_pdf:
        return None
    m = _MWH_RE.search(str(source_pdf))
    if not m:
        return None
    s = m.group(1).replace(",", "")
    try:
        return float(s)
    except Exception:
        return None


def build_parse_check(df_redemption: pd.DataFrame, tol: float = 1e-6) -> pd.DataFrame:
    """
    For each Source PDF:
      expected_mwh = number extracted from filename (.. MWh ..)
      actual_sum = sum(Number of Certificate)
      diff = actual_sum - expected_mwh
    Output columns:
      No., Client Name, Source PDF, Difference
    """
    src_col = _pick(df_redemption.columns, "Source PDF", "Source_PDF", "SourcePdf")
    cert_col = _pick(df_redemption.columns, "Number of Certificate", "Number of Certificates", "Certificates", "MWh")

    # optional: client name (for convenience)
    client_col = None
    for c in ["Client Name", "Client", "Buyer", "Customer"]:
        try:
            client_col = _pick(df_redemption.columns, c)
            break
        except Exception:
            pass

    df = df_redemption.copy()
    df[src_col] = df[src_col].astype(str).str.strip()
    df = df[df[src_col].str.len() > 0].copy()

    df["_cert"] = df[cert_col].map(_to_num).fillna(0.0)
    df["_expected"] = df[src_col].map(extract_expected_mwh_from_filename)

    # group
    g = df.groupby(src_col, dropna=False).agg(
        actual_sum=("_cert", "sum"),
        expected=("_expected", "first"),
    ).reset_index()

    # compute diff (only where expected exists)
    g["difference"] = g["actual_sum"] - g["expected"]
    bad = g[g["expected"].notna() & (g["difference"].abs() > tol)].copy()

    # attach client name (first seen for that pdf) for quick scan
    if client_col:
        first_client = df.groupby(src_col)[client_col].first().to_dict()
        bad["client name"] = bad[src_col].map(lambda k: str(first_client.get(k, "")).strip())
    else:
        bad["client name"] = ""

    bad = bad.sort_values("difference", key=lambda s: s.abs(), ascending=False)

    out = pd.DataFrame({
        "No.": np.arange(1, len(bad) + 1),
        "Client Name": bad["client name"].astype(str),
        "Source PDF": bad[src_col].astype(str),
        "Difference": bad["difference"].astype(float),
    })

    return out
