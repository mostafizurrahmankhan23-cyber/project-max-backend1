# app/main.py

from __future__ import annotations

from io import BytesIO

import pandas as pd
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from m1_pipeline.device_id import attach_device_ids


app = FastAPI(
    title="M-1 Device ID API",
    description="Upload two Excel files (Sales + Device Registry) to attach Device IDs.",
    version="1.0.0",
)

# Allow your frontend / local testing to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # tighten later if needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"status": "ok", "message": "M-1 Device ID service is running."}


@app.post("/m1/device-id/json")
async def process_device_id_json(
    sales_file: UploadFile = File(..., description="Redemption Status Excel"),
    registry_file: UploadFile = File(..., description="Device Registration Excel"),
):
    """
    Endpoint that returns the result as JSON (for table display in frontend).
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
    Endpoint that returns the result as a downloadable Excel file.
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
        # Optional: extra sheets for debugging
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
