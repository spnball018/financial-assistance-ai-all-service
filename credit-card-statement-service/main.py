from fastapi import FastAPI, UploadFile, File
from pydantic import BaseModel
import pandas as pd
import io

app = FastAPI()

class Transaction(BaseModel):
    date: str
    description: str
    amount: float

@app.post("/upload-statement/", response_model=list[Transaction])
async def upload_statement(file: UploadFile = File(...)):
    """
    Uploads a credit card statement, processes it, and returns a list of transactions.
    """
    contents = await file.read()
    df = pd.read_csv(io.BytesIO(contents))

    transactions = []
    for _, row in df.iterrows():
        transactions.append(Transaction(
            date=row['Date'],
            description=row['Description'],
            amount=row['Amount']
        ))

    return transactions


# --- KTC e-Statement PDF (KTC track, docs/ktc-track.md) ---
from datetime import date as _date
from decimal import Decimal as _Decimal
from typing import Optional

from fastapi import Form, HTTPException

import ktc_pdf

MAX_PDF_BYTES = 10 * 1024 * 1024


class KtcStatement(BaseModel):
    statement_date: _date
    due_date: _date
    card_masked: str
    card_last4: str
    previous_balance: _Decimal
    total_new_balance: _Decimal
    minimum_payment: Optional[_Decimal] = None


class KtcLine(BaseModel):
    transaction_date: _date
    posting_date: _date
    description: str
    amount: _Decimal  # signed: payments/refunds are negative
    approval_code: Optional[str] = None
    type: str  # purchase|payment|refund|fee|interest|other


class KtcParseResponse(BaseModel):
    statement: KtcStatement
    lines: list[KtcLine]


@app.post("/parse-ktc-pdf", response_model=KtcParseResponse)
async def parse_ktc_pdf(file: UploadFile = File(...), password: str = Form(...)):
    """Unlock and parse a KTC e-Statement PDF in memory. 401 = wrong password, 422 = unreadable/changed layout."""
    data = await file.read()
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail={"code": "file_too_large"})
    try:
        return ktc_pdf.parse_pdf(data, password)
    except ktc_pdf.PasswordRejected:
        raise HTTPException(status_code=401, detail={"code": "wrong_password"})
    except ktc_pdf.KtcParseError as e:
        raise HTTPException(status_code=422, detail={"code": e.code})
