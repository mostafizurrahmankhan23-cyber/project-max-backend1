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
from m1_pipeline.device_status import build_device_status
from m1_pipeline.issuance_status import build_issuance_status
from m1_pipeline.cost_redemption import attach_redemption_cost
from m1_pipeline.transfer_vintage import attach_transfer_vintage
from m1_pipeline.device_wise_sales import fill_device_wise_sales
from m1_pipeline.cogs import compute_cogs


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

@app.get("/")
def root():
    # Used by Render's health check
    return {"status": "ok", "service": "project-max-backend"}


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
# 1b) PDFs + I/S master → updated I/S.xlsx
# ============================================================
from openpyxl import load_workbook

@app.post("/process-pdfs/is-xlsx")
async def process_pdfs_is_xlsx(
    files: List[UploadFile] = File(...),
    is_file: UploadFile = File(...)
):
    """
    Take certificate PDFs + an existing I/S workbook.
    Fill the 'Redemption Status' sheet and return XLSX.
    """
    import tempfile, os
    from io import BytesIO

    # --- Save and parse PDFs (same logic as /process-pdfs)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = []
            for f in files:
                path = os.path.join(tmpdir, f.filename)
                with open(path, "wb") as out:
                    out.write(await f.read())
                paths.append(path)

            all_devices, all_certs = [], []
            for pdf in paths:
                d, c = parse_one_pdf(pdf)
                base_index = len(all_devices)
                for row in c:
                    row["Device Index"] = base_index + row["Device Index"]
                all_devices.extend(d)
                all_certs.extend(c)

        df_devices = pd.DataFrame(all_devices, columns=LABELS_FLAT + ["Source PDF", "Client Name"])
        cert_cols = [
            "Device Index","From Certificate ID","To Certificate ID",
            "Number of Certificates","Offset Attributes","Period of Production",
            "Issuer","Source PDF","Client Name",
        ]
        df_certs = pd.DataFrame(all_certs, columns=cert_cols)

        df_devices_for_merge = df_devices[LABELS_FLAT + ["Source PDF", "Client Name"]]
        df_devices_for_merge = df_devices_for_merge.rename(columns={"Source PDF": "Device Source PDF"})

        df_merged = pd.merge(
            df_certs, df_devices_for_merge,
            left_on="Device Index", right_index=True,
            how="left"
        ).drop(columns=["Device Index"])

        df_merged2 = df_merged.copy()
        df_merged2[["Start Date","End Date"]] = df_merged2["Period of Production"].str.split(" - ", expand=True)

        df_sales = build_sales_dataframe(df_merged2)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF parsing failed: {e}")

    # --- Load I/S workbook and overwrite Redemption Status sheet
    try:
        workbook_bytes = await is_file.read()
        wb = load_workbook(BytesIO(workbook_bytes))

        sheet_name = "Redemption Status"
        if sheet_name not in wb.sheetnames:
            raise HTTPException(status_code=400, detail="I/S workbook missing Redemption Status sheet")

        ws = wb[sheet_name]

        # Clear rows but keep headers
        if ws.max_row > 1:
            ws.delete_rows(2, ws.max_row - 1)

        # Write results starting row 2
        for r_idx, row in enumerate(df_sales.itertuples(index=False), start=2):
            for c_idx, value in enumerate(row, start=1):
                ws.cell(row=r_idx, column=c_idx, value=value)

        out = BytesIO()
        wb.save(out)
        out.seek(0)

        return StreamingResponse(
            out,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="IS_with_redemption.xlsx"'}
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update I/S workbook: {e}")



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




@app.post("/m1/device-status/json")
async def device_status_json(
    devices_file: UploadFile = File(..., description="Device Registration Excel"),
    vendor_file: UploadFile = File(..., description="Vendor Payment Excel"),
):
    try:
        df_devices = pd.read_excel(devices_file.file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read devices_file: {e}")

    try:
        df_vendor = pd.read_excel(vendor_file.file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read vendor_file: {e}")

    try:
        out = build_device_status(df_devices, df_vendor, selling_price=4.5)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DeviceStatus error: {e}")

    return JSONResponse({"rows": out.to_dict(orient="records")})


@app.post("/m1/device-status/xlsx")
async def device_status_xlsx(
    devices_file: UploadFile = File(..., description="Device Registration Excel"),
    vendor_file: UploadFile = File(..., description="Vendor Payment Excel"),
):
    try:
        df_devices = pd.read_excel(devices_file.file)
        df_vendor = pd.read_excel(vendor_file.file)
        out = build_device_status(df_devices, df_vendor, selling_price=4.5)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DeviceStatus error: {e}")

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        out.to_excel(writer, sheet_name="DeviceStatus", index=False)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="device_status.xlsx"'},
    )



@app.post("/m1/issuance-status/json")
async def issuance_status_json(
    issuance_file: UploadFile = File(..., description="Device Issuance Status Excel"),
):
    try:
        df_src = pd.read_excel(issuance_file.file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read issuance_file: {e}")

    try:
        out = build_issuance_status(df_src)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"IssuanceStatus error: {e}")

    return JSONResponse({"rows": out.to_dict(orient="records")})


@app.post("/m1/issuance-status/xlsx")
async def issuance_status_xlsx(
    issuance_file: UploadFile = File(..., description="Device Issuance Status Excel"),
):
    try:
        df_src = pd.read_excel(issuance_file.file)
        out = build_issuance_status(df_src)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"IssuanceStatus error: {e}")

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        out.to_excel(writer, sheet_name="IssuanceStatus", index=False)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="issuance_status.xlsx"'},
    )



