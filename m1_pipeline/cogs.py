# m1_pipeline/cogs.py

from __future__ import annotations

import pandas as pd
from typing import List


def registration_cost(cap: float) -> float:
    """
    Replicates your Colab logic:

    - >= 3 MW      -> 1170.0
    - >= 1 MW      -> 588.5
    - >= 0.25 MW   -> 117.7
    - else         -> 117.7
    """
    if cap >= 3:
        return 1170.0
    elif cap >= 1:
        return 588.5
    elif cap >= 0.25:
        return 117.7
    else:
        return 117.7


def compute_cogs(
    cogs_df: pd.DataFrame,
    sales_df: pd.DataFrame,
    plant_id_col_cogs: str = "Plant ID",
    plant_id_col_sales: str = "Plant ID",
    capacity_col: str = "Plant Capacity (MW)",
    issued_substring: str = "Issued",
    sold_substring: str = "Sold",
    issuance_rate: float = 0.03,   # 3%
    redemption_rate: float = 0.08  # 8%
) -> pd.DataFrame:
    """
    Core logic from your Colab M-1_COGS script, but purely in pandas.

    Inputs
    ------
    cogs_df   : COGS table (one row per plant)
    sales_df  : Device Wise Sales Status table (has Issued*/Sold* columns)

    Returns
    -------
    merged : DataFrame
        Updated COGS with:
        - Registration Cost
        - Issued IREC, Sold IREC
        - Issuance Cost, Redemption Cost
    """

    # --- 1) Registration Cost ---
    if capacity_col not in cogs_df.columns:
        raise KeyError(f"COGS DataFrame missing '{capacity_col}' column.")

    cogs = cogs_df.copy()

    cogs[capacity_col] = pd.to_numeric(cogs[capacity_col], errors="coerce").fillna(0.0)
    cogs["Registration Cost"] = cogs[capacity_col].apply(registration_cost)

    # --- 2) Issued / Sold IREC from Device Wise Sales ---
    if plant_id_col_sales not in sales_df.columns:
        raise KeyError(f"Sales DataFrame missing '{plant_id_col_sales}' column.")

    sales = sales_df.copy()

    # Identify Issued / Sold columns by substring
    issued_cols: List[str] = [c for c in sales.columns if issued_substring in c]
    sold_cols:   List[str] = [c for c in sales.columns if sold_substring in c]

    # Convert to numeric and fill NaN
    for col in issued_cols + sold_cols:
        sales[col] = pd.to_numeric(sales[col], errors="coerce").fillna(0.0)

    # Group by Plant ID and sum
    if not issued_cols and not sold_cols:
        # No Issued/Sold columns present – just attach zeros
        sum_df = sales[[plant_id_col_sales]].drop_duplicates().copy()
        sum_df["Issued IREC"] = 0.0
        sum_df["Sold IREC"] = 0.0
    else:
        sum_df = sales.groupby(plant_id_col_sales, as_index=False)[issued_cols + sold_cols].sum()
        sum_df["Issued IREC"] = sum_df[issued_cols].sum(axis=1) if issued_cols else 0.0
        sum_df["Sold IREC"]   = sum_df[sold_cols].sum(axis=1)   if sold_cols   else 0.0
        sum_df = sum_df[[plant_id_col_sales, "Issued IREC", "Sold IREC"]]

    # --- 3) Merge into COGS ---
    # Drop old Issued/Sold IREC columns if they exist
    cogs = cogs.drop(columns=["Issued IREC", "Sold IREC"], errors="ignore")

    merged = cogs.merge(
        sum_df,
        left_on=plant_id_col_cogs,
        right_on=plant_id_col_sales,
        how="left",
    )

    # If the merge created a duplicate key column, drop it
    if plant_id_col_sales in merged.columns and plant_id_col_sales != plant_id_col_cogs:
        merged = merged.drop(columns=[plant_id_col_sales])

    # --- 4) Compute costs ---
    merged["Issued IREC"] = pd.to_numeric(merged["Issued IREC"], errors="coerce").fillna(0.0)
    merged["Sold IREC"]   = pd.to_numeric(merged["Sold IREC"],   errors="coerce").fillna(0.0)

    merged["Issuance Cost"]   = (merged["Issued IREC"] * issuance_rate).round(2)
    merged["Redemption Cost"] = (merged["Sold IREC"]   * redemption_rate).round(2)

    # --- 5) Reorder columns similar to your Colab script ---
    for col in ["Issuance Cost", "Redemption Cost"]:
        if col not in merged.columns:
            merged[col] = ""

    cols = list(merged.columns)
    if "Registration Cost" in cols:
        left_part = cols[: cols.index("Registration Cost") + 1]
    else:
        left_part = cols[:]  # fallback: no special block

    block = [c for c in ["Issued IREC", "Issuance Cost", "Sold IREC", "Redemption Cost"] if c in cols]
    placed = set(left_part + block)
    right_part = [c for c in cols if c not in placed]

    ordered_cols = left_part + block + right_part
    merged = merged[ordered_cols]

    return merged
