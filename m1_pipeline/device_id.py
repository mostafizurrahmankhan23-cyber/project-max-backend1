# m1_pipeline/device_id.py

from __future__ import annotations

import re
from typing import List, Tuple

import pandas as pd
from difflib import get_close_matches
from unidecode import unidecode


# -------------------------
# Helpers
# -------------------------

def pick(colnames, *candidates) -> str:
    """
    Find the first column whose name matches any candidate (case-insensitive).
    """
    lower_map = {str(c).strip().lower(): c for c in colnames}
    for cand in candidates:
        key = str(cand).strip().lower()
        if key in lower_map:
            return lower_map[key]
    raise KeyError(f"Missing columns. Need one of {candidates}. Found: {list(colnames)}")


def normalize(name: str) -> str:
    """
    Normalize names for matching:
    - ASCII fold
    - lowercase
    - keep letters/digits/spaces/dashes
    """
    if name is None:
        return ""
    name = unidecode(str(name)).lower()
    name = name.replace("\u00a0", " ").replace("\u200b", "").replace("–", "-")
    name = re.sub(r"[^a-z0-9\s\-]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def find_best_match(name: str, ref_list: List[str], threshold: float) -> str | None:
    name = normalize(name)
    if not name:
        return None
    matches = get_close_matches(name, ref_list, n=1, cutoff=threshold)
    return matches[0] if matches else None


# -------------------------
# Core
# -------------------------

def attach_device_ids(
    df_sales: pd.DataFrame,
    df_devreg: pd.DataFrame,
    fuzzy_threshold: float = 0.67,
) -> tuple[pd.DataFrame, list[tuple[str, str]], pd.DataFrame]:
    """
    Fill Sales/Redemption 'Device ID' by matching Sales 'Device Name'
    to Registry 'Name' (or variants), then copying Registry 'Device ID'.

    Robust to header variations.
    """

    sales = df_sales.copy()
    reg = df_devreg.copy()

    # --- robust column selection ---
    sales_name_col = pick(
        sales.columns,
        "Device Name", "Device", "Plant Name", "Project Name", "Name"
    )

    # Ensure sales has an ID column (create if missing)
    if any(str(c).strip().lower() in {"device id", "plant id"} for c in sales.columns):
        sales_id_col = pick(sales.columns, "Device ID", "Plant ID")
    else:
        sales_id_col = "Device ID"
        sales[sales_id_col] = ""

    reg_name_col = pick(
        reg.columns,
        "Name", "Device Name", "Plant Name", "Project Name"
    )
    reg_id_col = pick(reg.columns, "Device ID", "Plant ID")

    # --- normalize keys ---
    sales["__clean_name__"] = sales[sales_name_col].apply(normalize)
    reg["__clean_name__"] = reg[reg_name_col].apply(normalize)

    # --- exact join ---
    right = reg[["__clean_name__", reg_id_col]].rename(columns={reg_id_col: "__id_reg__"})
    joined = sales.merge(right, on="__clean_name__", how="left")

    # Fill only where sales id is empty
    sales_id_series = joined[sales_id_col].astype(str).str.strip()
    reg_id_series = joined["__id_reg__"].astype(str).str.strip()

    needs_fill = sales_id_series.eq("") | sales_id_series.isna()
    has_reg = reg_id_series.ne("") & reg_id_series.notna()

    joined.loc[needs_fill & has_reg, sales_id_col] = joined.loc[needs_fill & has_reg, "__id_reg__"]

    # --- fuzzy for remaining blanks ---
    reg_dict = dict(zip(reg["__clean_name__"], reg[reg_id_col]))
    reg_keys = list(reg_dict.keys())

    still_blank = joined[sales_id_col].astype(str).str.strip().eq("")
    fuzzy_log: list[Tuple[str, str]] = []

    for idx in joined[still_blank].index:
        cname = joined.at[idx, "__clean_name__"]
        best = find_best_match(cname, reg_keys, threshold=fuzzy_threshold)
        if best:
            joined.at[idx, sales_id_col] = reg_dict.get(best, "")
            sales_name = joined.at[idx, sales_name_col]
            reg_name = reg.loc[reg["__clean_name__"] == best, reg_name_col].iloc[0]
            fuzzy_log.append((str(sales_name), str(reg_name)))

    # --- output: normalize column names back to expected ---
    df_out = joined.drop(columns=["__clean_name__", "__id_reg__"], errors="ignore")

    # Standardize to 'Device Name' and 'Device ID' for downstream steps
    if sales_name_col != "Device Name":
        df_out.rename(columns={sales_name_col: "Device Name"}, inplace=True)

    if sales_id_col != "Device ID":
        df_out.rename(columns={sales_id_col: "Device ID"}, inplace=True)

    # --- unmatched report ---
    nonblank_name = df_out["Device Name"].astype(str).str.strip().ne("")
    no_id = df_out["Device ID"].astype(str).str.strip().eq("")
    mask_unmatched = nonblank_name & no_id

    cols = ["Device Name"]
    if "Sl" in df_out.columns:
        cols = ["Sl", "Device Name"]

    unmatched = df_out.loc[mask_unmatched, cols].copy()

    return df_out, fuzzy_log, unmatched
