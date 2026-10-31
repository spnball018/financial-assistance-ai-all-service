"""Fund accounts: funds in an investment account that holds no cash. Buys are paid by a transfer from
the bank (new or linked), sells go out to an account, switches move between funds; average cost, NAV,
history, and per-user isolation."""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import auth
from app.auth import Principal, require_user
from app.main import app

BANK = 1  # KBank ใช้จ่าย


def _drop_test_accounts(db):
    with db.begin() as conn:
        conn.execute(text("TRUNCATE ledger.fund_trades, ledger.funds RESTART IDENTITY CASCADE"))
        conn.execute(text("DELETE FROM ledger.transactions"))
        conn.execute(text("DELETE FROM ledger.accounts WHERE name LIKE 'TEST fund %'"))


@pytest.fixture
def api(db, monkeypatch):
    monkeypatch.setattr("app.db._engine", db)
    _drop_test_accounts(db)
    yield TestClient(app)
    _drop_test_accounts(db)  # the accounts list in other tests starts with the seeded accounts


@pytest.fixture
def port(api):
    """An investment account (KAsset) with one fund."""
    acc = api.post("/accounts", json={"name": "TEST fund KAsset", "type": "investment"}).json()["id"]
    fund = api.post("/funds", json={"account_id": acc, "code": "K-USA-A(A)", "name": "เค ยูเอส"}).json()
    return acc, fund["id"]


def rows(db, sql, **p):
    with db.connect() as conn:
        return conn.execute(text(sql), p).all()


def buy(api, fund, amount, units, day="2026-10-01", **kw):
    return api.post("/funds/trades", json={"fund_id": fund, "side": "buy", "trade_date": day, "units": units,
                                           "amount": amount, **kw})


def sell(api, fund, amount, units, day="2026-10-20", **kw):
    return api.post("/funds/trades", json={"fund_id": fund, "side": "sell", "trade_date": day, "units": units,
                                           "amount": amount, **kw})


def transfer(api, src, dst, amount, when="2026-10-01T09:00:00+07:00"):
    return api.post("/transactions", json={"kind": "transfer", "account_id": src, "transfer_account_id": dst,
                                           "amount": amount, "occurred_at": when}).json()["id"]


def account(api, acc):
    return next(a for a in api.get("/funds/portfolio").json() if a["account_id"] == acc)


def test_fund_only_in_investment_account_and_unique_code(api, port):
    acc, _ = port
    assert api.post("/funds", json={"account_id": BANK, "code": "X"}).status_code == 422
    assert api.post("/funds", json={"account_id": acc, "code": "K-USA-A(A)"}).status_code == 409
    assert api.post("/funds", json={"account_id": acc, "code": "Y", "nav": "10"}).status_code == 422  # no nav_date


def test_buy_with_transfer_from_bank(api, db, port):
    acc, fund = port
    r = buy(api, fund, "10000", "800.1234", counter_account_id=BANK)
    assert r.status_code == 201, r.text
    t = r.json()
    assert (Decimal(str(t["nav"])), t["funding"]) == (Decimal("12.4981"), "new_transfer")  # nav = amount / units
    # one confirmed transfer KBank -> fund account: not spending, moves the balance
    assert rows(db, "SELECT kind, account_id, counter_account_id, amount, status, merchant_raw FROM ledger.transactions "
                    "WHERE id = :i", i=t["transaction_id"]) == [
        ("transfer", BANK, acc, Decimal("10000.00"), "confirmed", "ซื้อกองทุน K-USA-A(A)")]
    assert api.get("/reports/summary?month=2026-10").json()["total"] == 0

    a = account(api, acc)
    # units x NAV rounded to 4 places: 2 satang off the amount paid, as the fund house would show it
    assert (a["net_deposits"], a["cost"], a["market_value"], a["unlinked_transfers"]) == (10000, 10000, 10000.02, 0)
    assert "cash" not in a
    f = a["funds"][0]
    assert (f["units"], f["nav_source"], f["unrealized"]) == (800.1234, "trade", 0.02)


def test_every_trade_needs_one_money_source(api, db, port):
    _, fund = port
    assert buy(api, fund, "1000", "100").status_code == 422
    assert buy(api, fund, "1000", "100", counter_account_id=BANK, held_before=True).status_code == 422
    assert buy(api, fund, "1000", "100", held_before=True).json()["funding"] == "held_before"
    assert sell(api, fund, "500", "50").status_code == 422
    assert sell(api, fund, "500", "50", held_before=True).status_code == 422
    assert rows(db, "SELECT count(*) FROM ledger.transactions") == [(0,)]   # held before: no money moved


