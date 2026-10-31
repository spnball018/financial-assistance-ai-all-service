"""Fund accounts: funds in an investment account that holds no cash. Buys are paid by a transfer from
the bank (new or linked), sells go out to an account, switches move between funds; average cost, NAV,
history, and per-user isolation."""
from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import auth
from app.auth import Principal, require_user
from app.main import app
from app.routers.funds import get_nav_source
from funds.nav import FakeNav, FinnomenaNav, FundNotListed, NavError

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


@pytest.fixture
def nav():
    fake = FakeNav({"K-USA-A(A)": [(date(2026, 10, 1), Decimal("10.0000")), (date(2026, 10, 2), Decimal("10.5000")),
                                   (date(2026, 10, 29), Decimal("12.3456"))],
                    "B-CHINAARMF": [(date(2026, 10, 29), Decimal("7.9427"))]},
                   names={"B-CHINAARMF": "กองทุนเปิดบัวหลวงหุ้นจีนเอแชร์เพื่อการเลี้ยงชีพ"})
    app.dependency_overrides[get_nav_source] = lambda: fake
    yield fake


def test_new_fund_gets_name_and_nav_from_finnomena(api, port, nav):
    acc, _ = port
    f = api.post("/funds", json={"account_id": acc, "code": "B-CHINAARMF"}).json()
    assert (f["lookup"], f["name"], f["nav"], f["nav_date"], f["nav_source"]) == \
        ("found", "กองทุนเปิดบัวหลวงหุ้นจีนเอแชร์เพื่อการเลี้ยงชีพ", 7.9427, "2026-10-29", "finnomena")
    # a code Finnomena does not list is still recorded, without a NAV
    f = api.post("/funds", json={"account_id": acc, "code": "PRIVATE-FUND"}).json()
    assert (f["lookup"], f["nav"], f["nav_source"]) == ("not_listed", None, None)


def test_value_is_units_times_fetched_nav(api, port, nav):
    acc, fund = port
    buy(api, fund, "1000", "100", day="2026-10-01", held_before=True)
    r = api.post("/funds/refresh-nav").json()
    assert [(u["code"], u["nav"], u["nav_date"]) for u in r["updated"]] == [("K-USA-A(A)", 12.3456, "2026-10-29")]
    f = account(api, acc)["funds"][0]
    assert (f["nav"], f["nav_source"], f["market_value"], f["unrealized"]) == (12.3456, "finnomena", 1234.56, 234.56)


def test_refresh_keeps_a_newer_nav_typed_by_the_user_and_reports_failures(api, port, nav):
    acc, fund = port
    api.patch(f"/funds/{fund}", json={"nav": "13", "nav_date": "2026-10-30"})
    other = api.post("/funds", json={"account_id": acc, "code": "GONE-FUND"}).json()["id"]
    r = api.post("/funds/refresh-nav").json()
    assert r["updated"] == [] and [(x["fund_id"], x["code"]) for x in r["failed"]] == [(other, "GONE-FUND")]
    assert account(api, acc)["funds"][1]["nav_source"] == "user"   # sorted by code: GONE-FUND, K-USA-A(A)
    # hidden funds are not fetched
    api.patch(f"/funds/{other}", json={"is_active": False})
    assert api.post("/funds/refresh-nav").json()["failed"] == []


def test_nav_on_a_trade_date_uses_the_business_day_before_a_holiday(api, nav):
    nav_of = lambda code, day: api.get("/funds/nav", params={"code": code, "date": day})   # noqa: E731
    assert nav_of("K-USA-A(A)", "2026-10-02").json()["nav"] == 10.5
    r = nav_of("K-USA-A(A)", "2026-10-04").json()   # Sunday
    assert (r["date"], r["nav"]) == ("2026-10-02", 10.5)
    assert nav_of("K-USA-A(A)", "2026-09-01").status_code == 422
    assert nav_of("NOT-A-FUND", "2026-10-02").status_code == 422


