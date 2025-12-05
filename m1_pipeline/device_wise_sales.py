# m1_pipeline/device_wise_sales.py

from __future__ import annotations
from typing import Dict, Tuple, Optional

import re
import pandas as pd
from unidecode import unidecode


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _norm(s: object) -> str:
    """Normalize text for matching."""
    return unidecode(str(s).strip())


def _norm_vintage(s: object) -> str:
    """
    Normalize vintage tokens like 'V24 Q3' or 'v24q3'
    -> 'V24Q3'.
    """
    s_norm = _norm(s).upper()
    m = re.search(r"(V\d{2}\s*Q\d)", s_norm)
    if not m:
        return ""
    return m.group(1).replace(" ", "")


def _to_num(x: object) -> float:
    """Safe numeric conversion; invalid -> 0.0."""
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return 0.0


def _pick(colnames, *cands) -> str:
    """
    Pick a column name from a list of candidates (case-insensitive).
    Raises KeyError if none found.
    """
    lc_map = {c.lower(): c for c in colnames}
    for c in cands:
        if c.lower() in lc_map:
            return lc_map[c.lower()]
    raise KeyError(f"Could not find any of {cands} in columns: {list(colnames)}")


def _extract_vintage_from_header(header: str, keyword: str) -> Optional[str]:
    """
    From a column header like 'V24 Q3 Issued' or 'Issued V24Q3',
    extract 'V24Q3'. Returns None if not found or if keyword is absent.
    """
    if keyword.lower() not in header.lower():
        return None
    m = re.search(r"(V\d{2}\s*Q\d)", header, flags=re.I)
    if not m:
        return None
    return m.group(1).replace(" ", "").upper()


# -------------------------------------------------------------------
# Core function
# -------------------------------------------------------------------

