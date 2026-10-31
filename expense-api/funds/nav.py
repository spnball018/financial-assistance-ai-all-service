"""NAV of Thai mutual funds from Finnomena's public fund API (the data behind finnomena.com/fund/<code>),
plus an in-memory fake for tests. Funds are looked up by their short code, e.g. B-CHINAARMF."""
import os
import time
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional, Protocol
from urllib.parse import quote

import httpx

DEFAULT_URL = "https://www.finnomena.com/fn3/api/fund/v2/public/funds"
# the history endpoint takes a range, not dates: the shortest one that reaches back far enough
_RANGES = [(7, "1W"), (31, "1M"), (92, "3M"), (183, "6M"), (366, "1Y"), (1096, "3Y"), (1827, "5Y")]


class NavError(Exception):
    """Finnomena could not be reached or answered something unexpected."""


class FundNotListed(NavError):
    """Finnomena has no fund with this code."""


class NoNavOn(NavError):
    """The fund has no NAV on or shortly before the date asked for (e.g. it did not exist yet)."""


class NavSource(Protocol):
    def latest(self, code: str) -> tuple[date, Decimal]:
        """The newest NAV and its date."""

    def name(self, code: str) -> Optional[str]:
        """The fund's Thai name (English when there is none)."""

    def history(self, code: str, since: date) -> list[tuple[date, Decimal]]:
        """NAVs from `since` (or a little earlier) to today, oldest first."""


def nav_on(source: NavSource, code: str, day: date) -> tuple[date, Decimal]:
    """The NAV of `day`, or of the last business day before it (a trade on a holiday gets no NAV of its own)."""
    rows = [r for r in source.history(code, day - timedelta(days=7)) if r[0] <= day]
    if not rows:
        raise NoNavOn(f"Finnomena has no NAV of {code} on or just before {day}")
    return rows[-1]


class FinnomenaNav:
    def __init__(self, base_url: str = DEFAULT_URL, *, retries: int = 3, backoff: float = 0.5,
                 transport: Optional[httpx.BaseTransport] = None):
        self._retries = retries
        self._backoff = backoff
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=15, transport=transport,
                                  headers={"User-Agent": "Mozilla/5.0 (expense-tracker)"})

    def _get(self, code: str, path: str = "", **params) -> dict:
        url = f"/{quote(code.strip(), safe='')}{path}"
        for attempt in range(self._retries):   # Finnomena answers 502 now and then
            try:
                r = self._http.get(url, params=params)
            except httpx.HTTPError as e:
                if attempt + 1 == self._retries:
                    raise NavError(f"Finnomena: {e}") from None
            else:
                if r.status_code < 500 or attempt + 1 == self._retries:
                    break
            time.sleep(self._backoff * (attempt + 1))
        if r.status_code == 404:
            raise FundNotListed(code)
        try:
            body = r.json()
        except ValueError:
            raise NavError(f"Finnomena returned {r.status_code}") from None
        if r.status_code != 200 or not body.get("status") or not isinstance(body.get("data"), dict):
            raise NavError(f"Finnomena returned {r.status_code}: {body.get('error_message') or 'no data'}")
        return body["data"]

    def latest(self, code: str) -> tuple[date, Decimal]:
        d = self._get(code, "/latest")
        if d.get("value") is None or not d.get("date"):
            raise NavError(f"Finnomena has no NAV for {code}")
        return date.fromisoformat(d["date"][:10]), Decimal(str(d["value"]))

    def name(self, code: str) -> Optional[str]:
        d = self._get(code)
        return d.get("name_th") or d.get("name_en") or None

    def history(self, code: str, since: date) -> list[tuple[date, Decimal]]:
        days = (date.today() - since).days
        rng = next((r for limit, r in _RANGES if days <= limit), "MAX")
        navs = self._get(code, "/nav/q", range=rng).get("navs") or []
        return sorted((date.fromisoformat(n["date"][:10]), Decimal(str(n["value"])))
                      for n in navs if n.get("value") is not None and n.get("date"))

    def close(self) -> None:
        self._http.close()


def source_from_env() -> Optional[NavSource]:
    """FUND_NAV_URL overrides the API's address; FUND_NAV_URL=off turns fetching off (None)."""
    url = os.getenv("FUND_NAV_URL", DEFAULT_URL)
    return None if url.lower() == "off" else FinnomenaNav(url)


class FakeNav:
    """navs: {code: [(date, nav), ...]}; names: {code: name}. Codes not in navs are not listed."""

    def __init__(self, navs: dict[str, list[tuple[date, Decimal]]], names: Optional[dict[str, str]] = None):
        self.navs = {c: sorted(v) for c, v in navs.items()}
        self.names = names or {}
        self.calls: list[str] = []

    def _rows(self, code: str) -> list[tuple[date, Decimal]]:
        self.calls.append(code)
        if code not in self.navs:
            raise FundNotListed(code)
        return self.navs[code]

    def latest(self, code):
        rows = self._rows(code)
        if not rows:
            raise NavError(f"no NAV for {code}")
        return rows[-1]

    def name(self, code):
        self._rows(code)
        return self.names.get(code)

    def history(self, code, since):
        return [r for r in self._rows(code) if r[0] >= since]
