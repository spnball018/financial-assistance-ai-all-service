from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.auth import current_user_id
from app.db import get_engine
from funds import service
from ledger.transactions import Invalid, NotFound

router = APIRouter(tags=["funds"])

NAV = Field(default=None, gt=0, max_digits=16, decimal_places=4)


def _run(fn):
    try:
        return fn()
    except NotFound:
        raise HTTPException(status_code=404, detail="not found")
    except Invalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    except service.Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/funds/portfolio")
def portfolio(uid: int = Depends(current_user_id)):
    """Every investment account: cash, funds with units / average cost / market value / gains, and totals."""
    with get_engine().connect() as conn:
        return service.portfolio(conn, uid)


class FundIn(BaseModel):
    account_id: int  # an account of type investment
    code: str = Field(min_length=1, max_length=50)
    name: Optional[str] = Field(None, max_length=200)
    nav: Optional[Decimal] = NAV
    nav_date: Optional[date] = None


class FundPatch(BaseModel):
    code: Optional[str] = Field(None, min_length=1, max_length=50)
    name: Optional[str] = Field(None, max_length=200)
    is_active: Optional[bool] = None
    nav: Optional[Decimal] = NAV  # the latest NAV, with nav_date; null clears it
    nav_date: Optional[date] = None


@router.post("/funds", status_code=201)
def create_fund(body: FundIn, uid: int = Depends(current_user_id)):
    with get_engine().begin() as conn:
        return _run(lambda: service.create_fund(conn, uid, **body.model_dump()))


@router.patch("/funds/{fund_id}")
def update_fund(fund_id: int, body: FundPatch, uid: int = Depends(current_user_id)):
    """Only the fields sent are changed. Update the NAV by sending nav and nav_date."""
    fields = {k: getattr(body, k) for k in body.model_fields_set}
    with get_engine().begin() as conn:
        return _run(lambda: service.update_fund(conn, uid, fund_id, fields=fields))


@router.delete("/funds/{fund_id}", status_code=204)
def delete_fund(fund_id: int, uid: int = Depends(current_user_id)):
    """Only a fund without trades (409 otherwise; hide it with is_active=false)."""
    with get_engine().begin() as conn:
        _run(lambda: service.delete_fund(conn, uid, fund_id))
    return Response(status_code=204)


class TradeIn(BaseModel):
    fund_id: int
    side: Literal["buy", "sell"]
    trade_date: date
    units: Decimal = Field(gt=0, max_digits=20, decimal_places=4)
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    nav: Optional[Decimal] = NAV  # omitted: amount / units
    # exactly one: where the money came from (buy) or went (sell); a fund account holds no cash
    counter_account_id: Optional[int] = None  # create the transfer from/to this account
    transaction_id: Optional[int] = None      # an existing transfer into/out of the fund account, same amount
    held_before: bool = False                 # buy only: units held before the ledger started
    note: Optional[str] = None


@router.post("/funds/trades", status_code=201)
def add_trade(body: TradeIn, uid: int = Depends(current_user_id)):
    with get_engine().begin() as conn:
        return _run(lambda: service.add_trade(conn, uid, **body.model_dump()))


class SwitchIn(BaseModel):
    from_fund_id: int
    to_fund_id: int
    trade_date: date
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    units_out: Decimal = Field(gt=0, max_digits=20, decimal_places=4)
    units_in: Decimal = Field(gt=0, max_digits=20, decimal_places=4)
    nav_out: Optional[Decimal] = NAV
    nav_in: Optional[Decimal] = NAV
    note: Optional[str] = None


@router.post("/funds/switches", status_code=201)
def add_switch(body: SwitchIn, uid: int = Depends(current_user_id)):
    """Switch between two funds of the same account: a sell and a buy of the same amount, no transfer."""
    with get_engine().begin() as conn:
        return _run(lambda: service.add_switch(conn, uid, **body.model_dump()))


@router.delete("/funds/trades/{trade_id}", status_code=204)
def delete_trade(trade_id: int, uid: int = Depends(current_user_id)):
    """Also deletes the transfer created with the trade; either side of a switch deletes both."""
    with get_engine().begin() as conn:
        _run(lambda: service.delete_trade(conn, uid, trade_id))
    return Response(status_code=204)


@router.get("/funds/accounts/{account_id}/history")
def history(account_id: int, uid: int = Depends(current_user_id)):
    """Trades of one fund account, newest first, with the transfer behind each, plus the transfers into
    or out of the account that no trade points at yet (`unlinked`)."""
    with get_engine().connect() as conn:
        return _run(lambda: service.history(conn, uid, account_id))