def fill_device_wise_sales(
    device_df: pd.DataFrame,
    issuance_df: pd.DataFrame,
    redemption_df: pd.DataFrame | None = None,
    transfer_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Replicate full M-1_DeviceWiseSalesStatus_VQ logic in pure pandas.

    1) Fills all '…Issued' columns in `device_df` using `issuance_df`
       (Issuance Status):
          Issued[VxxQy] = SUM MWh over Issuance
                          grouped by (Device ID, Vintage).

    2) If BOTH `redemption_df` and `transfer_df` are provided:
       fills all '…Sold' columns in `device_df` as:
          Sold[VxxQy] = SUM_Redemption(Number of Certificate)
                        + SUM_Transfer_OUT(MWh)
       grouped by (Plant ID = Device ID, Vintage).

    Parameters
    ----------
    device_df      : Device Wise Sales Status sheet
    issuance_df    : Issuance Status sheet
    redemption_df  : Redemption Status sheet (optional)
    transfer_df    : Transfer Status sheet (optional)

    Returns
    -------
    pd.DataFrame with Issued/Sold vintage columns updated.
    """
    dev = device_df.copy()

    # ---------------------------------------------------------------
    # Part A: "Issued" vintage columns (Issuance Status)
    # ---------------------------------------------------------------
    # Normalise Issuance columns
    iss = issuance_df.copy()

    dev_id_col = _pick(iss.columns, "Device ID", "Plant ID")
    vint_col   = _pick(iss.columns, "Vintage")
    mwh_col    = _pick(iss.columns, "MWh")

    iss["_dev"]     = iss[dev_id_col].map(_norm)
    iss["_vintage"] = iss[vint_col].map(_norm_vintage)
    iss["_mwh"]     = pd.to_numeric(iss[mwh_col], errors="coerce").fillna(0.0)

    grp_iss = (
        iss.groupby(["_dev", "_vintage"])["_mwh"]
        .sum()
        .reset_index()
    )
    issued_lookup: Dict[Tuple[str, str], float] = {
        (row["_dev"], row["_vintage"]): float(row["_mwh"])
        for _, row in grp_iss.iterrows()
    }

    # Normalise Plant ID in device sheet
    plant_col = _pick(dev.columns, "Plant ID")
    dev["_plant"] = dev[plant_col].map(_norm)

    # Identify Issued columns and fill them
    issued_cols = [c for c in dev.columns if "issued" in c.lower()]
    for col in issued_cols:
        vint = _extract_vintage_from_header(col, "issued")
        if not vint:
            continue  # skip weird headers

        vals = []
        for pid in dev["_plant"]:
            v = issued_lookup.get((pid, vint), 0.0)
            vals.append("" if abs(v) < 1e-12 else v)
        dev[col] = vals

    # ---------------------------------------------------------------
    # Part B: "Sold" vintage columns
    #         (Redemption Status + Transfer Status OUT)
    # ---------------------------------------------------------------
    if redemption_df is not None and transfer_df is not None:
        # ----- Redemption sums (Device ID, Vintage) -> Number of Certificate
        red = redemption_df.copy()
        red_dev_col = _pick(red.columns, "Device ID", "Plant ID")
        red_vint_col = _pick(red.columns, "Vintage")
        red_num_col = _pick(
            red.columns,
            "Number of Certificate",
            "Number of Certificates",
            "Number of Certficate",
        )

        red["_dev"]     = red[red_dev_col].map(_norm)
        red["_vintage"] = red[red_vint_col].map(_norm_vintage)
        red["_num"]     = red[red_num_col].map(_to_num)

        grp_red = (
            red.groupby(["_dev", "_vintage"])["_num"]
            .sum()
            .reset_index()
        )
        sum_red: Dict[Tuple[str, str], float] = {
            (row["_dev"], row["_vintage"]): float(row["_num"])
            for _, row in grp_red.iterrows()
        }

        # ----- Transfer OUT sums (Plant ID, Vintage) -> MWh OUT
        tran = transfer_df.copy()

        # In your original sheet the OUT table is the second set of columns,
        # so after reading with pandas you will typically have something like
        #   'Plant ID', 'Period Starts', 'Period Ends', 'Vintage', 'MWh',
        #   'Plant ID.1', 'Period Starts.1', ...
        # We always take the *last* matching set to approximate the OUT table.
        tran_plant_cols = [c for c in tran.columns if "plant" in c.lower() and "id" in c.lower()]
        tran_vint_cols  = [c for c in tran.columns if "vintage" in c.lower()]
        tran_mwh_cols   = [c for c in tran.columns if "mwh" in c.lower()]

        if not (tran_plant_cols and tran_vint_cols and tran_mwh_cols):
            raise KeyError(
                "Transfer Status sheet must contain Plant ID / Vintage / MWh "
                "columns (OUT table)."
            )

        t_plant_col = tran_plant_cols[-1]   # OUT side
        t_vint_col  = tran_vint_cols[-1]
        t_mwh_col   = tran_mwh_cols[-1]

        tran["_plant"]  = tran[t_plant_col].map(_norm)
        tran["_vintage"] = tran[t_vint_col].map(_norm_vintage)
        tran["_mwh"]    = tran[t_mwh_col].map(_to_num)

        grp_out = (
            tran.groupby(["_plant", "_vintage"])["_mwh"]
            .sum()
            .reset_index()
        )
        sum_out: Dict[Tuple[str, str], float] = {
            (row["_plant"], row["_vintage"]): float(row["_mwh"])
            for _, row in grp_out.iterrows()
        }

        # ----- Fill “…Sold” columns in device sheet
        sold_cols = []
        for j, name in enumerate(dev.columns):
            if "sold" in name.lower():
                m = re.search(r"(V\d{2}\s*Q\d)", name, flags=re.I)
                if m:
                    vint = m.group(1).replace(" ", "").upper()
                    sold_cols.append((name, vint))

        if not sold_cols:
            # Nothing to do; keep Issued only
            pass
        else:
            for col_name, vint in sold_cols:
                vals = []
                for pid in dev["_plant"]:
                    total = 0.0
                    total += sum_red.get((pid, vint), 0.0)
                    total += sum_out.get((pid, vint), 0.0)
                    vals.append("" if abs(total) < 1e-12 else total)
                dev[col_name] = vals

    # Remove helper column
    if "_plant" in dev.columns:
        dev.drop(columns=["_plant"], inplace=True)

    return dev
