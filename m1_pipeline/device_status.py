# m1_pipeline/device_status.py

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
    raise KeyError(f"None of {candidates} found in columns {list(colnames)}")


def fmt_percent(val):
    """
    Normalize percent values into a string like '80%'.
    Accepts '80', '80%', 0.8, '0.8', etc.
    """
    s = str(val).strip()
    if s == "" or s.lower() in {"nan", "none"}:
        return ""
    if s.endswith("%"):
        return s
    try:
        f = float(s)
        if 0 <= f <= 1:
            return f"{round(f * 100)}%"
        else:
            return f"{round(f)}%"
    except Exception:
        return s


def parse_percent(p):
    """
    Convert '80%', '80', 0.8 etc. to a numeric fraction in [0,1].
    """
    s = str(p).strip()
    if s == "" or s.lower() in {"nan", "none"}:
        return 0.0
    if s.endswith("%"):
        s = s.replace("%", "")
    try:
        f = float(s)
        if f > 1:
            f = f / 100.0
        return f
    except Exception:
        return 0.0


# ---------- main function ----------

def build_device_status(
    df_devices: pd.DataFrame,
    df_vendor: pd.DataFrame,
    selling_price: float = 4.5,
) -> pd.DataFrame:
    """
    Pure M-1_DeviceStatus logic.

    Parameters
    ----------
    df_devices : DataFrame
        Device Registration table (approved devices).
        Must contain columns: 'Device ID', 'Country', 'Status'.
    df_vendor : DataFrame
        Vendor Payment table with Plant Owner's %.
        Must contain some variant of 'Plant ID' and a percent column.
    selling_price : float
        Global selling price per MWh.

    Returns
    -------
    output_df : DataFrame
        Columns: SL, Plant ID, Country, Plant Owner's %, Selling Price, Cost of MWh
    """

    # --- 1) Filter approved devices and clean ID / Country ---
    devices = df_devices.copy()

    devices["Status"] = devices["Status"].astype(str)
    approved_df = devices[
        devices["Status"].str.contains("approved", case=False, na=False)
    ].copy()

    approved_df["Device ID"] = approved_df["Device ID"].astype(str).str.strip()
    approved_df["Country"] = approved_df["Country"].astype(str).str.strip()

    # --- 2) Prepare vendor data (Plant Owner's %) ---
    vendor = df_vendor.copy()

    col_plant_id_vendor = pick(vendor.columns, "Plant ID", "Plant Id", "Plant_ID")
    col_percent_vendor = pick(
        vendor.columns, "%", "Percent", "Ownership %", "Plant Owner's %"
    )

    vendor[col_plant_id_vendor] = vendor[col_plant_id_vendor].astype(str).str.strip()
    vendor["Plant Owner's %"] = vendor[col_percent_vendor].map(fmt_percent)

    df_vendor_small = vendor[[col_plant_id_vendor, "Plant Owner's %"]].drop_duplicates(
        subset=[col_plant_id_vendor]
    )

    # --- 3) Merge Plant Owner's % into approved devices ---
    merged = approved_df.merge(
        df_vendor_small,
        left_on="Device ID",
        right_on=col_plant_id_vendor,
        how="left",
    )

    merged["Plant Owner's %"] = merged["Plant Owner's %"].fillna("")

    # --- 4) Compute Selling Price and Cost of MWh ---
    merged["Numeric_%"] = merged["Plant Owner's %"].map(parse_percent)
    merged["Selling Price"] = float(selling_price)
    merged["Cost of MWh"] = merged["Numeric_%"] * merged["Selling Price"]

    # --- 5) Final output table ---
    output_df = pd.DataFrame(
        {
            "SL": range(1, len(merged) + 1),
            "Plant ID": merged["Device ID"],
            "Country": merged["Country"],
            "Plant Owner's %": merged["Plant Owner's %"],
            "Selling Price": merged["Selling Price"].round(4),
            "Cost of MWh": merged["Cost of MWh"].round(4),
        }
    )

    return output_df
