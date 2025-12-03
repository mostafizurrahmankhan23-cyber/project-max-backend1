# app/main.py

from typing import List
import os
import tempfile

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

from .core import parse_one_pdf, parse_pdf_folder, build_sales_dataframe

app = FastAPI(
    title="Project Max Certificates API",
    version="0.1.0",
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/process-pdfs")
async def process_pdfs(files: List[UploadFile] = File(...)):
    """
    Accept multiple PDF files, run your full pipeline, and return
    the final 'sales' table as JSON.
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

            # Instead of parse_pdf_folder(pdf_dir), we reuse parse_one_pdf on each
            all_devices, all_certs = [], []
            for pdf in paths:
                d, c = parse_one_pdf(pdf)
                base_index_offset = len(all_devices)
                for row in c:
                    row["Device Index"] = base_index_offset + row["Device Index"]
                all_devices.extend(d)
                all_certs.extend(c)

        # Build DataFrames similar to parse_pdf_folder
        import pandas as pd
        from .core import LABELS_FLAT

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

        df_merged = pd.merge(
            df_certs,
            df_devices_for_merge,
            left_on="Device Index", right_index=True, how="left",
            suffixes=("_cert", "_dev")
        ).drop(columns=["Device Index"])

        df_merged2 = df_merged.copy()
        df_merged2[["Start Date", "End Date"]] = df_merged2["Period of Production"].str.split(" - ", expand=True)

        df_sales = build_sales_dataframe(df_merged2)

        return JSONResponse(content=df_sales.to_dict(orient="records"))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
