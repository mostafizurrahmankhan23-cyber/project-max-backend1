# m1_pipeline/cost_redemption.py

from __future__ import annotations

import re
import pandas as pd
from typing import Tuple, Dict


# ---------- helpers ----------

def normalize_id(s: str) -> str:
    """
    Make IDs comparable:

    - cast to string, upper-case
    - strip spaces
    - remove all whitespace
    - keep only letters, digits and dot (.)
    """
    if s is None:
        return ""
    s = str(s).upper().strip()
    s = re.sub(r"\s+", "", s)          # remove all whitespace
    s = re.sub(r"[^A-Z0-9\.]", "", s)  # keep A-Z, 0-9 and '.'
    return s


def pick(colnames, *candidates) -> str:
    """
    Find the first column whose name matches any of the `candidates`
    (case-insensitive). Raises KeyError if none are found.
    """
    lower_map = {str(c).lower(): c for c in colnames}
    for cand in candidates:
        key = str(cand).lower()
        if key in lower_map:
            return lower_map[key]
    raise KeyError(f"Could not find any of {candidates} in columns: {list(colnames)}")


# ---------- core function ----------

def attach_redemption_cost(
    df_device_status: pd.DataFrame,
    df_redemption: pd.DataFrame,
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """
    Attach 'Cost of MWh' to Redemption Status by matching IDs.

    Inputs
    ------
    df_device_status : DataFrame
        Device Status sheet, must contain:
          - Plant ID column   (e.g. 'Plant ID')
          - Cost of MWh column (e.g. 'Cost of MWh')
    df_redemption : DataFrame
        Redemption Status sheet, must contain:
          - Device ID column   (e.g. 'Device ID')
        May or may not already contain a 'Cost of MWh' column.

    Returns
    -------
    df_out : DataFrame
        df_redemption copy with 'Cost of MWh' filled/updated.
    stats : dict
        {"hits": <matched rows>, "misses": <unmatched rows>}
    """

    dev = df_device_status.copy()
    red = df_redemption.copy()

    # --- find relevant columns (robust to header variations) ---
    dev_col_pid = pick(dev.columns, "Plant ID", "Plant Id", "Plant_ID", "Device ID")
    dev_col_cost = pick(dev.columns, "Cost of MWh", "Cost/MWh", "Cost_MWh")

    red_col_did = pick(red.columns, "Device ID", "Plant ID", "Plant Id", "Plant_ID")

    # ensure Redemption has a Cost-of-MWh column
    if any(name.lower() == "cost of mwh" for name in red.columns):
        red_col_cost = next(
            c for c in red.columns if str(c).lower() == "cost of mwh"
        )
    else:
        red_col_cost = "Cost of MWh"
        red[red_col_cost] = ""

    # --- build lookup: Plant ID → Cost of MWh ---
    pid_to_cost = {}
    for _, row in dev.iterrows():
        pid = normalize_id(row.get(dev_col_pid, ""))
        if not pid:
            continue
        cost = row.get(dev_col_cost, "")
        pid_to_cost[pid] = cost  # last one wins; adjust if needed

    # --- fill Cost of MWh in Redemption Status ---
    hits = 0
    misses = 0
    costs = []

    for _, row in red.iterrows():
        device_id = normalize_id(row.get(red_col_did, ""))
        cost = pid_to_cost.get(device_id, "")
        if cost != "":
            hits += 1
        else:
            misses += 1
        costs.append(cost)

    red[red_col_cost] = costs

    stats = {"hits": hits, "misses": misses}
    return red, stats