def test_link_an_existing_transfer(api, db, port):
    """A KBank transfer to the fund house, already marked as a transfer to the fund account, pays for the buy."""
    acc, fund = port
    tx = transfer(api, BANK, acc, "5000")
    other = transfer(api, BANK, 3, "5000")   # to another account
    assert account(api, acc)["unlinked_transfers"] == 1
    assert buy(api, fund, "4999", "400", transaction_id=tx).status_code == 422          # amount differs
    assert buy(api, fund, "5000", "400", transaction_id=other).status_code == 422       # not into this account
    assert sell(api, fund, "5000", "1", transaction_id=tx).status_code == 422           # it comes in, not out
    t = buy(api, fund, "5000", "400", transaction_id=tx).json()
    assert (t["funding"], t["transaction_id"]) == ("linked_transfer", tx)
    assert buy(api, fund, "5000", "400", transaction_id=tx).status_code == 409          # one trade per transfer
    assert account(api, acc)["unlinked_transfers"] == 0

    # deleting the trade keeps the transfer (it came from the bank), which waits for a trade again
    assert api.delete(f"/funds/trades/{t['id']}").status_code == 204
    assert rows(db, "SELECT count(*) FROM ledger.transactions WHERE id = :i", i=tx) == [(1,)]
    assert [u["transaction_id"] for u in api.get(f"/funds/accounts/{acc}/history").json()["unlinked"]] == [tx]


def test_nav_update_and_sell_average_cost(api, db, port):
    acc, fund = port
    buy(api, fund, "1000", "100", day="2026-10-01", counter_account_id=BANK)
    buy(api, fund, "2200", "200", day="2026-10-05", counter_account_id=BANK)   # avg cost 10.6667
    r = api.patch(f"/funds/{fund}", json={"nav": "12.5", "nav_date": "2026-10-10"})
    assert r.status_code == 200 and r.json()["nav"] == 12.5
    f = account(api, acc)["funds"][0]
    assert (f["units"], f["cost"], f["market_value"], f["unrealized"], f["nav_source"]) == (300, 3200, 3750, 550, "user")

    # sell 150 units for 1950 back to the bank: cost out 1600, realized 350
    s = sell(api, fund, "1950", "150", counter_account_id=BANK).json()
    assert rows(db, "SELECT account_id, counter_account_id, merchant_raw FROM ledger.transactions WHERE id = :i",
                i=s["transaction_id"]) == [(acc, BANK, "ขายกองทุน K-USA-A(A)")]
    a = account(api, acc)
    f = a["funds"][0]
    assert (f["units"], f["cost"], f["realized"], f["nav"], f["nav_source"]) == (150, 1600, 350, 13, "trade")
    assert (a["net_deposits"], a["market_value"]) == (1250, 1950)


def test_cannot_oversell_now_or_by_deleting_a_buy(api, db, port):
    acc, fund = port
    b = buy(api, fund, "1000", "100", counter_account_id=BANK).json()
    r = sell(api, fund, "2000", "100.0001", counter_account_id=BANK)
    assert r.status_code == 422 and "only 100" in r.json()["detail"]
    assert sell(api, fund, "1100", "100", day="2026-09-30", counter_account_id=BANK).status_code == 422  # before the buy
    assert rows(db, "SELECT count(*) FROM ledger.fund_trades") == [(1,)]
    assert rows(db, "SELECT count(*) FROM ledger.transactions") == [(1,)]   # the refused sells made no transfer

    assert sell(api, fund, "1100", "100", counter_account_id=BANK).status_code == 201
    assert api.delete(f"/funds/trades/{b['id']}").status_code == 422
    assert rows(db, "SELECT count(*) FROM ledger.transactions WHERE id = :i", i=b["transaction_id"]) == [(1,)]


def test_delete_trade_removes_its_transfer(api, db, port):
    acc, fund = port
    t = buy(api, fund, "500", "50", counter_account_id=BANK).json()
    assert api.delete(f"/funds/trades/{t['id']}").status_code == 204
    assert rows(db, "SELECT count(*) FROM ledger.transactions") == [(0,)]
    assert api.delete(f"/funds/{fund}").status_code == 204


def test_fund_with_trades_cannot_be_deleted(api, port):
    _, fund = port
    buy(api, fund, "500", "50", held_before=True)
    assert api.delete(f"/funds/{fund}").status_code == 409
    assert api.patch(f"/funds/{fund}", json={"is_active": False}).json()["is_active"] is False


