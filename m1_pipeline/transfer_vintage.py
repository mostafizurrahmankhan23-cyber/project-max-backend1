# m1_pipeline/transfer_vintage.py

from __future__ import annotations

import pandas as pd
from typing import Tuple


# ---------- core quarter / vintage helpers ----------

def _q(dt: pd.Timestamp) -> int:
    """Return quarter number 1..4 for a pandas Timestamp."""
    return (dt.month - 1) // 3 + 1


def get_vintage(start_date, end_date) -> str:
    """
    Return 'VYYQx'.

    - Year = start year (YY).
    - Quarter:
        - if Start and End in different quarters -> use Start's quarter
        - else -> that common quarter
    """
    s = pd.to_datetime(start_date, errors="coerce")
    e = pd.to_datetime(end_date, errors="coerce")

    if pd.isna(s) or pd.isna(e):
        return ""

    yy = s.year % 100      # 2025 -> 25
    qs, qe = _q(s), _q(e)
    q = qs if qs != qe else qe
    return f"V{yy:02d}Q{q}"


def vintage_needs_highlight(start_date, end_date) -> bool:
    """
    True if Start and End are in different quarters.
    (In Sheets you used this to decide yellow highlighting.)
    """
    s = pd.to_datetime(start_date, errors="coerce")
    e = pd.to_datetime(end_date, errors="coerce")
    if pd.isna(s) or pd.isna(e):
        return False
    return _q(s) != _q(e)


# ---------- main function: compute IN + OUT vintage on a DataFrame ----------

def attach_transfer_vintage(
    df_transfer: pd.DataFrame,
    in_start_col: str,
    in_end_col: str,
    out_start_col: str,
    out_end_col: str,
    in_vintage_col: str = "Vintage_IN",
    out_vintage_col: str = "Vintage_OUT",
    add_flags: bool = False,
) -> pd.DataFrame:
    """
    Compute Vintage for IN + OUT sides in a single Transfer Status DataFrame.

    Parameters
    ----------
    df_transfer : DataFrame
        Data as a single table (e.g. from reading the 'Transfer Status' sheet).
    in_start_col : str
        Column name for IN 'Period Starts' (equivalent to column C in sheet).
    in_end_col : str
        Column name for IN 'Period Ends' (equivalent to column D).
    out_start_col : str
        Column name for OUT 'Period Starts' (equivalent to column L).
    out_end_col : str
        Column name for OUT 'Period Ends' (equivalent to column M).
    in_vintage_col : str, default "Vintage_IN"
        Name of the IN Vintage column to create/overwrite.
    out_vintage_col : str, default "Vintage_OUT"
        Name of the OUT Vintage column to create/overwrite.
    add_flags : bool, default False
        If True, add boolean columns:
            - 'Highlight_IN'  (Start/End IN quarters differ)
            - 'Highlight_OUT' (Start/End OUT quarters differ)

    Returns
    -------
    df_out : DataFrame
        Copy of df_transfer with vintage columns (and optional flags) added.
    """
    df = df_transfer.copy()

    # IN Vintage
    df[in_vintage_col] = df.apply(
        lambda row: get_vintage(row.get(in_start_col, ""), row.get(in_end_col, "")),
        axis=1,
    )

    # OUT Vintage
    df[out_vintage_col] = df.apply(
        lambda row: get_vintage(row.get(out_start_col, ""), row.get(out_end_col, "")),
        axis=1,
    )

    if add_flags:
        df["Highlight_IN"] = df.apply(
            lambda row: vintage_needs_highlight(row.get(in_start_col, ""), row.get(in_end_col, "")),
            axis=1,
        )
        df["Highlight_OUT"] = df.apply(
            lambda row: vintage_needs_highlight(row.get(out_start_col, ""), row.get(out_end_col, "")),
            axis=1,
        )

    return df
