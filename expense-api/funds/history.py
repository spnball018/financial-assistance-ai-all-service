"""Daily NAV history (ledger.fund_navs) for charts and analysis.

- sync(): for every fund code someone holds, fetch the NAVs Finnomena has since the last stored day (the
  whole history the first time) and store them; then move each fund's latest NAV (funds.nav) forward. Run
  every day by funds/scheduler.py, or on demand from POST /funds/navs/sync. One code failing does not stop
  the others.
- series(): one code's NAVs; value_history(): what a fund account was worth each day (units held that day
  x that day's NAV) next to its cost, from the user's trades.

fund_navs is public market data shared by all users; the per-user functions only read the caller's funds
and trades."""
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Literal, Optional

from sqlalchemy import text

from funds.nav import FundNotListed, NavError, NavSource
from funds.service import CENT, _investment_account
from ledger.transactions import Invalid

log = logging.getLogger(__name__)

EARLIEST = date(1990, 1, 1)     # "from the beginning": Finnomena answers its MAX range
OVERLAP = timedelta(days=7)     # refetch the last week: a NAV can be corrected after it is published
Interval = Literal["day", "week", "month"]


def _code(code: str) -> str:
    return code.strip().upper()


def tracked_codes(conn, uid: Optional[int] = None) -> list[str]:
    """Codes of every fund anyone holds (or only this user's)."""
    return [r[0] for r in conn.execute(text(
        "SELECT DISTINCT upper(trim(code)) FROM ledger.funds WHERE CAST(:u AS integer) IS NULL OR user_id = :u "
        "ORDER BY 1"), {"u": uid})]


def _sync_code(engine, source: NavSource, code: str) -> int:
    with engine.connect() as conn:
        last = conn.execute(text("SELECT max(nav_date) FROM ledger.fund_navs WHERE code = :c"), {"c": code}).scalar()
    rows = source.history(code, last - OVERLAP if last else EARLIEST)
    if not rows:
        return 0
    with engine.begin() as conn:
        return conn.execute(text("""
            INSERT INTO ledger.fund_navs (code, nav_date, nav)
            SELECT :c, d, n FROM unnest(CAST(:d AS date[]), CAST(:n AS numeric[])) AS x(d, n)
            ON CONFLICT (code, nav_date) DO UPDATE SET nav = EXCLUDED.nav, fetched_at = now()
              WHERE ledger.fund_navs.nav <> EXCLUDED.nav
            RETURNING 1"""), {"c": code, "d": [r[0] for r in rows], "n": [r[1] for r in rows]}).rowcount


def _update_latest(engine, codes: list[str]) -> int:
    """funds.nav = the newest stored NAV, unless the user typed in a NAV for a later (or the same) day."""
    with engine.begin() as conn:
        return conn.execute(text("""
            UPDATE ledger.funds f SET nav = n.nav, nav_date = n.nav_date, nav_source = 'finnomena'
            FROM (SELECT DISTINCT ON (code) code, nav_date, nav FROM ledger.fund_navs
                  WHERE code = ANY(:codes) ORDER BY code, nav_date DESC) n
            WHERE upper(trim(f.code)) = n.code
              AND (f.nav_date IS NULL OR n.nav_date > f.nav_date
                   OR (n.nav_date = f.nav_date AND f.nav_source IS DISTINCT FROM 'user' AND f.nav <> n.nav))"""),
            {"codes": codes}).rowcount


def sync(engine, source: NavSource, codes: Optional[Iterable[str]] = None) -> dict:
    """Fetch and store NAVs of these codes (default: every tracked code). Returns what happened."""
    if codes is None:
        with engine.connect() as conn:
            codes = tracked_codes(conn)
    codes = sorted({_code(c) for c in codes})
    added, failed, ok = 0, [], []
    for code in codes:
        try:
            added += _sync_code(engine, source, code)
            ok.append(code)
        except FundNotListed:
            failed.append({"code": code, "error": "ไม่พบกองทุนนี้ใน Finnomena"})
        except NavError as e:
            failed.append({"code": code, "error": str(e)})
    funds_updated = _update_latest(engine, ok) if ok else 0
    return {"codes": len(codes), "navs_saved": added, "funds_updated": funds_updated, "failed": failed}


def status(conn, uid: int) -> list[dict]:
    """For each of the user's fund codes: how much history is stored and when it was last fetched."""
    return [dict(r._mapping) for r in conn.execute(text("""
        SELECT c.code, min(n.nav_date) AS first_date, max(n.nav_date) AS last_date, count(n.nav_date) AS days,
               max(n.fetched_at) AS fetched_at
        FROM (SELECT DISTINCT upper(trim(code)) AS code FROM ledger.funds WHERE user_id = :u) c
        LEFT JOIN ledger.fund_navs n ON n.code = c.code
        GROUP BY c.code ORDER BY c.code"""), {"u": uid})]


# ── reading ───────────────────────────────────────────────────────────────────────────────────────

