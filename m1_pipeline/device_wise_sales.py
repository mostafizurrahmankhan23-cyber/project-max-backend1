# m1_pipeline/device_wise_sales.py

from __future__ import annotations

import re
from typing import List, Tuple, Dict

import pandas as pd
from unidecode import unidecode


def _normalize_text(x) -> str:
    """Unidecode + strip; always return string."""
    return unidecode(str(x).strip())


def build_device_vintage_lookup(issuance_df: pd.DataFrame) -> Dict[Tuple[str, str], float]:
    """
    From Issuance Status table, build:
        (Device ID, Vintage) -> total MWh
    """
    for col in ["Device ID", "Vintage", "MWh"]:
        if col not in issuance_df.columns:
            raise KeyError(f"Issuance DataFrame missing required column '{col}'.")

    df = issuance_df.copy()

    # Clean keys
    df["Device ID"] = df["Device ID"].apply(_normalize_text)
    df["Vintage"]   = df["Vintage"].apply(_normalize_text)

    # Ensure numeric MWh
    df["MWh"] = pd.to_numeric(df["MWh"], errors="coerce").fillna(0.0)

    grouped_df = (
        df.groupby(["Device ID", "Vintage"], as_index=False)["MWh"]
          .sum()
          .reset_index(drop=True)
    )

    lookup = {
        (row["Device ID"], row["Vintage"]): float(row["MWh"])
        for _, row in grouped_df.iterrows()
    }
    return lookup


def fill_device_wise_sales(
    device_df: pd.DataFrame,
    issuance_df: pd.DataFrame,
    plant_id_col: str = "Plant ID",
    issued_substring: str = "Issued",
) -> pd.DataFrame:
    """
    Fill 'Issued' vintage columns in Device Wise Sales Status using Issuance Status totals.

    Parameters
    ----------
    device_df : DataFrame
        Device Wise Sales Status data (must have Plant ID + 'Issued...' columns).
    issuance_df : DataFrame
        Issuance Status data (must have Device ID, Vintage, MWh).
    plant_id_col : str, default "Plant ID"
        Column in device_df that corresponds to 'Device ID' in issuance_df.
    issued_substring : str, default "Issued"
        We treat any column whose name contains this substring as an 'Issued' column
        and attempt to extract a vintage code from its name like 'V24Q1'.

    Returns
    -------
    df_out : DataFrame
        Copy of device_df with Issued columns filled with summed MWh.
    """
    if plant_id_col not in device_df.columns:
        raise KeyError(f"Device DataFrame missing '{plant_id_col}' column.")

    df_dev = device_df.copy()

    # Normalize Plant ID
    df_dev[plant_id_col] = df_dev[plant_id_col].apply(_normalize_text)

    # Build (Device ID, Vintage) -> MWh lookup from Issuance data
    lookup = build_device_vintage_lookup(issuance_df)

    # Identify 'Issued' columns (same as: [col for col in device_df.columns if "Issued" in col])
    issued_cols: List[str] = [col for col in df_dev.columns if issued_substring in col]

    # Fill Issued columns
    for idx, row in df_dev.iterrows():
        plant_id = row[plant_id_col]

        for col in issued_cols:
            # Extract VxxQx from column name (ignore spaces)
            match = re.search(r"(V\d{2}Q\d)", col.replace(" ", ""))
            if not match:
                # No vintage code in this column name: leave as-is
                continue

            vintage = match.group(1)  # e.g., "V24Q1"
            key = (plant_id, vintage)

            if key in lookup:
                df_dev.at[idx, col] = lookup[key]
            else:
                # Keep blank if no matching issuance
                df_dev.at[idx, col] = ""

    return df_dev
