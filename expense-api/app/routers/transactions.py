import re
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import current_user_id
from app.db import get_engine
from ledger import reports
from ledger import transactions as txns

router = APIRouter(tags=["transactions"])

MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _month(month: Optional[str]) -> Optional[str]:
    if month is not None and not MONTH.match(month):
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")
    return month


def _run(fn):
    try:
        return fn()
    except txns.NotFound:
        raise HTTPException(status_code=404, detail="transaction not found")
    except txns.Invalid as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/transactions")
def list_transactions(status: Optional[Literal["pending", "confirmed", "ignored"]] = None,
                      account_id: Optional[int] = None, month: Optional[str] = None,
                      limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0),
                      uid: int = Depends(current_user_id)):
    """Root transactions newest first, each with its descendants under `children`."""
    with get_engine().connect() as conn:
        return txns.list_trees(conn, uid, status=status, account_id=account_id, month=_month(month),
                               limit=limit, offset=offset)


@router.get("/transactions/{txn_id}")
def get_transaction(txn_id: int, uid: int = Depends(current_user_id)):
    with get_engine().connect() as conn:
        return _run(lambda: txns.get_one(conn, uid, txn_id))


class TransactionPatch(BaseModel):
    category_id: Optional[int] = None
    transfer_account_id: Optional[int] = None
    description: Optional[str] = None
    status: Optional[Literal["pending", "ignored"]] = None
    remember: Optional[bool] = None


@router.patch("/transactions/{txn_id}")
def patch_transaction(txn_id: int, body: TransactionPatch, uid: int = Depends(current_user_id)):
    """Only the fields sent are changed. category_id = expense in that category; transfer_account_id =
    transfer to one of the user's accounts (not counted as an expense); remember=false = never learn
    a rule for this payee."""
    with get_engine().begin() as conn:
        return _run(lambda: txns.update(conn, uid, txn_id, category_id=body.category_id,
                                        transfer_account_id=body.transfer_account_id, description=body.description,
                                        status=body.status, remember=body.remember, fields=body.model_fields_set))


class ConfirmRequest(BaseModel):
    ids: Optional[list[int]] = None
    all: bool = False


@router.post("/transactions/confirm")
def confirm_transactions(body: ConfirmRequest, uid: int = Depends(current_user_id)):
    with get_engine().begin() as conn:  # confirm + Merchant Memory in one DB transaction (T3.4)
        return _run(lambda: txns.confirm(conn, uid, ids=body.ids, all_pending=body.all))


class ManualTransaction(BaseModel):
    account_id: int
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    occurred_at: datetime
    category_id: int
    merchant: Optional[str] = None
    description: Optional[str] = None
    has_time: bool = True
    kind: Literal["expense", "income"] = "expense"


@router.post("/transactions", status_code=201)
def create_transaction(body: ManualTransaction, uid: int = Depends(current_user_id)):
    with get_engine().begin() as conn:
        return _run(lambda: txns.create_manual(conn, uid, **body.model_dump()))


@router.get("/reports/summary")
def summary(month: str, uid: int = Depends(current_user_id)):
    with get_engine().connect() as conn:
        return reports.monthly_summary(conn, uid, _month(month))
