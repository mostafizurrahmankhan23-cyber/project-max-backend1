# app/core.py

import os
import re
import glob
from datetime import datetime

import numpy as np
import pandas as pd
from pdfminer.high_level import extract_text

# ---------- CONFIG-LIKE CONSTANTS (you can override in caller) ----------

LABELS_FLAT = [
    "Device", "Country of Origin", "Energy Source",
    "Technology", "Supported", "Commissioning Date",
    "Carbon (CO2 / MWh)"
]

MONTHS = {
    'jan':1,'january':1,'feb':2,'february':2,'mar':3,'march':3,'apr':4,'april':4,
    'may':5,'jun':6,'june':6,'jul':7,'july':7,'aug':8,'august':8,'sep':9,'sept':9,'september':9,
    'oct':10,'october':10,'nov':11,'november':11,'dec':12,'december':12
}

date_re = re.compile(r'(?i)\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b')
seq_re  = re.compile(r'(\d+)\s*(?=\.pdf$)', re.I)   # e.g., "... 1.pdf" or "... 2.PDF"


# ---------- UTIL ----------

def _q(dt: pd.Timestamp) -> int:
    return (dt.month - 1) // 3 + 1

def get_vintage(start_date, end_date) -> str:
    s = pd.to_datetime(start_date, errors="coerce")
    e = pd.to_datetime(end_date,   errors="coerce")
    if pd.isna(s) or pd.isna(e):
        return ""
    yy = s.year % 100
    qs, qe = _q(s), _q(e)
    q = qs if qs != qe else qe
    return f"V{yy:02d}Q{q}"

def join_digits(s: str) -> str:
    return re.sub(r'(?<=\d)\s+(?=\d)', '', str(s))

def next_nonempty(lines, i: int) -> int:
    while i < len(lines) and (not lines[i].strip() or lines[i].strip() == "\f"):
        i += 1
    return i

def is_header_line(s: str) -> bool:
    s = s.strip().lower()
    return s in {
        "production device details","redeemed certificates","device",
        "country of","origin","energy","source","technology",
        "supported","commissioning","date","carbon (co2","/ mwh)",
        "from certificate id","to certificate id",
        "number of certificates","offset attributes",
        "period of production","issuer"
    }

def strip_leading_noise(s: str) -> str:
    s = re.sub(
        r'^\s*(?:Carbon\s*)?(?:\(\s*)?CO2\s*/\s*(?:MWh\s*)?\)?\s*',
        '',
        s,
        flags=re.I
    )
    s = re.sub(r'^\s*/\s*MWh\)\s*', '', s, flags=re.I)
    s = re.sub(r'^\s*MWh\)\s*', '', s, flags=re.I)
    return s

def norm(s: str) -> str:
    s = (s or "")
    s = strip_leading_noise(s)
    s = s.replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    s = s.replace("\ufb01", "fi").replace("\ufb02", "fl")
    return " ".join(s.split()).strip()

def filename_sort_key(path: str):
    base = os.path.basename(path)
    # 1) date
    m = date_re.search(base)
    if m:
        day  = int(m.group(1))
        mon  = MONTHS.get(m.group(2).lower(), 1)
        year = int(m.group(3))
        try:
            d = datetime(year, mon, day)
        except ValueError:
            d = datetime.max
    else:
        d = datetime.max

    # 2) optional trailing sequence
    sm = seq_re.search(base)
    seq = int(sm.group(1)) if sm else 0

    return (d, seq, base.lower())


# ---------- CORE PARSING: ONE PDF ----------