def test_nav_endpoints_when_fetching_is_off(api, port):
    assert api.post("/funds/refresh-nav").status_code == 503
    assert api.get("/funds/nav", params={"code": "K-USA-A(A)", "date": "2026-10-02"}).status_code == 503


def test_buy_a_fund_not_recorded_yet_by_its_code(api, db, port, nav):
    acc, _ = port
    r = api.post("/funds/trades", json={"account_id": acc, "fund_code": "B-CHINAARMF", "side": "buy",
                                        "trade_date": "2026-10-29", "units": "1259.0177", "amount": "10000",
                                        "counter_account_id": BANK})
    assert r.status_code == 201
    f = [f for f in account(api, acc)["funds"] if f["code"] == "B-CHINAARMF"][0]
    assert (f["id"], f["name"], f["units"], f["nav_source"]) == \
        (r.json()["fund_id"], "กองทุนเปิดบัวหลวงหุ้นจีนเอแชร์เพื่อการเลี้ยงชีพ", 1259.0177, "finnomena")
    # the same code again buys more of the same fund
    r2 = api.post("/funds/trades", json={"account_id": acc, "fund_code": "B-CHINAARMF", "side": "buy",
                                         "trade_date": "2026-10-30", "units": "10", "amount": "80", "held_before": True})
    assert r2.json()["fund_id"] == r.json()["fund_id"]
    # a failed trade leaves no new fund behind
    bad = api.post("/funds/trades", json={"account_id": acc, "fund_code": "B-INNOTECH", "side": "buy",
                                          "trade_date": "2026-10-30", "units": "1", "amount": "1"})
    assert bad.status_code == 422 and rows(db, "SELECT id FROM ledger.funds WHERE code = 'B-INNOTECH'") == []
    assert api.post("/funds/trades", json={"fund_id": r.json()["fund_id"], "account_id": acc, "fund_code": "X",
                                           "side": "buy", "trade_date": "2026-10-30", "units": "1", "amount": "1",
                                           "held_before": True}).status_code == 422


def test_finnomena_client_reads_the_public_fund_api():
    seen = []

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        if req.url.path.endswith("/NOPE/latest"):
            return httpx.Response(404, json={"status": False, "error_code": "05"})
        if req.url.path.endswith("/BROKEN/latest"):
            return httpx.Response(500, text="oops")
        if req.url.path.endswith("/FLAKY/latest") and len([u for u in seen if "FLAKY" in u]) == 1:
            return httpx.Response(502, text="bad gateway")
        if req.url.path.endswith("/latest"):
            return httpx.Response(200, json={"status": True, "data": {"short_code": "SCBS&P500",
                                                                      "date": "2026-10-29T00:00:00Z", "value": 29.1027}})
        if req.url.path.endswith("/nav/q"):
            return httpx.Response(200, json={"status": True, "data": {"navs": [
                {"date": "2026-10-28T00:00:00Z", "value": 29.2}, {"date": "2026-10-27T00:00:00Z", "value": 29.0}]}})
        return httpx.Response(200, json={"status": True, "data": {"name_th": "ไทยพาณิชย์ S&P500", "name_en": "SCB S&P500"}})

    src = FinnomenaNav("https://example.test/funds", backoff=0, transport=httpx.MockTransport(handler))
    assert src.latest("SCBS&P500") == (date(2026, 10, 29), Decimal("29.1027"))
    assert seen[-1] == "https://example.test/funds/SCBS%26P500/latest"
    assert src.name("SCBS&P500") == "ไทยพาณิชย์ S&P500"
    assert src.history("X", date.today()) == [(date(2026, 10, 27), Decimal("29.0")), (date(2026, 10, 28), Decimal("29.2"))]
    assert seen[-1].endswith("/X/nav/q?range=1W")
    with pytest.raises(FundNotListed):
        src.latest("NOPE")
    with pytest.raises(NavError):
        src.latest("BROKEN")
    assert len([u for u in seen if "BROKEN" in u]) == 3
    assert src.latest("FLAKY") == (date(2026, 10, 29), Decimal("29.1027"))   # a 502, then the answer
