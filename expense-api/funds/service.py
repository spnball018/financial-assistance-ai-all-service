"""Fund accounts: mutual funds held in an investment account, entered by hand.

The investment account (accounts.type = 'investment', e.g. one per fund house) is an ordinary ledger
account, but it holds no cash: every baht transferred in buys units, and every sale goes straight out
to another account. So each trade says where its money came from or went (fund_trades.funding):

- new_transfer:    the transfer bank -> fund account (buy) or fund account -> bank (sell) is created with
                   the trade and deleted with it
- linked_transfer: an existing transfer of the same amount, e.g. a KBank email the user marked as a
                   transfer to the fund account
- held_before:     units bought before the user started the ledger; no money moved (buy only)
- switch:          a sell of one fund paired with a buy of another in the same account; no money moved

Transfers into or out of the account that no trade points at are listed as unlinked, for the user to
record what they bought.

cost   = average cost: a sell takes units * (cost / units) out of the fund's cost, and the rest of the
         proceeds is realized gain (or loss)
value  = units * the latest NAV (the user's, or the last trade's when that is newer)

Every function takes the user id (uid) and only reads or changes that user's rows."""
from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ledger import transactions as txns
from ledger.transactions import Invalid, NotFound

BANGKOK = ZoneInfo("Asia/Bangkok")
CENT = Decimal("0.01")
NAV_PLACES = Decimal("0.0001")

_FUND_COLS = "f.id, f.account_id, f.code, f.name, f.nav, f.nav_date, f.is_active"
_TRADE_COLS = ("tr.id, tr.fund_id, tr.side, tr.trade_date, tr.units, tr.amount, tr.nav, tr.funding, tr.transaction_id, "
               "tr.switched_from, tr.note")


class Conflict(Exception):
    pass


def _investment_account(conn, uid: int, account_id: int) -> dict:
    a = conn.execute(text("SELECT id, name, type, is_active FROM ledger.accounts WHERE id = :a AND user_id = :uid"),
                     {"a": account_id, "uid": uid}).first()
    if a is None:
        raise Invalid(f"account {account_id} does not exist")
    if a.type != "investment":
        raise Invalid(f"account {a.name} is not an investment account")
    return dict(a._mapping)


def get_fund(conn, uid: int, fund_id: int) -> dict:
    f = conn.execute(text(f"SELECT {_FUND_COLS} FROM ledger.funds f WHERE f.id = :id AND f.user_id = :uid"),
                     {"id": fund_id, "uid": uid}).first()
    if f is None:
        raise NotFound(fund_id)
    return dict(f._mapping)


def _check_nav(nav: Optional[Decimal], nav_date: Optional[date]) -> None:
    if (nav is None) != (nav_date is None):
        raise Invalid("send nav and nav_date together")
    if nav is not None and nav <= 0:
        raise Invalid("nav must be positive")


def create_fund(conn, uid: int, *, account_id: int, code: str, name: Optional[str] = None,
                nav: Optional[Decimal] = None, nav_date: Optional[date] = None) -> dict:
    _investment_account(conn, uid, account_id)
    code = code.strip()
    if not code:
        raise Invalid("code is required")
    _check_nav(nav, nav_date)
    try:
        new_id = conn.execute(text(
            "INSERT INTO ledger.funds (user_id, account_id, code, name, nav, nav_date) "
            "VALUES (:uid, :a, :code, :name, :nav, :nd) RETURNING id"),
            {"uid": uid, "a": account_id, "code": code, "name": (name or "").strip() or None,
             "nav": nav, "nd": nav_date}).scalar()
    except IntegrityError as e:
        if "funds_user_account_code_key" in str(e.orig):
            raise Conflict(f"fund {code} is already in this account")
        raise
    return get_fund(conn, uid, new_id)


