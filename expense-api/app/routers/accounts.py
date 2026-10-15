import re
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.auth import current_user_id
from app.db import get_engine
from ledger import balances as bal

router = APIRouter(tags=["accounts"])

AccountType = Literal["bank", "credit_card", "ewallet", "cash", "investment"]
_COLS = "id, name, type, institution, masked_ref, currency, is_active, purpose, sort_order"


def _masked_ref(v: Optional[str]) -> Optional[str]:
    """Last digits of an account / wallet / PromptPay id, e.g. "7722" or "x5204". Used by T6.2 to
    recognise transfers to this account from the destination number in bank emails, so it must
    contain at least 4 digits. Never store a full account number here."""
    if v is None or not v.strip():
        return None
    v = v.strip()
    if len(re.sub(r"\D", "", v)) < 4:
        raise ValueError("masked_ref needs at least the last 4 digits")
    return v


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: AccountType
    institution: Optional[str] = None
    masked_ref: Optional[str] = None
    purpose: Optional[str] = None
    currency: str = Field("THB", min_length=3, max_length=3)
    sort_order: int = 0

    _check_ref = field_validator("masked_ref")(lambda cls, v: _masked_ref(v))


class AccountPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    type: Optional[AccountType] = None
    institution: Optional[str] = None
    masked_ref: Optional[str] = None
    purpose: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None

    _check_ref = field_validator("masked_ref")(lambda cls, v: _masked_ref(v))


def _duplicate(e: IntegrityError) -> HTTPException:
    if "accounts_user_name_key" in str(e.orig):
        return HTTPException(status_code=409, detail="an account with this name already exists")
    raise e


@router.get("/accounts")
def list_accounts(include_inactive: bool = True, uid: int = Depends(current_user_id)):
    with get_engine().connect() as conn:
        where = "" if include_inactive else "AND is_active"
        rows = conn.execute(text(f"SELECT {_COLS} FROM ledger.accounts WHERE user_id = :uid {where} "
                                 "ORDER BY sort_order, id"), {"uid": uid})
        return [dict(r._mapping) for r in rows]


@router.post("/accounts", status_code=201)
def create_account(body: AccountIn, uid: int = Depends(current_user_id)):
    try:
        with get_engine().begin() as conn:
            row = conn.execute(text(
                "INSERT INTO ledger.accounts (user_id, name, type, institution, masked_ref, purpose, currency, sort_order) "
                f"VALUES (:uid, :name, :type, :institution, :masked_ref, :purpose, :currency, :sort_order) RETURNING {_COLS}"),
                {**body.model_dump(), "uid": uid}).one()
    except IntegrityError as e:
        raise _duplicate(e)
    return dict(row._mapping)


@router.patch("/accounts/{account_id}")
def update_account(account_id: int, body: AccountPatch, uid: int = Depends(current_user_id)):
    """Only the fields sent are changed. Accounts are never deleted (transactions point at them);
    set is_active=false to hide one."""
    fields = {k: getattr(body, k) for k in body.model_fields_set}
    if "name" in fields and fields["name"] is None:
        raise HTTPException(status_code=422, detail="name cannot be null")
    for k in ("type", "is_active", "sort_order"):
        if k in fields and fields[k] is None:
            raise HTTPException(status_code=422, detail=f"{k} cannot be null")
    try:
        with get_engine().begin() as conn:
            if fields:
                sets = ", ".join(f"{k} = :{k}" for k in fields)
                conn.execute(text(f"UPDATE ledger.accounts SET {sets} WHERE id = :id AND user_id = :uid"),
                             {**fields, "id": account_id, "uid": uid})
            row = conn.execute(text(f"SELECT {_COLS} FROM ledger.accounts WHERE id = :id AND user_id = :uid"),
                               {"id": account_id, "uid": uid}).first()
    except IntegrityError as e:
        raise _duplicate(e)
    if row is None:
        raise HTTPException(status_code=404, detail="account not found")
    return dict(row._mapping)


@router.get("/accounts/balances")
def account_balances(uid: int = Depends(current_user_id)):
    """Per account: balance (confirmed), balance_with_pending, opening balance, and for the KBank account
    the bank's latest reported balance next to the computed one."""
    with get_engine().connect() as conn:
        return bal.balances(conn, uid)


@router.get("/accounts/{account_id}/ledger")
def account_ledger(account_id: int, uid: int = Depends(current_user_id)):
    """Every movement of one account, newest first, with the running balance after each row and, on rows
    from a K PLUS email, the balance the bank reported, to find where the computed balance drifts."""
    with get_engine().connect() as conn:
        try:
            return bal.account_ledger(conn, uid, account_id)
        except bal.NotFound:
            raise HTTPException(status_code=404, detail="account not found")


class OpeningBalance(BaseModel):
    amount: Decimal = Field(max_digits=15, decimal_places=2)
    as_of: date


@router.put("/accounts/{account_id}/opening-balance")
def put_opening_balance(account_id: int, body: OpeningBalance, uid: int = Depends(current_user_id)):
    """Balance at the start of `as_of` (Bangkok). Negative for money owed (credit card)."""
    try:
        with get_engine().begin() as conn:
            return bal.set_opening(conn, uid, account_id, body.amount, body.as_of)
    except bal.NotFound:
        raise HTTPException(status_code=404, detail="account not found")


@router.delete("/accounts/{account_id}/opening-balance", status_code=204)
def delete_opening_balance(account_id: int, uid: int = Depends(current_user_id)):
    with get_engine().begin() as conn:
        bal.delete_opening(conn, uid, account_id)