@app.post("/m1/cost-redemption/json")
async def cost_redemption_json(
    device_status_file: UploadFile = File(...),
    redemption_file: UploadFile = File(...),
):
    try:
        df_dev = pd.read_excel(device_status_file.file)
        df_red = pd.read_excel(redemption_file.file)
        df_out, stats = attach_redemption_cost(df_dev, df_red)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return JSONResponse({
        "rows": df_out.to_dict(orient="records"),
        "stats": stats,
    })

@app.post("/m1/cost-redemption/xlsx")
async def cost_redemption_xlsx(
    device_status_file: UploadFile = File(..., description="Device Status Excel"),
    redemption_file: UploadFile = File(..., description="Redemption Status Excel"),
):
    """
    Return Redemption Status Excel with 'Cost of MWh' filled
    using Device Status (Plant Owner's % × Selling Price).
    """
    try:
        df_dev = pd.read_excel(device_status_file.file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read device_status_file: {e}")

    try:
        df_red = pd.read_excel(redemption_file.file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read redemption_file: {e}")

    try:
        df_out, stats = attach_redemption_cost(df_dev, df_red)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"CostRedemption error: {e}")

    # Write to in-memory Excel
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_out.to_excel(writer, sheet_name="Redemption Status", index=False)
        # optional debug sheet with stats
        pd.DataFrame([stats]).to_excel(writer, sheet_name="Stats", index=False)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition":
            'attachment; filename="redemption_with_cost_of_mwh.xlsx"'
        },
    )




@app.post("/m1/transfer-status/xlsx")
async def transfer_status_vintage(
    file: UploadFile = File(..., description="Transfer Status Excel"),
):
    try:
        df_transfer = pd.read_excel(file.file, sheet_name="Transfer Status")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read Excel: {e}")

    # Adjust these names to your real headers in that sheet
    df_out = attach_transfer_vintage(
        df_transfer,
        in_start_col="IN Period Starts",
        in_end_col="IN Period Ends",
        out_start_col="OUT Period Starts",
        out_end_col="OUT Period Ends",
        in_vintage_col="Vintage IN",
        out_vintage_col="Vintage OUT",
        add_flags=False,
    )

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_out.to_excel(writer, sheet_name="Transfer Status", index=False)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="transfer_status_with_vintage.xlsx"'},
    )


@app.post("/m1/device-wise-sales/xlsx")
async def device_wise_sales_full_xlsx(
    issuance_file: UploadFile = File(..., description="Issuance Status Excel"),
    device_file: UploadFile = File(...,   description="Device Wise Sales Status Excel"),
    redemption_file: UploadFile = File(..., description="Redemption Status Excel"),
    transfer_file: UploadFile = File(...,   description="Transfer Status Excel"),
):
    try:
        issuance_df   = pd.read_excel(issuance_file.file)
        device_df     = pd.read_excel(device_file.file)
        redemption_df = pd.read_excel(redemption_file.file)
        transfer_df   = pd.read_excel(transfer_file.file)

        df_out = fill_device_wise_sales(
            device_df,
            issuance_df,
            redemption_df=redemption_df,
            transfer_df=transfer_df,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_out.to_excel(writer, sheet_name="Device Wise Sales Status", index=False)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="device_wise_sales_with_issued_sold.xlsx"'
        },
    )



@app.post("/m1/cogs/xlsx")
async def process_cogs_xlsx(
    cogs_file: UploadFile = File(..., description="COGS Excel"),
    sales_file: UploadFile = File(..., description="Device Wise Sales Status Excel"),
):
    """
    Upload:
      - COGS Excel
      - Device Wise Sales Status Excel

    Get back: COGS Excel with Registration Cost, Issued/Sold IREC,
              Issuance Cost, Redemption Cost filled.
    """
    try:
        cogs_df = pd.read_excel(cogs_file.file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read cogs_file: {e}")

    try:
        sales_df = pd.read_excel(sales_file.file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read sales_file: {e}")

    try:
        merged = compute_cogs(cogs_df, sales_df)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"COGS computation error: {e}")

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        merged.to_excel(writer, sheet_name="COGS", index=False)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="COGS_with_costs.xlsx"'},
    )