def _bucket(d: date, interval: Interval):
    if interval == "week":
        return d.isocalendar()[:2]
    if interval == "month":
        return d.year, d.month
    return d


def downsample(points: list[dict], interval: Interval) -> list[dict]:
    """The last point of each week or month (points are oldest first)."""
    if interval == "day":
        return points
    out: dict = {}
    for p in points:
        out[_bucket(p["date"], interval)] = p
    return list(out.values())


def stats(points: list[dict], key: str) -> Optional[dict]:
    """Change over the period, high, low and the deepest fall from a high (max drawdown)."""
    vals = [(p["date"], p[key]) for p in points if p.get(key)]
    if not vals:
        return None
    first, last = vals[0][1], vals[-1][1]
    peak, worst, worst_at = vals[0][1], Decimal(0), None
    for d, v in vals:
        peak = max(peak, v)
        dd = (v - peak) / peak
        if dd < worst:
            worst, worst_at = dd, d
    return {"from": vals[0][0], "to": vals[-1][0], "first": first, "last": last, "change": last - first,
            "change_pct": ((last - first) / first * 100).quantize(CENT), "high": max(v for _, v in vals),
            "low": min(v for _, v in vals), "max_drawdown_pct": (worst * 100).quantize(CENT), "max_drawdown_at": worst_at}


def series(conn, code: str, since: Optional[date] = None, interval: Interval = "day") -> dict:
    code = _code(code)
    points = [{"date": r.nav_date, "nav": r.nav} for r in conn.execute(text(
        "SELECT nav_date, nav FROM ledger.fund_navs WHERE code = :c AND nav_date >= :s ORDER BY nav_date"),
        {"c": code, "s": since or EARLIEST})]
    return {"code": code, "stats": stats(points, "nav"), "points": downsample(points, interval)}


def value_history(conn, uid: int, account_id: int, since: Optional[date] = None,
                  interval: Interval = "day") -> dict:
    """What the account's funds were worth each day: units held that day x the NAV of that day (the last one
    before it on holidays; the trade's NAV before history starts), next to the cost (average cost, like the
    portfolio). Days are the days with a NAV of any fund held, from the first trade."""
    acct = _investment_account(conn, uid, account_id)
    funds = {r.id: _code(r.code) for r in conn.execute(text(
        "SELECT id, code FROM ledger.funds WHERE user_id = :u AND account_id = :a"), {"u": uid, "a": account_id})}
    trades = [dict(r._mapping) for r in conn.execute(text(
        "SELECT fund_id, side, trade_date, units, amount, nav FROM ledger.fund_trades "
        "WHERE user_id = :u AND fund_id = ANY(:f) ORDER BY trade_date, id"), {"u": uid, "f": list(funds)})]
    base = {"account_id": acct["id"], "name": acct["name"], "funds": sorted(set(funds.values()))}
    if not trades:
        return {**base, "stats": None, "points": []}
    start = trades[0]["trade_date"]
    navs: dict[str, dict[date, Decimal]] = {c: {} for c in funds.values()}
    for r in conn.execute(text("SELECT code, nav_date, nav FROM ledger.fund_navs WHERE code = ANY(:c) AND nav_date >= :s"),
                          {"c": list(navs), "s": start}):
        navs[r.code][r.nav_date] = r.nav
    days = sorted({d for by_day in navs.values() for d in by_day} | {t["trade_date"] for t in trades})

    units = {f: Decimal(0) for f in funds}
    cost = {f: Decimal(0) for f in funds}
    last_nav: dict[int, Optional[Decimal]] = {f: None for f in funds}
    points, i = [], 0
    for d in days:
        while i < len(trades) and trades[i]["trade_date"] <= d:
            t = trades[i]
            f = t["fund_id"]
            if t["side"] == "buy":
                units[f] += t["units"]
                cost[f] += t["amount"]
            elif units[f]:
                out = cost[f] if t["units"] >= units[f] else (cost[f] * t["units"] / units[f]).quantize(CENT)
                cost[f] -= out
                units[f] = max(units[f] - t["units"], Decimal(0))
            if t["nav"] and navs[funds[f]].get(d) is None:
                last_nav[f] = t["nav"]
            i += 1
        by_fund = {}
        for f, code in funds.items():
            if navs[code].get(d) is not None:
                last_nav[f] = navs[code][d]
            if units[f] and last_nav[f] is not None:
                by_fund[code] = by_fund.get(code, Decimal(0)) + (units[f] * last_nav[f]).quantize(CENT)
        if since and d < since:
            continue
        points.append({"date": d, "value": sum(by_fund.values(), Decimal(0)), "cost": sum(cost.values(), Decimal(0)),
                       "by_fund": by_fund})
    return {**base, "stats": stats(points, "value"), "points": downsample(points, interval)}


def check_interval(interval: str) -> Interval:
    if interval not in ("day", "week", "month"):
        raise Invalid("interval must be day, week or month")
    return interval  # type: ignore[return-value]