def parse_one_pdf(pdf_path: str, save_debug_txt: bool = False, debug_dir: str = "."):
    """
    Parse a single certificate PDF.
    Returns:
        devices: list[dict]
        certs:   list[dict]
    """
    text = extract_text(pdf_path)

    if save_debug_txt:
        base = os.path.splitext(os.path.basename(pdf_path))[0]
        raw_txt   = os.path.join(debug_dir, f"{base}_raw.txt")
        clean_txt = os.path.join(debug_dir, f"{base}_clean.txt")
        with open(raw_txt, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        clean_txt = None

    lines = [ln.replace("\u00a0", " ").replace("\uf0b7", " ").strip()
             for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("\f")]

    # --- Client Name ---
    client_name = ""
    pf_idx = next((k for k, ln in enumerate(lines) if "produced for" in ln.lower()), None)
    if pf_idx is not None:
        j = next_nonempty(lines, pf_idx + 1)
        if j is not None and j < len(lines):
            client_name = lines[j].strip()

    if not client_name:
        m = re.search(r"produced\s+for\s*(.+)", text, flags=re.I)
        if m:
            candidate = m.group(1).strip()
            client_name = candidate.splitlines()[0].strip()

    devices, certs, out_lines = [], [], []
    i, device_idx = 0, -1
    total = len(lines)

    COUNTRIES = {"Bangladesh","India","China","USA","United States","Pakistan","Sri Lanka"}

    while i < total:
        tok = lines[i].strip()

        if tok.lower() == "production device details":
            out_lines += ["", "="*60, "Production Device Details", "="*60]
            i += 1

            hdr_tokens = {"device","country of","origin","energy","source","technology",
                          "supported","commissioning","date","carbon (co2","/ mwh)"}
            while i < total and lines[i].strip().lower() in hdr_tokens:
                i += 1

            STOP_SECTIONS = {"production device details", "redeemed certificates"}

            POWER_RE = re.compile(
                r"\b\d{1,5}(?:[.,]\d+)?\s*(?:kW|KW|kWp|KWp|MW|MWp|G W|GW|GWp|M W|KW DC|kW DC|KW AC|kW AC)\b",
                re.IGNORECASE
            )

            # -------- device name extraction (same logic as your Colab script) --------
            device_lines = []

            while i < total:
                s_raw = lines[i]
                s = norm(s_raw)
                if not s:
                    i += 1
                    continue

                if s.lower() in STOP_SECTIONS:
                    i += 1
                    continue

                if s in COUNTRIES:
                    break

                s1 = s
                s2 = norm(lines[i+1]) if i+1 < total else ""
                s3 = norm(lines[i+2]) if i+2 < total else ""

                combo  = f"{s1} {s2}".strip()
                combo2 = f"{combo} {s3}".strip()

                if re.search(r"\d+(?:\.\d+)?\s*(?:kW|kWp|MWp|MW)\b.*\b(Solar|Rooftop|Project)\b", combo2, re.I):
                    device_lines.append(s1)
                    j = i + 1
                    while j < total:
                        nxt = norm(lines[j])
                        if nxt in COUNTRIES or nxt.lower() in STOP_SECTIONS:
                            break
                        if nxt:
                            device_lines.append(nxt)
                        j += 1
                    i = j
                    break

                if re.search(r"\bsolar\b", s, re.I) and not re.search(r"redeemed|production", s, re.I):
                    device_lines.append(s1)
                    if i+1 < total and len(norm(lines[i+1]).split()) <= 3:
                        device_lines.append(norm(lines[i+1]))
                        i += 1
                    i += 1
                    break

                i += 1

            device_name = " ".join(device_lines).strip()

            # Country
            country = ""
            if i < total and lines[i].strip() in COUNTRIES:
                country = lines[i].strip()
                i += 1

            # Energy
            i = next_nonempty(lines, i)
            energy = ""
            if i < total and re.match(r"^(Solar|Wind|Hydro|Biomass|Geo|Tidal|PV)$", lines[i], re.I):
                energy = lines[i].strip()
                i += 1

            # Technology
            tech_lines = []
            while i < total:
                s = lines[i].strip()
                if s in {"Yes","No"} or "redeemed certificates" in s.lower():
                    break
                if not is_header_line(s):
                    tech_lines.append(s)
                i += 1
            technology = " ".join(tech_lines).strip()

            # Supported
            supported = ""
            if i < total and lines[i].strip() in {"Yes","No"}:
                supported = lines[i].strip()
                i += 1

            # Commissioning Date
            i = next_nonempty(lines, i)
            date_val = ""
            if i < total and re.match(r"^\d{4}-\d{2}-\d{2}$", lines[i]):
                date_val = lines[i].strip()
                i += 1

            # Carbon
            i = next_nonempty(lines, i)
            carbon = ""
            if i < total and re.match(r"^[+-]?\d+(?:\.\d+)?$", join_digits(lines[i])):
                carbon = join_digits(lines[i].strip())
                i += 1

            dev = {
                "Device": device_name,
                "Country of Origin": country,
                "Energy Source": energy,
                "Technology": technology,
                "Supported": supported,
                "Commissioning Date": date_val,
                "Carbon (CO2 / MWh)": carbon,
                "Source PDF": os.path.basename(pdf_path),
                "Client Name": client_name,
            }
            devices.append(dev)
            device_idx = len(devices) - 1

            for k in LABELS_FLAT:
                out_lines.append(f"{k}: {dev.get(k,'')}")
            out_lines += ["="*60, "Redeemed Certificates", "="*60]

            # ==== REDEEMED CERTIFICATES ====
            start_i = i
            block_lines = []
            while start_i < total:
                s = lines[start_i].strip()
                if s.lower() == "production device details":
                    break
                block_lines.append(s)
                start_i += 1

            block_text = "\n".join(block_lines)
            matches = re.finditer(
                r"(?P<from>\d{4}-\d{4}-\d{4}-\d{4}\.\d+)\s+(?P<to>\d{4}-\d{4}-\d{4}-\d{4}\.\d+)\s+"
                r"(?P<num>[0-9\.\s]+)\s+(?P<offset>Incl|Excl)\s+(?P<start>\d{4}-\d{2}-\d{2})\s*-\s*(?P<end>\d{4}-\d{2}-\d{2})",
                block_text, re.I
            )

            count = 0
            for m in matches:
                certs.append({
                    "Device Index": device_idx,
                    "From Certificate ID": m.group("from"),
                    "To Certificate ID": m.group("to"),
                    "Number of Certificates": join_digits(m.group("num")),
                    "Offset Attributes": m.group("offset"),
                    "Period of Production": f"{m.group('start')} - {m.group('end')}",
                    "Issuer": "The Green Certificate Company (Central Issuer)",
                    "Source PDF": os.path.basename(pdf_path),
                    "Client Name": client_name,
                })
                count += 1

            i = start_i
            continue

        i += 1

    if save_debug_txt and clean_txt is not None:
        with open(clean_txt, "w", encoding="utf-8") as f:
            f.write("\n".join(out_lines).strip() + "\n")

    return devices, certs


# ---------- MULTI-PDF + MERGING ----------

def coalesce(df: pd.DataFrame, base: str) -> pd.DataFrame:
    """
    Combine multiple variants of a field (base, base_cert, base_dev, base_x, base_y)
    into a single column called `base`.
    Keeps first non-null value and drops the duplicates.
    """
    cands = [base, f"{base}_cert", f"{base}_dev", f"{base}_x", f"{base}_y"]
    present = [c for c in cands if c in df.columns]
    if not present:
        df[base] = ""
        return df

    s = pd.Series(pd.NA, index=df.index)
    for c in present:
        s = s.combine_first(df[c])

    df[base] = s
    for c in present:
        if c != base:
            df.drop(columns=c, inplace=True)
    return df



def parse_pdf_folder(pdf_dir: str):
    """
    Parse all PDFs in a directory and return:
        df_devices, df_certs, df_merged2
    df_merged2 matches the Colab 'with Start/End Dates' version and
    contains a single 'Client Name' column (coalesced).
    """
    pdf_files = sorted(
        [p for p in glob.glob(os.path.join(pdf_dir, "*.pdf")) if os.path.isfile(p)],
        key=filename_sort_key
    )
    if not pdf_files:
        raise FileNotFoundError(f"No PDFs found in: {pdf_dir}")

    all_devices, all_certs = [], []
    for pdf in pdf_files:
        d, c = parse_one_pdf(pdf)
        base_index_offset = len(all_devices)
        # adjust Device Index for cert rows
        for row in c:
            row["Device Index"] = base_index_offset + row["Device Index"]
        all_devices.extend(d)
        all_certs.extend(c)

    # ---------- build device & cert tables ----------
    df_devices = pd.DataFrame(
        all_devices,
        columns=LABELS_FLAT + ["Source PDF", "Client Name"]
    )

    cert_cols = ["Device Index","From Certificate ID","To Certificate ID",
                 "Number of Certificates","Offset Attributes","Period of Production",
                 "Issuer","Source PDF","Client Name"]
    df_certs = pd.DataFrame(all_certs, columns=cert_cols)

    df_devices_for_merge = df_devices[LABELS_FLAT + ["Source PDF","Client Name"]] \
                            .rename(columns={"Source PDF": "Device Source PDF"})

    # ---------- merge (this creates Client Name_cert / Client Name_dev) ----------
    df_merged = pd.merge(
        df_certs,
        df_devices_for_merge,
        left_on="Device Index", right_index=True, how="left",
        suffixes=("_cert", "_dev")
    ).drop(columns=["Device Index"])

    # ---------- COALESCE Client Name exactly like Colab ----------
    df_merged = coalesce(df_merged, "Client Name")

    # ---------- Split Period of Production → Start/End ----------
    df_merged2 = df_merged.copy()
    df_merged2[["Start Date", "End Date"]] = df_merged2["Period of Production"].str.split(" - ", expand=True)

    cols = ['From Certificate ID', 'To Certificate ID', 'Number of Certificates',
            'Offset Attributes', 'Start Date', 'End Date',
            'Issuer', 'Device', 'Country of Origin', 'Energy Source',
            'Technology', 'Supported', 'Commissioning Date', 'Carbon (CO2 / MWh)',
            'Client Name', 'Source PDF', 'Device Source PDF']
    cols = [c for c in cols if c in df_merged2.columns]
    df_merged2 = df_merged2[cols]

    return df_devices, df_certs, df_merged2



def build_sales_dataframe(df_merged2: pd.DataFrame) -> pd.DataFrame:
    """
    Build the final df_sales/df_upload table from df_merged2.
    Ensures 'Client Name' is preserved (coalesced if needed).
    """
    df_final = df_merged2.copy()

    # EXTRA SAFETY: if Client Name somehow came in as _cert/_dev, fix it here too
    df_final = coalesce(df_final, "Client Name")

    rename_map = {
        "Device": "Device Name",
        "Start Date": "Start Date",
        "End Date": "End Date",
        "Number of Certificates": "Number of Certificate",
    }
    available = [k for k in rename_map.keys() if k in df_final.columns]
    df_sales = df_final.rename(columns={k: rename_map[k] for k in available})

    # Only create empty columns if missing – do NOT overwrite existing ones
    for col in ["Device Name","Start Date","End Date","Number of Certificate","Client Name"]:
        if col not in df_sales.columns:
            df_sales[col] = ""

    df_sales["Vintage"] = df_sales.apply(
        lambda row: get_vintage(row["Start Date"], row["End Date"]), axis=1
    )

    source_col = "Source PDF" if "Source PDF" in df_final.columns else (
        "Device Source PDF" if "Device Source PDF" in df_final.columns else None
    )
    df_sales["Source PDF"] = df_final[source_col] if source_col else ""

    df_sales.insert(0, "Sl", range(1, len(df_sales)+1))

    # --- Date from filename ---
    def extract_date_from_filename(fname: str) -> str:
        base = os.path.basename(str(fname))
        m = date_re.search(base)
        if not m:
            return ""
        try:
            day  = int(m.group(1))
            mon  = MONTHS.get(m.group(2).lower(), 1)
            year = int(m.group(3))
            d = datetime(year, mon, day)
            return d.strftime("%Y-%m-%d")
        except Exception:
            return ""

    df_sales["Date"] = df_sales["Source PDF"].apply(extract_date_from_filename)
    df_sales["Device ID"] = ""

    ordered_cols = [
        "Sl", "Date", "Client Name", "Device ID", "Device Name",
        "Start Date", "End Date", "Number of Certificate", "Vintage", "Source PDF"
    ]
    for col in ordered_cols:
        if col not in df_sales.columns:
            df_sales[col] = ""
    df_sales = df_sales[ordered_cols].replace({np.nan: ""})

    return df_sales