def update_fund(conn, uid: int, fund_id: int, *, fields: dict) -> dict:
    """Change code, name, is_active, or the latest NAV (nav + nav_date together; nav null clears it)."""
    get_fund(conn, uid, fund_id)
    sets, params = [], {"id": fund_id, "uid": uid}
    if "code" in fields:
        if not (fields["code"] or "").strip():
            raise Invalid("code is required")
        sets.append("code = :code")
        params["code"] = fields["code"].strip()
    if "name" in fields:
        sets.append("name = :name")
        params["name"] = (fields["name"] or "").strip() or None
    if "is_active" in fields:
        if fields["is_active"] is None:
            raise Invalid("is_active must be true or false")
        sets.append("is_active = :active")
        params["active"] = fields["is_active"]
    if "nav" in fields or "nav_date" in fields:
        nav, nav_date = fields.get("nav"), fields.get("nav_date")
        _check_nav(nav, nav_date)
        sets.append("nav = :nav, nav_date = :nd")
        params.update(nav=nav, nd=nav_date)
    if sets:
        try:
            conn.execute(text(f"UPDATE ledger.funds SET {', '.join(sets)} WHERE id = :id AND user_id = :uid"), params)
        except IntegrityError as e:
            if "funds_user_account_code_key" in str(e.orig):
                raise Conflict(f"fund {params['code']} is already in this account")
            raise
    return get_fund(conn, uid, fund_id)


def delete_fund(conn, uid: int, fund_id: int) -> None:
    """Only a fund without trades; otherwise set is_active = false."""
    get_fund(conn, uid, fund_id)
    if conn.execute(text("SELECT EXISTS (SELECT 1 FROM ledger.fund_trades WHERE fund_id = :f AND user_id = :uid)"),
                    {"f": fund_id, "uid": uid}).scalar():
        raise Conflict("this fund has trades: delete them first, or hide the fund")
    conn.execute(text("DELETE FROM ledger.funds WHERE id = :f AND user_id = :uid"), {"f": fund_id, "uid": uid})


def _trades(conn, uid: int, fund_ids: list[int]) -> list[dict]:
    return [dict(r._mapping) for r in conn.execute(text(
        f"SELECT {_TRADE_COLS} FROM ledger.fund_trades tr WHERE tr.user_id = :uid AND tr.fund_id = ANY(:f) "
        "ORDER BY tr.trade_date, tr.id"), {"uid": uid, "f": fund_ids})]


def _position(fund: dict, trades: list[dict]) -> dict:
    """Units, average cost and realized gain from the fund's trades in date order. Raises Invalid when a
    sell takes more units than were held on that day."""
    units, cost, realized = Decimal(0), Decimal(0), Decimal(0)
    last = None
    for t in trades:
        if t["side"] == "buy":
            units += t["units"]
            cost += t["amount"]
        else:
            if t["units"] > units:
                raise Invalid(f"{fund['code']}: selling {t['units']} units on {t['trade_date']} but only "
                              f"{units} are held then")
            out = cost if t["units"] == units else (cost * t["units"] / units).quantize(CENT)
            realized += t["amount"] - out
            cost -= out
            units -= t["units"]
        last = t
    nav, nav_date, nav_source = fund["nav"], fund["nav_date"], "user"
    if last is not None and (nav_date is None or last["trade_date"] > nav_date):
        nav, nav_date, nav_source = last["nav"], last["trade_date"], "trade"
    value = (units * nav).quantize(CENT) if nav is not None else None
    return {"units": units, "cost": cost, "avg_cost": (cost / units).quantize(NAV_PLACES) if units else None,
            "nav": nav, "nav_date": nav_date, "nav_source": nav_source if nav is not None else None,
            "market_value": value, "unrealized": value - cost if value is not None else None,
            "realized": realized, "trades": len(trades)}


def _check_positions(conn, uid: int, fund_ids: set[int]) -> None:
    """No fund sold short at any date (after a new sell, or after deleting a buy)."""
    for fid in fund_ids:
        _position(get_fund(conn, uid, fid), _trades(conn, uid, [fid]))


