# m1_pipeline/issuance_status.py

from __future__ import annotations

import pandas as pd


# ---------- small helpers ----------

def pick(colnames, *candidates):
    """
    Find a column in `colnames` that matches any of the candidate names
    (case-insensitive). Raises KeyError if none are found.
    """
    lower_map = {str(c).lower(): c for c in colnames}
    for cand in candidates:
        key = str(cand).lower()
        if key in lower_map:
            return lower_map[key]
    raise KeyError(f"Could not find any of {candidates} in columns: {list(colnames)}")


def _q(dt: pd.Timestamp) -> int:
    """Quarter number 1..4 from a pandas Timestamp."""
    return (dt.month - 1) // 3 + 1


def get_vintage_safe(start_date, end_date) -> str:
    """
    Return 'VYYQx'.

    Year = year(start_date).
    Quarter = start-quarter if start/end in different quarters,
              else that common quarter.
    """
    s = pd.to_datetime(start_date, errors="coerce")
    e = pd.to_datetime(end_date,   errors="coerce")
    if pd.isna(s) or pd.isna(e):
        return ""
    yy = s.year % 100
    qs, qe = _q(s), _q(e)
    q = qs if qs != qe else qe
    return f"V{yy:02d}Q{q}"


def start_end_to_iso(series):
    """Helper: convert a Series of date-like values to 'YYYY-MM-DD' strings."""
    return pd.to_datetime(series, errors="coerce").dt.strftime("%Y-%m-%d")


# ---------- core function ----------

def build_issuance_status(df_src: pd.DataFrame) -> pd.DataFrame:
    """
    Pure M-1_IssuanceStatus logic.

    Input: df_src = raw 'Device Issuance Status' table from Excel/Sheet.
           Must contain:
             - Device column (e.g. 'Device')
             - Start date column
             - End date column
             - MWh / Period Production column

    Output: DataFrame with columns:
        Sl, Device ID, Production Starts, Production Ends, Vintage, MWh
    """

    df = df_src.copy()

    # Robust column selection (same idea as in Colab)
    col_device = pick(df.columns, "Device")
    col_start  = pick(df.columns, "Start Date")
    col_end    = pick(df.columns, "End Date")
    col_mwh    = pick(
        df.columns,
        "Period Production",
        "Period Production (MWh)",
        "MWh",
    )

    out = pd.DataFrame()

    # Device ID = part before " - "
    out["Device ID"] = (
        df[col_device]
        .astype(str)
        .str.split(" - ", n=1, expand=True)[0]
        .str.strip()
    )

    # Production start/end dates
    out["Production Starts"] = start_end_to_iso(df[col_start])
    out["Production Ends"]   = start_end_to_iso(df[col_end])

    # Vintage
    out["Vintage"] = out.apply(
        lambda r: get_vintage_safe(r["Production Starts"], r["Production Ends"]),
        axis=1,
    )

    # MWh (keep as string here; you can cast to float later if needed)
    out["MWh"] = df[col_mwh].astype(str).str.strip()

    # Serial number
    out.insert(0, "Sl", range(1, len(out) + 1))

    # Ensure column order
    cols = ["Sl", "Device ID", "Production Starts", "Production Ends", "Vintage", "MWh"]
    out = out[cols]

    return out
