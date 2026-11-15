"""Daily NAV history (funds/history.py, funds/scheduler.py): backfill then new days, corrections, the latest
NAV moved forward, series with stats, what an account was worth each day, and the daily job's lock."""
from datetime import date, datetime, time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import auth
from app.auth import Principal, require_user
from app.main import app
from app.routers.funds import get_nav_source
from funds import history, scheduler
from funds.nav import FakeNav
from tests.test_funds import BANK, api, buy, port, rows, sell  # noqa: F401  (fixtures)

USA = "K-USA-A(A)"
NAVS = [(date(2026, 10, 1), Decimal("10.0000")), (date(2026, 10, 2), Decimal("10.5000")),
        (date(2026, 10, 15), Decimal("9.0000")), (date(2026, 10, 29), Decimal("12.3456"))]


class Recording(FakeNav):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.since: list[tuple[str, date]] = []

    def history(self, code, since):
        self.since.append((code, since))
        return super().history(code, since)


@pytest.fixture
def fake(api, db):
    with db.begin() as conn:
        conn.execute(text("TRUNCATE ledger.fund_navs"))
    f = Recording({USA: list(NAVS)})
    app.dependency_overrides[get_nav_source] = lambda: f
    yield f
    with db.begin() as conn:
        conn.execute(text("TRUNCATE ledger.fund_navs"))


def test_first_sync_backfills_then_only_new_days(api, port, fake, db):
    r = api.post("/funds/navs/sync").json()
    assert (r["codes"], r["navs_saved"], r["funds_updated"], r["failed"]) == (1, 4, 1, [])
    assert fake.since == [(USA, history.EARLIEST)]
    assert rows(db, "SELECT code, count(*) FROM ledger.fund_navs GROUP BY code") == [(USA, 4)]
    # the fund's latest NAV moved forward
    assert api.get("/funds/portfolio").json()[0]["funds"][0]["nav"] == 12.3456

    # nothing new: refetches the last week only, saves nothing
    assert api.post("/funds/navs/sync").json()["navs_saved"] == 0
    assert fake.since[-1] == (USA, date(2026, 10, 22))
    # a new day, and a correction inside the last week
    fake.navs[USA] = NAVS[:3] + [(date(2026, 10, 29), Decimal("12.3500")), (date(2026, 10, 30), Decimal("12.4000"))]
    assert api.post("/funds/navs/sync").json()["navs_saved"] == 2
    assert rows(db, "SELECT nav FROM ledger.fund_navs WHERE nav_date = '2026-10-29'") == [(Decimal("12.3500"),)]

    status = api.get("/funds/navs/status").json()
    assert [(s["code"], s["first_date"], s["last_date"], s["days"]) for s in status] == [
        (USA, "2026-10-01", "2026-10-30", 5)]


def test_a_nav_typed_by_the_user_for_a_later_day_is_kept_and_failures_are_reported(api, port, fake, db):
    acc, fund = port
    api.patch(f"/funds/{fund}", json={"nav": "13.0000", "nav_date": "2026-11-02"})
    api.post("/funds", json={"account_id": acc, "code": "NOT-LISTED"})
    r = api.post("/funds/navs/sync").json()
    assert r["failed"] == [{"code": "NOT-LISTED", "error": "ไม่พบกองทุนนี้ใน Finnomena"}]
    assert r["navs_saved"] == 4 and r["funds_updated"] == 0
    f = next(x for x in api.get("/funds/portfolio").json()[0]["funds"] if x["code"] == USA)
    assert (f["nav"], f["nav_date"], f["nav_source"]) == (13.0, "2026-11-02", "user")


def test_series_with_stats(api, port, fake):
    api.post("/funds/navs/sync")
    s = api.get("/funds/navs", params={"code": "k-usa-a(a)"}).json()     # any case
    assert s["code"] == USA and [p["date"] for p in s["points"]] == ["2026-10-01", "2026-10-02", "2026-10-15", "2026-10-29"]
    st = s["stats"]
    assert (st["first"], st["last"], st["change_pct"], st["high"], st["low"]) == (10.0, 12.3456, 23.46, 12.3456, 9.0)
    assert (st["max_drawdown_pct"], st["max_drawdown_at"]) == (-14.29, "2026-10-15")   # 10.5 -> 9.0
    assert len(api.get("/funds/navs", params={"code": USA, "interval": "month"}).json()["points"]) == 1
    assert api.get("/funds/navs", params={"code": USA, "since": "2026-10-10"}).json()["stats"]["first"] == 9.0
    assert api.get("/funds/navs", params={"code": USA, "interval": "year"}).status_code == 422


def test_value_history_follows_units_held_each_day(api, port, fake):
    acc, fund = port
    assert buy(api, fund, "1000", "100", day="2026-10-01", held_before=True).status_code == 201
    assert sell(api, fund, "400", "40", day="2026-10-20", counter_account_id=BANK).status_code == 201
    api.post("/funds/navs/sync")
    h = api.get(f"/funds/accounts/{acc}/value-history").json()
    assert [(p["date"], p["value"], p["cost"]) for p in h["points"]] == [
        ("2026-10-01", 1000.0, 1000.0), ("2026-10-02", 1050.0, 1000.0), ("2026-10-15", 900.0, 1000.0),
        ("2026-10-20", 600.0, 600.0),            # no NAV that day: the sell's NAV (400 / 40 = 10)
        ("2026-10-29", 740.74, 600.0)]           # 60 units x 12.3456
    assert h["points"][-1]["by_fund"] == {USA: 740.74} and h["funds"] == [USA]
    assert h["stats"]["max_drawdown_pct"] == -42.86            # 1050 -> 600 (a sale counts as a fall in value)
    assert [p["date"] for p in api.get(f"/funds/accounts/{acc}/value-history",
                                       params={"since": "2026-10-16", "interval": "week"}).json()["points"]] == [
        "2026-10-20", "2026-10-29"]


def test_value_history_is_private(api, port, fake, db):
    acc, _ = port
    kid = auth.add_user("kid@example.com", "member")["id"]
    app.dependency_overrides[require_user] = lambda: Principal(kid, "kid@example.com", "member", "session")
    try:
        # someone else's account "does not exist", like the trade history
        res = TestClient(app).get(f"/funds/accounts/{acc}/value-history")
        assert res.status_code == 422 and "TEST fund" not in res.text
        assert TestClient(app).get("/funds/navs/status").json() == []
    finally:
        app.dependency_overrides.pop(require_user, None)


def test_scheduler_times_and_lock(db, fake, port, monkeypatch):
    monkeypatch.setenv("FUND_NAV_SYNC_TIMES", "20:00, 08:00")
    assert scheduler.sync_times() == [time(8, 0), time(20, 0)]
    now = datetime(2026, 11, 15, 21, 0, tzinfo=scheduler.BANGKOK)
    assert scheduler.seconds_until_next(scheduler.sync_times(), now) == 11 * 3600    # tomorrow 08:00
    monkeypatch.setenv("FUND_NAV_SYNC_TIMES", "off")
    assert scheduler.sync_times() == []

    monkeypatch.setattr(scheduler, "source_from_env", lambda: fake)
    with db.connect() as other:                    # another process is syncing
        other.execute(text("SELECT pg_advisory_lock(:k)"), {"k": scheduler._LOCK})
        assert scheduler.sync_once() is None
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": scheduler._LOCK})
    assert scheduler.sync_once()["navs_saved"] == 4
    monkeypatch.setattr(scheduler, "source_from_env", lambda: None)   # FUND_NAV_URL=off
    assert scheduler.sync_once() is None
