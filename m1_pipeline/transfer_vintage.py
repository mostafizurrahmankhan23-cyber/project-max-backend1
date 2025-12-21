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

def attach_transfer_vintage(df_transfer: pd.DataFrame, add_flags: bool = False) -> pd.DataFrame:
    df = df_transfer.copy()

    # ✅ make headers robust (handles trailing spaces)
    df.columns = [str(c).strip() for c in df.columns]

    # IN columns
    in_start = _first_existing(df, ["Period Starts", "Period Start"])
    in_end   = _first_existing(df, ["Period Ends", "Period End"])
    in_vin   = _first_existing(df, ["Vintage"])

    # OUT columns (duplicates -> .1)
    out_start = _first_existing(df, ["Period Starts.1", "Period Start.1"])
    out_end   = _first_existing(df, ["Period Ends.1", "Period End.1"])
    out_vin   = _first_existing(df, ["Vintage.1"])

    df[in_vin] = df.apply(lambda r: get_vintage(r.get(in_start, ""), r.get(in_end, "")), axis=1)
    df[out_vin] = df.apply(lambda r: get_vintage(r.get(out_start, ""), r.get(out_end, "")), axis=1)

    if add_flags:
        df["Highlight_IN"] = df.apply(lambda r: vintage_needs_highlight(r.get(in_start, ""), r.get(in_end, "")), axis=1)
        df["Highlight_OUT"] = df.apply(lambda r: vintage_needs_highlight(r.get(out_start, ""), r.get(out_end, "")), axis=1)

    return df

