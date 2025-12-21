# m1_pipeline/transfer_vintage.py
from __future__ import annotations
import pandas as pd

def _q(dt: pd.Timestamp) -> int:
    return (dt.month - 1) // 3 + 1

def get_vintage(start_date, end_date) -> str:
    """
    Org rule: use START year + START quarter even if crossing quarters.
    Returns 'VYYQx'. If either date invalid -> ''.
    """
    s = pd.to_datetime(start_date, errors="coerce")
    e = pd.to_datetime(end_date, errors="coerce")
    if pd.isna(s) or pd.isna(e):
        return ""
    yy = s.year % 100
    q = _q(s)
    return f"V{yy:02d}Q{q}"

def vintage_needs_highlight(start_date, end_date) -> bool:
    s = pd.to_datetime(start_date, errors="coerce")
    e = pd.to_datetime(end_date, errors="coerce")
    if pd.isna(s) or pd.isna(e):
        return False
    return _q(s) != _q(e)

def _first_existing(df: pd.DataFrame, names: list[str]) -> str:
    for n in names:
        if n in df.columns:
            return n
    raise KeyError(f"None of {names} found in columns: {list(df.columns)}")

def _norm_header(h: str) -> str:
    # normalize: strip, collapse spaces, remove non-breaking spaces
    return str(h).replace("\u00A0", " ").strip().lower()

def _find_nth(df: pd.DataFrame, target: str, n: int) -> str:
    """
    Find the nth occurrence (1-based) of a header whose normalized name == target.
    Example: target="period starts", n=2 returns OUT "Period Starts".
    """
    target = target.lower()
    matches = [c for c in df.columns if _norm_header(c) == target]
    if len(matches) < n:
        raise KeyError(f"Need {n} occurrence(s) of '{target}', found {len(matches)}. Columns: {list(df.columns)}")
    return matches[n - 1]

def attach_transfer_vintage(df_transfer: pd.DataFrame, add_flags: bool = False) -> pd.DataFrame:
    df = df_transfer.copy()

    # normalize headers but keep originals
    df.columns = [str(c).replace("\u00A0", " ").strip() for c in df.columns]

    # IN = 1st occurrence, OUT = 2nd occurrence
    in_start = _find_nth(df, "Period Starts", 1)
    in_end   = _find_nth(df, "Period Ends", 1)
    in_vin   = _find_nth(df, "Vintage", 1)

    out_start = _find_nth(df, "Period Starts", 2)
    out_end   = _find_nth(df, "Period Ends", 2)
    out_vin   = _find_nth(df, "Vintage", 2)

    df[in_vin]  = df.apply(lambda r: get_vintage(r.get(in_start, ""),  r.get(in_end, "")),  axis=1)
    df[out_vin] = df.apply(lambda r: get_vintage(r.get(out_start, ""), r.get(out_end, "")), axis=1)

    if add_flags:
        df["Highlight_IN"] = df.apply(lambda r: vintage_needs_highlight(r.get(in_start, ""),  r.get(in_end, "")),  axis=1)
        df["Highlight_OUT"] = df.apply(lambda r: vintage_needs_highlight(r.get(out_start, ""), r.get(out_end, "")), axis=1)

    return df