def _net_deposits(conn, uid: int, account_id: int) -> Decimal:
    """What the balances page shows for the account: confirmed money in minus money out."""
    since = conn.execute(text("SELECT occurred_at FROM ledger.transactions WHERE user_id = :uid AND account_id = :a "
                              "AND node_type = 'opening_balance'"), {"a": account_id, "uid": uid}).scalar()
    return conn.execute(text("""
        SELECT COALESCE(sum(amount), 0) FROM ledger.v_account_postings
        WHERE user_id = :uid AND account_id = :a AND status = 'confirmed'
          AND (CAST(:s AS timestamptz) IS NULL OR occurred_at >= :s)"""),
        {"uid": uid, "a": account_id, "s": since}).scalar()


_UNLINKED = text("""
    SELECT t.id AS transaction_id, t.occurred_at, t.has_time, t.status,
           CASE WHEN t.counter_account_id = :a THEN t.amount ELSE -t.amount END AS amount,
           o.name AS other_account, COALESCE(t.merchant_raw, t.description) AS description
    FROM ledger.transactions t
    JOIN ledger.accounts o ON o.id = CASE WHEN t.counter_account_id = :a THEN t.account_id ELSE t.counter_account_id END
    WHERE t.user_id = :uid AND t.kind = 'transfer' AND t.status IN ('pending', 'confirmed') AND t.parent_id IS NULL
      AND :a IN (t.account_id, t.counter_account_id)
      AND NOT EXISTS (SELECT 1 FROM ledger.fund_trades tr WHERE tr.transaction_id = t.id)
    ORDER BY t.occurred_at DESC, t.id DESC""")


def unlinked_transfers(conn, uid: int, account_id: int) -> list[dict]:
    """Transfers into (amount > 0) or out of (< 0) the fund account that no trade points at yet."""
    return [dict(r._mapping) for r in conn.execute(_UNLINKED, {"uid": uid, "a": account_id})]


def portfolio(conn, uid: int) -> list[dict]:
    """Every investment account with its funds' positions, totals, and how many transfers wait for a trade."""
    out = []
    for a in conn.execute(text("SELECT id, name, institution, is_active FROM ledger.accounts "
                               "WHERE user_id = :uid AND type = 'investment' ORDER BY sort_order, id"), {"uid": uid}):
        funds = [dict(r._mapping) for r in conn.execute(text(
            f"SELECT {_FUND_COLS} FROM ledger.funds f WHERE f.user_id = :uid AND f.account_id = :a "
            "ORDER BY f.is_active DESC, f.code"), {"uid": uid, "a": a.id})]
        trades = _trades(conn, uid, [f["id"] for f in funds])
        for f in funds:
            f.update(_position(f, [t for t in trades if t["fund_id"] == f["id"]]))
        value = sum((f["market_value"] for f in funds if f["market_value"] is not None), Decimal(0))
        priced_cost = sum((f["cost"] for f in funds if f["market_value"] is not None), Decimal(0))
        out.append({"account_id": a.id, "name": a.name, "institution": a.institution, "is_active": a.is_active,
                    "net_deposits": _net_deposits(conn, uid, a.id),
                    "cost": sum((f["cost"] for f in funds), Decimal(0)), "market_value": value,
                    "unrealized": value - priced_cost,
                    "realized": sum((f["realized"] for f in funds), Decimal(0)),
                    "unlinked_transfers": len(unlinked_transfers(conn, uid, a.id)), "funds": funds})
    return out


def _check_link(conn, uid: int, fund: dict, side: str, amount: Decimal, transaction_id: int) -> None:
    t = conn.execute(text("""
        SELECT t.kind, t.status, t.parent_id, t.account_id, t.counter_account_id, t.amount,
               EXISTS (SELECT 1 FROM ledger.fund_trades tr WHERE tr.transaction_id = t.id) AS linked
        FROM ledger.transactions t WHERE t.id = :id AND t.user_id = :uid"""), {"id": transaction_id, "uid": uid}).first()
    if t is None:
        raise Invalid(f"transaction {transaction_id} does not exist")
    into = t.counter_account_id == fund["account_id"]
    if t.kind != "transfer" or t.parent_id is not None or t.status == "ignored" or \
            (side == "buy" and not into) or (side == "sell" and t.account_id != fund["account_id"]):
        raise Invalid(f"transaction {transaction_id} is not a transfer {'into' if side == 'buy' else 'out of'} "
                      "this fund account")
    if t.linked:
        raise Conflict(f"transaction {transaction_id} already pays for another trade")
    if t.amount != amount:
        raise Invalid(f"the transfer is {t.amount}, the trade {amount}: they must be the same amount")


