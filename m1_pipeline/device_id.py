# m1_pipeline/device_id.py

from __future__ import annotations

import re
from typing import List, Tuple

import pandas as pd
from difflib import get_close_matches
from unidecode import unidecode


# -------------------------
# Helper functions
# -------------------------

def normalize(name: str) -> str:
    """
    Clean and normalize names for matching.

    - ASCII fold (unidecode)
    - lowercase
    - remove weird spaces/characters
    - keep letters, digits, spaces, dashes
    """
    if not isinstance(name, str):
        return ""
    name = unidecode(name).lower()
    name = name.replace("\u00a0", " ").replace("\u200b", "").replace("–", "-")
    name = re.sub(r"[^a-z0-9\s\-]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def find_best_match(
    name: str,
    ref_list: List[str],
    threshold: float = 0.85,
) -> str | None:
    """
    Return closest fuzzy match to `name` in `ref_list` using difflib.
    Compares on *normalized* names.
    """
    from difflib import get_close_matches

    name = normalize(name)
    if not name:
        return None
    matches = get_close_matches(name, ref_list, n=1, cutoff=threshold)
    return matches[0] if matches else None


# -------------------------
# Core logic
# -------------------------

def attach_device_ids(
    df_sales: pd.DataFrame,
    df_devreg: pd.DataFrame,
    fuzzy_threshold: float = 0.67,
) -> tuple[pd.DataFrame, list[tuple[str, str]], pd.DataFrame]:
    """
    Core M-1 Device ID logic.

    Parameters
    ----------
    df_sales : DataFrame
        "Redemption Status" table, must contain at least:
            - 'Device Name'
        Optionally:
            - 'Device ID'   (existing IDs; we won't override non-empty)
            - 'Sl'          (for reporting)
    df_devreg : DataFrame
        "Device Registration" table, must contain:
            - 'Name'
            - 'Device ID'
    fuzzy_threshold : float
        Similarity cutoff for fuzzy matching (0..1). Lower -> more aggressive.

    Returns
    -------
    df_out : DataFrame
        Same rows as df_sales, with a *single* 'Device ID' column filled
        using exact + fuzzy matching.
    fuzzy_log : list[(sales_name, registry_name)]
        Pairs of names that were matched via fuzzy matching.
    unmatched : DataFrame
        Subset of df_out rows where Device Name is non-blank but Device ID is
        still empty. Columns: ['Sl','Device Name'] if 'Sl' exists, otherwise
        just ['Device Name'].
    """

    # --- sanity checks ---
    if "Device Name" not in df_sales.columns:
        raise ValueError("df_sales must contain column 'Device Name'.")

    for col in ("Name", "Device ID"):
        if col not in df_devreg.columns:
            raise ValueError("df_devreg must contain columns 'Name' and 'Device ID'.")

    # --- normalize names for exact key join ---
    df_sales = df_sales.copy()
    df_devreg = df_devreg.copy()

    df_sales["clean_name"] = df_sales["Device Name"].apply(normalize)
    df_devreg["clean_name"] = df_devreg["Name"].apply(normalize)

    right = df_devreg[["clean_name", "Device ID"]].rename(
        columns={"Device ID": "Device ID_reg"}
    )

    df_join = df_sales.merge(right, on="clean_name", how="left")

    # existing 'Device ID' (if present) is kept unless registry has a match
    if "Device ID" in df_join.columns:
        df_join["Device ID_final"] = df_join["Device ID_reg"].combine_first(
            df_join["Device ID"]
        )
    else:
        df_join["Device ID_final"] = df_join["Device ID_reg"]

    # --- fuzzy matching for remaining blanks ---
    devreg_dict = dict(zip(df_devreg["clean_name"], df_devreg["Device ID"]))
    registry_keys = list(devreg_dict.keys())

    mask_missing = (
        df_join["Device ID_final"].isna()
        | (df_join["Device ID_final"].astype(str).str.strip() == "")
    )

    fuzzy_log: list[Tuple[str, str]] = []

    for idx in df_join[mask_missing].index:
        cname = df_join.at[idx, "clean_name"]
        best = find_best_match(cname, registry_keys, threshold=fuzzy_threshold)
        if best:
            df_join.at[idx, "Device ID_final"] = devreg_dict.get(best, "")
            sales_name = df_join.at[idx, "Device Name"]
            reg_name = df_devreg.loc[df_devreg["clean_name"] == best, "Name"].iloc[0]
            fuzzy_log.append((sales_name, reg_name))

    # --- final clean 'Device ID' column ---
    df_out = df_join.copy()
    df_out["Device ID"] = df_out["Device ID_final"].fillna("").astype(str)

    # drop helper columns
    df_out.drop(
        columns=["clean_name", "Device ID_reg", "Device ID_final"],
        inplace=True,
        errors="ignore",
    )

    # --- unmatched report ---
    device_id_series = df_out["Device ID"].astype(str)
    no_id = device_id_series.str.strip().eq("")
    nonblank_name = df_out["Device Name"].astype(str).str.strip() != ""
    mask_unmatched = no_id & nonblank_name

    if "Sl" in df_out.columns:
        cols = ["Sl", "Device Name"]
    else:
        cols = ["Device Name"]

    unmatched = df_out.loc[mask_unmatched, cols].copy()

    return df_out, fuzzy_log, unmatched
