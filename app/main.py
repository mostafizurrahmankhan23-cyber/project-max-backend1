# app/main.py

from __future__ import annotations

from typing import List
import os
import tempfile
from io import BytesIO

import pandas as pd
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from .core import parse_one_pdf, LABELS_FLAT, build_sales_dataframe
from m1_pipeline.device_id import attach_device_ids

app = FastAPI(
    title="Project Max Certificates API",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # you can restrict later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


# ============================================================
# 1) PDF → Redemption Status (your existing logic)
# ============================================================
@app.post("/process-pdfs")
async def process_pdfs(files: List[UploadFile] = File(...)):
    """
    Accept multiple certificate PDFs, parse them, and return
    the final 'Redemption Status'-style table as JSON.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = []
            for f in files:
                path = os.path.join(tmpdir, f.filename)
                with open(path, "wb") as out:
                    out.write(await f.read())
                paths.append(path)

            # Use parse_one_pdf for each uploaded file
            all_devices, all_certs = [], []
            for pdf in paths:
                d, c = parse_one_pdf(pdf)
                base_index_offset = len(all_devices)
                for row in c:
                    row["Device Index"] = base_index_offset + row["Device Index"]
                all_devices.extend(d)
                all_certs.extend(c)

        # Build DataFrames (similar to parse_pdf_folder)
        df_devices = pd.DataFrame(
            all_devices,
            columns=LABELS_FLAT + ["Source PDF", "Client Name"],
        )
        cert_cols = [
            "Device Index",
            "From Certificate ID",
            "To Certificate ID",
            "Number of Certificates",
            "Offset Attributes",
            "Period of Production",
            "Issuer",
            "Source PDF",
            "Client Name",
        ]
        df_certs = pd.DataFrame(all_certs, columns=cert_cols)

        df_devices_for_merge = (
            df_devices[LABELS_FLAT + ["Source PDF", "Client Name"]]
            .rename(columns={"Source PDF": "Device Source PDF"})
        )

        df_merged = pd.merge(
            df_certs,
            df_devices_for_merge,
            left_on="Device Index",
            right_index=True,
            how="left",
            suffixes=("_cert", "_dev"),
        ).drop(columns=["Device Index"])

        df_merged2 = df_merged.copy()
        df_merged2[["Start Date", "End Date"]] = df_merged2[
            "Period of Production"
        ].str.split(" - ", expand=True)

        df_sales = build_sales_dataframe(df_merged2)

        return JSONResponse(content=df_sales.to_dict(orient="records"))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 2) M-1 DeviceID: attach Device IDs from Excel files
# ============================================================

@app.post("/m1/device-id/json")
async def process_device_id_json(
    sales_file: UploadFile = File(..., description="Redemption Status Excel"),
    registry_file: UploadFile = File(..., description="Device Registration Excel"),
):
    """
    Return Redemption+DeviceID as JSON (for table display in frontend).
    """
    try:
        df_sales = pd.read_excel(sales_file.file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read sales_file: {e}")

    try:
        df_devreg = pd.read_excel(registry_file.file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read registry_file: {e}")

    try:
        df_out, fuzzy_log, unmatched = attach_device_ids(df_sales, df_devreg)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Matching error: {e}")

    return JSONResponse(
        {
            "rows": df_out.to_dict(orient="records"),
            "fuzzy_log": fuzzy_log,
            "unmatched": unmatched.to_dict(orient="records"),
        }
    )


@app.post("/m1/device-id/xlsx")
async def process_device_id_xlsx(
    sales_file: UploadFile = File(..., description="Redemption Status Excel"),
    registry_file: UploadFile = File(..., description="Device Registration Excel"),
):
    """
    Return Redemption+DeviceID as a downloadable Excel file.
    """
    try:
        df_sales = pd.read_excel(sales_file.file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read sales_file: {e}")

    try:
        df_devreg = pd.read_excel(registry_file.file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read registry_file: {e}")

    try:
        df_out, fuzzy_log, unmatched = attach_device_ids(df_sales, df_devreg)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Matching error: {e}")

    # Write to in-memory Excel
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_out.to_excel(writer, sheet_name="Redemption+DeviceID", index=False)
        if len(fuzzy_log) > 0:
            pd.DataFrame(fuzzy_log, columns=["Sales Name", "Registry Name"]).to_excel(
                writer, sheet_name="FuzzyMatches", index=False
            )
        if not unmatched.empty:
            unmatched.to_excel(writer, sheet_name="Unmatched", index=False)

    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": 'attachment; filename="redemption_with_device_ids.xlsx"'
        },
    )