def add_trade(conn, uid: int, *, fund_id: int, side: str, trade_date: date, units: Decimal, amount: Decimal,
              nav: Optional[Decimal] = None, counter_account_id: Optional[int] = None,
              transaction_id: Optional[int] = None, held_before: bool = False, note: Optional[str] = None) -> dict:
    """A buy or sell, with exactly one source of the money:
    counter_account_id -- buy: the account the money is transferred from; sell: the account it goes to.
                          The transfer is created now (has_time false, at the trade date).
    transaction_id     -- an existing transfer into (buy) / out of (sell) the fund account, same amount
    held_before        -- buy only: units held before the ledger started, no money moved"""
    if side not in ("buy", "sell"):
        raise Invalid("side must be buy or sell")
    if units <= 0 or amount <= 0:
        raise Invalid("units and amount must be positive")
    if nav is not None and nav <= 0:
        raise Invalid("nav must be positive")
    if [counter_account_id is not None, transaction_id is not None, held_before].count(True) != 1:
        raise Invalid("give one of counter_account_id, transaction_id or held_before: a fund account holds no cash, "
                      "so the money has to come from (or go to) somewhere")
    if held_before and side != "buy":
        raise Invalid("held_before is for a buy; a sale's money goes to an account")
    fund = get_fund(conn, uid, fund_id)
    if counter_account_id is not None:
        at = datetime.combine(trade_date, time(0, 0), tzinfo=BANGKOK)
        label = f"{'ซื้อ' if side == 'buy' else 'ขาย'}กองทุน {fund['code']}"
        src, dst = ((counter_account_id, fund["account_id"]) if side == "buy"
                    else (fund["account_id"], counter_account_id))
        transaction_id = txns.create_manual(conn, uid, account_id=src, amount=amount, occurred_at=at, has_time=False,
                                            kind="transfer", transfer_account_id=dst, merchant=label)["id"]
        funding = "new_transfer"
    elif transaction_id is not None:
        _check_link(conn, uid, fund, side, amount, transaction_id)
        funding = "linked_transfer"
    else:
        funding = "held_before"
    new_id = _insert(conn, uid, fund_id=fund_id, side=side, trade_date=trade_date, units=units, amount=amount,
                     nav=nav, funding=funding, transaction_id=transaction_id, note=note)
    _check_positions(conn, uid, {fund_id})
    return get_trade(conn, uid, new_id)


def _insert(conn, uid: int, *, fund_id: int, side: str, trade_date: date, units: Decimal, amount: Decimal,
            nav: Optional[Decimal], funding: str, transaction_id: Optional[int] = None,
            switched_from: Optional[int] = None, note: Optional[str] = None) -> int:
    return conn.execute(text("""
        INSERT INTO ledger.fund_trades (user_id, fund_id, side, trade_date, units, amount, nav, funding, transaction_id,
                                        switched_from, note)
        VALUES (:uid, :f, :side, :d, :u, :amt, :nav, :funding, :t, :sw, :note) RETURNING id"""),
        {"uid": uid, "f": fund_id, "side": side, "d": trade_date, "u": units, "amt": amount,
         "nav": nav if nav is not None else (amount / units).quantize(NAV_PLACES), "funding": funding,
         "t": transaction_id, "sw": switched_from, "note": (note or "").strip()[:2000] or None}).scalar()


