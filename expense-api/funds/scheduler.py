"""Fetch fund NAVs every day (funds/history.py sync), inside expense-api: started by app/main.py's lifespan.

FUND_NAV_SYNC_TIMES: Bangkok times of day, default "08:00,20:00" (Thai funds publish a day's NAV that evening
or the next morning; running twice catches either). "off" turns it off; the tests do. It also runs once a
minute after start-up, which backfills the history of funds added while the server was down. A Postgres
advisory lock keeps two processes from syncing at once. FUND_NAV_URL=off (no NAV source) also turns it off.
"""
import asyncio
import logging
import os
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import anyio
from sqlalchemy import text

from app.db import get_engine
from funds import history
from funds.nav import source_from_env

log = logging.getLogger(__name__)
BANGKOK = ZoneInfo("Asia/Bangkok")
STARTUP_DELAY = 60
_LOCK = 734_210_025      # pg advisory lock key for the NAV sync


def sync_times() -> list[time]:
    raw = os.getenv("FUND_NAV_SYNC_TIMES", "08:00,20:00").strip()
    if raw.lower() in ("", "off"):
        return []
    return sorted(time.fromisoformat(t.strip()) for t in raw.split(","))


def seconds_until_next(times: list[time], now: Optional[datetime] = None) -> float:
    now = now or datetime.now(BANGKOK)
    for day in (0, 1):
        for t in times:
            at = datetime.combine(now.date() + timedelta(days=day), t, BANGKOK)
            if at > now:
                return (at - now).total_seconds()
    raise ValueError("no sync times")


def sync_once() -> Optional[dict]:
    """One sync of every tracked code, unless another process is syncing. None when skipped."""
    source = source_from_env()
    if source is None:
        return None
    engine = get_engine()
    with engine.connect() as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK}).scalar():
            return None
        try:
            result = history.sync(engine, source)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK})
            lock.commit()
    log.info("fund NAV sync: %s", {k: v for k, v in result.items() if k != "failed"})
    for f in result["failed"]:
        log.warning("fund NAV sync: %s: %s", f["code"], f["error"])
    return result


async def run() -> None:
    times = sync_times()
    if not times:
        return
    await asyncio.sleep(STARTUP_DELAY)
    while True:
        try:
            await anyio.to_thread.run_sync(sync_once)
        except Exception:   # a bad day (DB restart, ...) must not stop tomorrow's sync
            log.exception("fund NAV sync failed")
        await asyncio.sleep(seconds_until_next(times))
