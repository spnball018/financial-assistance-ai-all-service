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