def add_switch(conn, uid: int, *, from_fund_id: int, to_fund_id: int, trade_date: date, amount: Decimal,
               units_out: Decimal, units_in: Decimal, nav_out: Optional[Decimal] = None,
               nav_in: Optional[Decimal] = None, note: Optional[str] = None) -> dict:
    """Switch `amount` from one fund to another in the same account: a sell and a buy, no money moved."""
    if amount <= 0 or units_out <= 0 or units_in <= 0:
        raise Invalid("amount and units must be positive")
    src, dst = get_fund(conn, uid, from_fund_id), get_fund(conn, uid, to_fund_id)
    if src["id"] == dst["id"]:
        raise Invalid("switch to a different fund")
    if src["account_id"] != dst["account_id"]:
        raise Invalid("both funds must be in the same account; between accounts, record a sell and a buy")
    out_id = _insert(conn, uid, fund_id=src["id"], side="sell", trade_date=trade_date, units=units_out, amount=amount,
                     nav=nav_out, funding="switch", note=note)
    in_id = _insert(conn, uid, fund_id=dst["id"], side="buy", trade_date=trade_date, units=units_in, amount=amount,
                    nav=nav_in, funding="switch", switched_from=out_id, note=note)
    _check_positions(conn, uid, {src["id"]})
    return {"sell": get_trade(conn, uid, out_id), "buy": get_trade(conn, uid, in_id)}


def get_trade(conn, uid: int, trade_id: int) -> dict:
    t = conn.execute(text(f"SELECT {_TRADE_COLS} FROM ledger.fund_trades tr WHERE tr.id = :id AND tr.user_id = :uid"),
                     {"id": trade_id, "uid": uid}).first()
    if t is None:
        raise NotFound(trade_id)
    return dict(t._mapping)


def delete_trade(conn, uid: int, trade_id: int) -> None:
    """Delete a trade, and the transfer created with it. Either side of a switch deletes both sides; a
    linked transfer stays (it becomes unlinked again)."""
    t = get_trade(conn, uid, trade_id)
    if t["switched_from"] is not None:
        t = get_trade(conn, uid, t["switched_from"])
    funds = {t["fund_id"]} | set(conn.execute(text(
        "SELECT fund_id FROM ledger.fund_trades WHERE switched_from = :id AND user_id = :uid"),
        {"id": t["id"], "uid": uid}).scalars())
    # the switch-in buy goes with its sell (ON DELETE CASCADE)
    conn.execute(text("DELETE FROM ledger.fund_trades WHERE id = :id AND user_id = :uid"), {"id": t["id"], "uid": uid})
    if t["funding"] == "new_transfer" and t["transaction_id"] is not None:
        conn.execute(text("DELETE FROM ledger.transactions WHERE id = :id AND user_id = :uid "
                          "AND source = 'manual' AND node_type = 'manual'"), {"id": t["transaction_id"], "uid": uid})
    _check_positions(conn, uid, funds)   # removing a buy must not leave a later sell short


def history(conn, uid: int, account_id: int) -> dict:
    """The fund account's trades newest first, each with where its money came from or went, and the
    transfers no trade points at yet."""
    acct = _investment_account(conn, uid, account_id)
    items = [dict(r._mapping) for r in conn.execute(text(f"""
        SELECT {_TRADE_COLS}, f.code AS fund_code, t.status AS transfer_status, t.occurred_at AS transfer_at,
               o.name AS other_account, COALESCE(sw.code, sw2.code) AS switch_fund_code
        FROM ledger.fund_trades tr
        JOIN ledger.funds f ON f.id = tr.fund_id
        LEFT JOIN ledger.transactions t ON t.id = tr.transaction_id
        LEFT JOIN ledger.accounts o ON o.id = CASE WHEN tr.side = 'buy' THEN t.account_id ELSE t.counter_account_id END
        LEFT JOIN ledger.fund_trades si ON si.id = tr.switched_from
        LEFT JOIN ledger.funds sw ON sw.id = si.fund_id
        LEFT JOIN ledger.fund_trades so ON so.switched_from = tr.id
        LEFT JOIN ledger.funds sw2 ON sw2.id = so.fund_id
        WHERE tr.user_id = :uid AND f.account_id = :a
        ORDER BY tr.trade_date DESC, tr.id DESC"""), {"uid": uid, "a": account_id})]
    return {"account_id": acct["id"], "name": acct["name"], "items": items,
            "unlinked": unlinked_transfers(conn, uid, account_id)}