def test_switch_between_funds(api, db, port):
    acc, usa = port
    div = api.post("/funds", json={"account_id": acc, "code": "KFSDIV"}).json()["id"]
    buy(api, usa, "1000", "100", day="2026-09-01", held_before=True)
    body = {"from_fund_id": usa, "to_fund_id": div, "trade_date": "2026-10-10", "amount": "600",
            "units_out": "50", "units_in": "40"}
    assert api.post("/funds/switches", json={**body, "units_out": "101"}).status_code == 422   # more than held
    sw = api.post("/funds/switches", json=body)
    assert sw.status_code == 201, sw.text
    assert rows(db, "SELECT count(*) FROM ledger.transactions") == [(0,)]   # no money moved

    a = account(api, acc)
    pos = {f["code"]: (f["units"], f["cost"], f["realized"]) for f in a["funds"]}
    assert pos == {"K-USA-A(A)": (50, 500, 100), "KFSDIV": (40, 600, 0)}
    items = api.get(f"/funds/accounts/{acc}/history").json()["items"]
    assert [(i["side"], i["fund_code"], i["funding"], i["switch_fund_code"]) for i in items] == [
        ("buy", "KFSDIV", "switch", "K-USA-A(A)"), ("sell", "K-USA-A(A)", "switch", "KFSDIV"),
        ("buy", "K-USA-A(A)", "held_before", None)]

    other = api.post("/accounts", json={"name": "TEST fund SCBAM", "type": "investment"}).json()["id"]
    scb = api.post("/funds", json={"account_id": other, "code": "SCBS&P500"}).json()["id"]
    assert api.post("/funds/switches", json={**body, "to_fund_id": scb}).status_code == 422   # another account

    # deleting either side deletes the whole switch
    assert api.delete(f"/funds/trades/{sw.json()['buy']['id']}").status_code == 204
    assert rows(db, "SELECT count(*) FROM ledger.fund_trades") == [(1,)]


def test_history_lists_trades_with_their_transfers(api, port):
    acc, fund = port
    tx = transfer(api, BANK, acc, "3000")
    buy(api, fund, "3000", "300", day="2026-10-01", transaction_id=tx)
    sell(api, fund, "1100", "100", day="2026-10-03", counter_account_id=BANK)
    out = transfer(api, acc, BANK, "200", when="2026-10-05T10:00:00+07:00")   # not explained by a trade
    h = api.get(f"/funds/accounts/{acc}/history").json()
    assert [(i["side"], i["amount"], i["funding"], i["other_account"]) for i in h["items"]] == [
        ("sell", 1100, "new_transfer", "KBank ใช้จ่าย"), ("buy", 3000, "linked_transfer", "KBank ใช้จ่าย")]
    assert [(u["transaction_id"], u["amount"], u["other_account"]) for u in h["unlinked"]] == [(out, -200, "KBank ใช้จ่าย")]
    assert api.get(f"/funds/accounts/{BANK}/history").status_code == 422   # not an investment account


def test_funds_are_private(api, db, port):
    acc, fund = port
    t = buy(api, fund, "500", "50", counter_account_id=BANK).json()
    owner_tx = transfer(api, BANK, acc, "1")
    kid = auth.add_user("kid@example.com", "member")["id"]
    app.dependency_overrides[require_user] = lambda: Principal(kid, "kid", "member", "session")
    assert api.get("/funds/portfolio").json() == []
    assert api.post("/funds", json={"account_id": acc, "code": "X"}).status_code == 422
    assert api.patch(f"/funds/{fund}", json={"name": "x"}).status_code == 404
    assert buy(api, fund, "1", "1", held_before=True).status_code == 404
    assert api.delete(f"/funds/trades/{t['id']}").status_code == 404
    assert api.get(f"/funds/accounts/{acc}/history").status_code == 422
    kid_acc = api.post("/accounts", json={"name": "TEST fund kid", "type": "investment"}).json()["id"]
    kid_fund = api.post("/funds", json={"account_id": kid_acc, "code": "K-USA-A(A)"}).json()["id"]
    # paying from the owner's bank account, or with the owner's transfer, is refused
    assert buy(api, kid_fund, "1", "1", counter_account_id=BANK).status_code == 422
    assert buy(api, kid_fund, "1", "1", transaction_id=owner_tx).status_code == 422
    assert api.post("/funds/switches", json={"from_fund_id": fund, "to_fund_id": kid_fund, "trade_date": "2026-10-10",
                                             "amount": "1", "units_out": "1", "units_in": "1"}).status_code == 404
    assert rows(db, "SELECT count(*) FROM ledger.fund_trades WHERE user_id = :k", k=kid) == [(0,)]
