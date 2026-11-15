from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.auth import current_user_id
from app.db import get_engine
from funds import history as nav_history
from funds import service
from funds.nav import NavError, NavSource, source_from_env
from ledger.transactions import Invalid, NotFound

router = APIRouter(tags=["funds"])

NAV = Field(default=None, gt=0, max_digits=16, decimal_places=4)


def get_nav_source() -> Optional[NavSource]:
    return source_from_env()


def _source(source: Optional[NavSource]) -> NavSource:
    if source is None:
        raise HTTPException(status_code=503, detail="fetching NAVs is turned off (FUND_NAV_URL=off)")
    return source


def _run(fn):
    try:
        return fn()
    except NotFound:
        raise HTTPException(status_code=404, detail="not found")
    except Invalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    except service.Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except NavError as e:
        raise HTTPException(status_code=502, detail=str(e))


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
def create_fund(body: FundIn, uid: int = Depends(current_user_id),
                source: Optional[NavSource] = Depends(get_nav_source)):
    """Looks the code up on Finnomena (before touching the database) for the name and latest NAV the body
    leaves out. `lookup` in the response: found | not_listed | error | off; the fund is created either way."""
    found = service.lookup(source, body.code.strip()) if body.name is None or body.nav is None else None
    with get_engine().begin() as conn:
        fund = _run(lambda: service.create_fund(conn, uid, **body.model_dump(), found=found))
    return {**fund, "lookup": found["status"] if found else "skipped"}


@router.post("/funds/refresh-nav")
def refresh_nav(account_id: Optional[int] = None, uid: int = Depends(current_user_id),
                source: Optional[NavSource] = Depends(get_nav_source)):
    """Fetch the latest NAV of every active fund (or those of one account) from Finnomena."""
    src = _source(source)
    with get_engine().begin() as conn:
        return _run(lambda: service.refresh_navs(conn, uid, src, account_id=account_id))


@router.post("/funds/navs/sync")
def sync_navs(uid: int = Depends(current_user_id), source: Optional[NavSource] = Depends(get_nav_source)):
    """Fetch NAV history of your funds now (it also runs every day by itself): the whole history the first
    time, then the new days. Also moves each fund's latest NAV forward."""
    src = _source(source)
    with get_engine().connect() as conn:
        codes = nav_history.tracked_codes(conn, uid)
    return nav_history.sync(get_engine(), src, codes)


@router.get("/funds/navs/status")
def nav_status(uid: int = Depends(current_user_id)):
    """For each of your fund codes: stored NAV days (first, last, count) and when they were last fetched."""
    with get_engine().connect() as conn:
        return nav_history.status(conn, uid)


@router.get("/funds/navs")
def nav_series(code: str = Query(min_length=1, max_length=50), since: Optional[date] = None,
               interval: str = "day", uid: int = Depends(current_user_id)):
    """Stored NAVs of one fund code, oldest first, with change, high, low and max drawdown over the period.
    interval week or month keeps the last NAV of each."""
    with get_engine().connect() as conn:
        return _run(lambda: nav_history.series(conn, code, since, nav_history.check_interval(interval)))


@router.get("/funds/nav")
def trade_nav(code: str = Query(min_length=1, max_length=50), on: date = Query(alias="date"),
              uid: int = Depends(current_user_id), source: Optional[NavSource] = Depends(get_nav_source)):
    """A fund's NAV on a date (or the business day before) from Finnomena, by code, for working out units.
    The fund need not be recorded yet."""
    return _run(lambda: service.trade_nav(_source(source), code, on))


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
    # the fund: fund_id, or account_id + fund_code (a code not in the account yet is added, looked up on Finnomena)
    fund_id: Optional[int] = None
    account_id: Optional[int] = None
    fund_code: Optional[str] = Field(None, min_length=1, max_length=50)
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
def add_trade(body: TradeIn, uid: int = Depends(current_user_id),
              source: Optional[NavSource] = Depends(get_nav_source)):
    """A new fund (account_id + fund_code) and its first trade are saved together, or neither is."""
    trade = body.model_dump(exclude={"account_id", "fund_code"})
    if (body.fund_id is None) == (body.account_id is None or body.fund_code is None):
        raise HTTPException(status_code=422, detail="give fund_id, or account_id and fund_code")
    found = None
    if body.fund_id is None:
        with get_engine().connect() as conn:
            exists = service.find_fund(conn, uid, body.account_id, body.fund_code)
        if exists is None:   # look the new code up before the transaction, not inside it
            found = service.lookup(source, body.fund_code.strip())
    with get_engine().begin() as conn:
        def run():
            if body.fund_id is None:
                trade["fund_id"] = service.find_fund(conn, uid, body.account_id, body.fund_code) or \
                    service.create_fund(conn, uid, account_id=body.account_id, code=body.fund_code, found=found)["id"]
            return service.add_trade(conn, uid, **trade)
        return _run(run)


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


@router.get("/funds/accounts/{account_id}/value-history")
def value_history(account_id: int, since: Optional[date] = None, interval: str = "day",
                  uid: int = Depends(current_user_id)):
    """What the account's funds were worth each day (units held x that day's NAV) next to their cost, from the
    first trade; `by_fund` splits the value per fund code."""
    with get_engine().connect() as conn:
        return _run(lambda: nav_history.value_history(conn, uid, account_id, since,
                                                      nav_history.check_interval(interval)))


@router.get("/funds/accounts/{account_id}/history")
def history(account_id: int, uid: int = Depends(current_user_id)):
    """Trades of one fund account, newest first, with the transfer behind each, plus the transfers into
    or out of the account that no trade points at yet (`unlinked`)."""
    with get_engine().connect() as conn:
        return _run(lambda: service.history(conn, uid, account_id))
