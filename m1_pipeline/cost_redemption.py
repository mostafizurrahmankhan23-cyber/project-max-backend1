# m1_pipeline/cost_redemption.py

from __future__ import annotations

import re
from io import BytesIO
from typing import Dict, Tuple

import pandas as pd


# ---------- helpers ----------

def normalize_id(s: str) -> str:
    """
    Normalize device/plant IDs for matching.

    Example:
      ' 2.75ses10000 ' -> '2.75SES10000'
    """
    if s is None:
        return ""
    s = str(s).upper().strip()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^A-Z0-9\.]", "", s)
    return s


def normalize_text(s: str) -> str:
    if s is None:
        return ""
    return str(s).strip().upper()


def pick(colnames, *candidates) -> str:
    """
    Return the first matching column name from candidates, case-insensitive.
    """
    lower_map = {str(c).strip().lower(): c for c in colnames}
    for cand in candidates:
        key = str(cand).strip().lower()
        if key in lower_map:
            return lower_map[key]
    raise KeyError(
        f"Could not find any of {candidates} in columns: {list(colnames)}"
    )


def parse_percent(x) -> float:
    """
    Convert percent-like values to fraction.

    Accepts:
      80%
      80.00%
      80
      0.8

    Returns:
      0.8
    """
    if pd.isna(x):
        return float("nan")

    s = str(x).strip().replace(",", "")
    if not s:
        return float("nan")

    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return float("nan")

    try:
        val = float(s)
    except ValueError:
        return float("nan")

    # If user stored 80 instead of 0.8, convert to fraction
    return val / 100.0 if val > 1 else val


def parse_number(x) -> float:
    """
    Parse numeric cell safely.
    """
    if pd.isna(x):
        return float("nan")

    s = str(x).strip().replace(",", "")
    if not s:
        return float("nan")

    try:
        return float(s)
    except ValueError:
        return float("nan")


def first_existing_column(columns, candidates) -> str | None:
    """
    Return first candidate column that exists, case-insensitive.
    """
    lower_cols = {str(c).strip().lower(): c for c in columns}
    for cand in candidates:
        actual = lower_cols.get(str(cand).strip().lower())
        if actual:
            return actual
    return None


def get_price_col_from_vintage(
    vintage: str,
    columns,
    quarterly_start_year: int = 25,
) -> str | None:
    """
    Determine which selling-price column should be used from vintage.

    Business rule:
    - If year < quarterly_start_year:
        V23Q1..V23Q4 -> V23 Selling Price
        V24Q1..V24Q4 -> V24 Selling Price
    - If year >= quarterly_start_year:
        V25Q1 -> V25 Q1 Selling Price
        V26Q2 -> V26 Q2 Selling Price
        V27Q3 -> V27 Q3 Selling Price
        ... and so on for future years.

    Supports both:
      'V25 Q1 Selling Price'
      'V25Q1 Selling Price'
    """
    if vintage is None:
        return None

    v = str(vintage).strip().upper()

    # Example matches: V23Q1, V24Q4, V27Q2
    m = re.fullmatch(r"V(\d{2})Q([1-4])", v)
    if not m:
        return None

    yy = int(m.group(1))   # 23, 24, 25, 26, ...
    qq = int(m.group(2))   # 1, 2, 3, 4

    # Older vintages use one yearly price column
    if yy < quarterly_start_year:
        return first_existing_column(
            columns,
            [f"V{yy} Selling Price"]
        )

    # Newer vintages use quarter-wise price columns
    return first_existing_column(
        columns,
        [
            f"V{yy} Q{qq} Selling Price",
            f"V{yy}Q{qq} Selling Price",
        ]
    )


# ---------- registration workbook logic ----------

