import re
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import get_engine

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
    if "accounts_name_key" in str(e.orig):
        return HTTPException(status_code=409, detail="an account with this name already exists")
    raise e


@router.get("/accounts")
def list_accounts(include_inactive: bool = True):
    with get_engine().connect() as conn:
        where = "" if include_inactive else "WHERE is_active"
        rows = conn.execute(text(f"SELECT {_COLS} FROM ledger.accounts {where} ORDER BY sort_order, id"))
        return [dict(r._mapping) for r in rows]


@router.post("/accounts", status_code=201)
def create_account(body: AccountIn):
    try:
        with get_engine().begin() as conn:
            row = conn.execute(text(
                "INSERT INTO ledger.accounts (name, type, institution, masked_ref, purpose, currency, sort_order) "
                f"VALUES (:name, :type, :institution, :masked_ref, :purpose, :currency, :sort_order) RETURNING {_COLS}"),
                body.model_dump()).one()
    except IntegrityError as e:
        raise _duplicate(e)
    return dict(row._mapping)


@router.patch("/accounts/{account_id}")
def update_account(account_id: int, body: AccountPatch):
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
                conn.execute(text(f"UPDATE ledger.accounts SET {sets} WHERE id = :id"), {**fields, "id": account_id})
            row = conn.execute(text(f"SELECT {_COLS} FROM ledger.accounts WHERE id = :id"), {"id": account_id}).first()
    except IntegrityError as e:
        raise _duplicate(e)
    if row is None:
        raise HTTPException(status_code=404, detail="account not found")
    return dict(row._mapping)