def build_registration_lookup_from_workbook(
    reg_sheets: dict[str, pd.DataFrame]
) -> dict[str, dict]:
    """
    Build normalized Device ID -> row dict lookup.

    Priority rule:
      1. GRIT
      2. Delnotic

    So if a Device ID exists in both, GRIT wins.
    """
    lookup: dict[str, dict] = {}
    priority_order = ["GRIT", "Delnotic"]

    for sheet_name in priority_order:
        df = reg_sheets.get(sheet_name)
        if df is None or df.empty:
            continue

        df = df.copy()
        df.columns = [str(c).strip() for c in df.columns]

        try:
            col_device = pick(df.columns, "Device ID", "Plant ID", "Plant Id", "Plant_ID")
            col_owner = pick(
                df.columns,
                "Plant Owner's %",
                "Plant Owners %",
                "Plant Owner %",
                "Owner %",
            )
        except KeyError:
            # Skip tabs that don't have the required registration structure
            continue

        for _, row in df.iterrows():
            norm_id = normalize_id(row.get(col_device, ""))
            if not norm_id:
                continue

            # GRIT wins because it is processed first
            if norm_id not in lookup:
                record = row.to_dict()
                record["_sheet_name"] = sheet_name
                record["_owner_col"] = col_owner
                lookup[norm_id] = record

    return lookup


# ---------- core function ----------

def attach_redemption_cost(
    registration_sheets: dict[str, pd.DataFrame],
    df_redemption: pd.DataFrame,
    quarterly_start_year: int = 25,
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """
    Attach/computed 'Cost of MWh' into Redemption Status.

    Calculation:
        Cost of MWh = Plant Owner's % × matched selling price

    Matching rule:
        Redemption Status.Device ID -> search in GRIT first, then Delnotic

    Vintage rule:
        Older years -> yearly selling price column
        Newer years -> quarter-wise selling price column
    """
    red = df_redemption.copy()

    red_col_did = pick(red.columns, "Device ID", "Plant ID", "Plant Id", "Plant_ID")
    red_col_vintage = pick(red.columns, "Vintage")

    # Ensure output column exists
    if any(str(c).strip().lower() == "cost of mwh" for c in red.columns):
        red_col_cost = next(
            c for c in red.columns if str(c).strip().lower() == "cost of mwh"
        )
    else:
        red_col_cost = "Cost of MWh"
        red[red_col_cost] = ""

    reg_lookup = build_registration_lookup_from_workbook(registration_sheets)

    hits = 0
    misses = 0
    missing_device = 0
    missing_vintage = 0
    missing_price_column = 0
    invalid_values = 0

    out_costs = []

    for _, row in red.iterrows():
        device_id = normalize_id(row.get(red_col_did, ""))
        vintage = row.get(red_col_vintage, "")

        if not device_id or device_id not in reg_lookup:
            out_costs.append("")
            misses += 1
            missing_device += 1
            continue

        reg_row = reg_lookup[device_id]
        owner_col = reg_row["_owner_col"]

        price_col = get_price_col_from_vintage(
            vintage=vintage,
            columns=list(reg_row.keys()),
            quarterly_start_year=quarterly_start_year,
        )

        if not vintage or str(vintage).strip() == "":
            out_costs.append("")
            misses += 1
            missing_vintage += 1
            continue

        if not price_col:
            out_costs.append("")
            misses += 1
            missing_price_column += 1
            continue

        owner_frac = parse_percent(reg_row.get(owner_col, ""))
        selling_price = parse_number(reg_row.get(price_col, ""))

        if pd.isna(owner_frac) or pd.isna(selling_price):
            out_costs.append("")
            misses += 1
            invalid_values += 1
            continue

        cost = round(owner_frac * selling_price, 4)
        out_costs.append(cost)
        hits += 1

    red[red_col_cost] = out_costs

    stats = {
        "hits": hits,
        "misses": misses,
        "missing_device": missing_device,
        "missing_vintage": missing_vintage,
        "missing_price_column": missing_price_column,
        "invalid_values": invalid_values,
    }

    return red, stats


# ---------- optional convenience function for uploaded workbook files ----------

def attach_redemption_cost_from_excel_files(
    registration_file_bytes: bytes,
    redemption_file_bytes: bytes,
    quarterly_start_year: int = 25,
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """
    Convenience wrapper if your API receives raw uploaded XLSX bytes.

    registration_file_bytes:
        workbook containing GRIT and Delnotic sheets

    redemption_file_bytes:
        workbook/sheet for Redemption Status
    """
    registration_sheets = pd.read_excel(BytesIO(registration_file_bytes), sheet_name=None)
    df_redemption = pd.read_excel(BytesIO(redemption_file_bytes))

    return attach_redemption_cost(
        registration_sheets=registration_sheets,
        df_redemption=df_redemption,
        quarterly_start_year=quarterly_start_year,
    )
