"""Read-only MCP server for Claude (claude.ai custom connector): ask about your own money in chat.

Served by expense-api itself at /mcp (Streamable HTTP, stateless, plain JSON so it passes the Cloudflare tunnel).
The MCP SDK also serves the OAuth routes (/register, /authorize, /token, /revoke, /.well-known/...); storage
and policy are in app/oauth.py. Every tool reads only the signed-in person's rows: the user id comes from the
access token (AccessToken.subject), never from the caller.

No tool changes anything. Tools reuse the functions behind the REST API and return trimmed results.
"""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal, Optional

import anyio
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from sqlalchemy import text

from app import auth, oauth
from app.db import get_engine
from funds import history as nav_history
from funds import service as funds
from ledger import balances as bal
from ledger import reports
from ledger import transactions as txns

BANGKOK = timezone(timedelta(hours=7))
MAX_POINTS = 400      # a history longer than this is sent weekly or monthly (interval "auto")
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

INSTRUCTIONS = """Personal expense tracker of a person in Thailand (amounts in THB, times in Bangkok time).
Data comes from bank emails (K PLUS), card statements (KTC), e-wallet screenshots and manual entries.
- Spending = confirmed expenses only. `pending` rows are still waiting for review and are not counted yet.
- Transfers between the person's own accounts (bank -> e-wallet, paying the credit card) are not spending.
- A payment may be split into children (order items, receipt lines); categories sit on the leaves.
- Months are YYYY-MM. Category and merchant names are often Thai; answer in the language the person uses.
This connector can only read."""


def _uid() -> int:
    """The signed-in person, from the access token only: no tool takes a user id from the caller."""
    token = get_access_token()
    if token is None or not token.subject:
        raise PermissionError("not signed in")
    return int(token.subject)


def _plain(v: Any) -> Any:
    """JSON-friendly: Decimal -> float, datetime -> Bangkok ISO time, date -> ISO date."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.astimezone(BANGKOK).isoformat(timespec="minutes")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def _month(month: Optional[str]) -> Optional[str]:
    if month is None:
        return None
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ToolError("month must be YYYY-MM, e.g. 2026-11")
    return month


def _date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ToolError("dates are YYYY-MM-DD, e.g. 2026-01-01") from None


async def _read(fn) -> Any:
    """Run fn(conn, uid) for the caller in a worker thread (the DB driver blocks)."""
    uid = _uid()

    def run():
        with get_engine().connect() as conn:
            return _plain(fn(conn, uid))
    return await anyio.to_thread.run_sync(run)


def _names(conn, uid: int) -> tuple[dict, dict]:
    accounts = {r.id: r.name for r in conn.execute(text("SELECT id, name FROM ledger.accounts WHERE user_id = :u"),
                                                   {"u": uid})}
    categories = {r.id: r.name_th for r in conn.execute(text("SELECT id, name_th FROM ledger.categories"))}
    return accounts, categories


def _txn(t: dict, accounts: dict, categories: dict) -> dict:
    """The fields a question about a transaction needs."""
    out = {"id": t["id"], "when": t["occurred_at"] if t["has_time"] else t["occurred_at"].astimezone(BANGKOK).date(),
           "amount": t["amount"], "kind": t["kind"], "status": t["status"], "merchant": t["merchant_raw"],
           "description": t["description"], "account": accounts.get(t["account_id"]),
           "category": categories.get(t["category_id"])}
    if t["counter_account_id"]:
        out["transfer_to"] = accounts.get(t["counter_account_id"])
    if t.get("note"):
        out["note"] = t["note"]
    if t.get("children"):
        out["items"] = [_txn(c, accounts, categories) for c in t["children"]]
    return {k: v for k, v in out.items() if v is not None}


def build() -> MCPServer:
    base = auth.public_base_url()
    server = MCPServer(
        "expense-tracker", title="Expense tracker", instructions=INSTRUCTIONS,
        auth_server_provider=oauth.Provider(),
        auth=AuthSettings(
            issuer_url=base, resource_server_url=base + "/mcp", required_scopes=[oauth.SCOPE],
            validate_token_resource=True,
            client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=[oauth.SCOPE],
                                                                  default_scopes=[oauth.SCOPE]),
            revocation_options=RevocationOptions(enabled=True)))

    @server.tool(annotations=READ_ONLY)
    async def monthly_summary(month: str) -> dict:
        """Spending of one month (YYYY-MM) by category with subcategories, income by category, net, money moved to
        own accounts (not spending) and how many rows still wait for review."""
        m = _month(month)
        return await _read(lambda conn, uid: reports.monthly_summary(conn, uid, m))

    @server.tool(annotations=READ_ONLY)
    async def category_expenses(month: str, category_id: int) -> dict:
        """The confirmed expenses behind one category of monthly_summary (a top-level category includes its
        subcategories), largest first."""
        m = _month(month)
        return {"items": await _read(lambda conn, uid: reports.expenses(conn, uid, m, category_id))}

    @server.tool(annotations=READ_ONLY)
    async def income(month: str) -> dict:
        """The confirmed incomes of one month, largest first."""
        m = _month(month)
        return {"items": await _read(lambda conn, uid: reports.income(conn, uid, m))}

    @server.tool(annotations=READ_ONLY)
    async def search_transactions(month: Optional[str] = None,
                                  status: Optional[Literal["pending", "confirmed", "ignored"]] = None,
                                  account_id: Optional[int] = None, text_contains: Optional[str] = None,
                                  limit: int = 50) -> dict:
        """Transactions newest first, with items under a split payment. Filter by month (YYYY-MM), review status,
        account (see accounts) and/or text in the payee, description or note (e.g. a shop name)."""
        m, n = _month(month), max(1, min(limit, 100))

        def run(conn, uid):
            accounts, categories = _names(conn, uid)
            r = txns.list_trees(conn, uid, status=status, account_id=account_id, month=m, limit=n,
                                search=(text_contains or "").strip() or None)
            return {"total": r["total"], "items": [_txn(t, accounts, categories) for t in r["items"]]}
        return await _read(run)

    @server.tool(annotations=READ_ONLY)
    async def pending_review(limit: int = 50) -> dict:
        """Rows waiting for the person to check and confirm (not counted in spending yet), newest first."""
        return await search_transactions(status="pending", limit=limit)

    @server.tool(annotations=READ_ONLY)
    async def transaction_details(transaction_id: int) -> dict:
        """Everything known about one transaction: parser fields (from/to account, fee, bank balance after),
        category and how it was chosen, the rows above it, the source email's subject and matched orders."""
        def run(conn, uid):
            d = txns.details(conn, uid, transaction_id)
            for k in ("merchant_key", "dedup_key", "memory_action", "ref_transaction_id"):
                d.pop(k, None)
            return d
        try:
            return await _read(run)
        except txns.NotFound:
            raise ToolError(f"transaction {transaction_id} not found") from None

    @server.tool(annotations=READ_ONLY)
    async def accounts() -> dict:
        """The person's accounts (bank, credit card, e-wallet, cash, investment) with ids for other tools."""
        return {"items": await _read(lambda conn, uid: [dict(r._mapping) for r in conn.execute(text(
            "SELECT id, name, type, institution, is_active FROM ledger.accounts WHERE user_id = :u "
            "ORDER BY sort_order, id"), {"u": uid})])}

    @server.tool(annotations=READ_ONLY)
    async def categories() -> dict:
        """Expense and income categories (Thai and English names) with ids, parents first."""
        return {"items": await _read(lambda conn, uid: [dict(r._mapping) for r in conn.execute(text(
            "SELECT id, parent_id, name_th, name_en, kind FROM ledger.categories "
            "ORDER BY COALESCE(parent_id, id), parent_id NULLS FIRST, sort_order"))])}

    @server.tool(annotations=READ_ONLY)
    async def account_balances() -> dict:
        """Balance of every account: confirmed, including pending, and for K PLUS the bank's own latest balance."""
        return {"items": await _read(lambda conn, uid: bal.balances(conn, uid))}

    @server.tool(annotations=READ_ONLY)
    async def account_ledger(account_id: int, limit: int = 50) -> dict:
        """Movements of one account, newest first, with the running balance after each."""
        n = max(1, min(limit, 200))

        def run(conn, uid):
            led = bal.account_ledger(conn, uid, account_id)
            return {**led, "rows": led["rows"][:n], "total_rows": len(led["rows"])}
        try:
            return await _read(run)
        except bal.NotFound:
            raise ToolError(f"account {account_id} not found") from None

    @server.tool(annotations=READ_ONLY)
    async def fund_portfolio() -> dict:
        """Investment accounts: cash, funds with units, average cost, market value and gains, and totals."""
        return {"accounts": await _read(lambda conn, uid: funds.portfolio(conn, uid))}

    @server.tool(annotations=READ_ONLY)
    async def fund_trades(account_id: int, fund_code: Optional[str] = None, limit: int = 100) -> dict:
        """Buys and sells of one investment account (account_id from accounts or fund_portfolio), newest first:
        date, fund, units, amount, NAV, where the money came from (buy) or went (sell), and the other fund of a
        switch. Optionally only one fund (fund_code, e.g. K-USXNDQ-A(A)). `unlinked` lists transfers into or
        out of the account that no trade explains yet."""
        n = max(1, min(limit, 500))
        code = (fund_code or "").strip().casefold() or None

        def run(conn, uid):
            h = funds.history(conn, uid, account_id)
            rows = [t for t in h["items"] if code is None or t["fund_code"].casefold() == code]
            keep = ("id", "trade_date", "fund_code", "side", "units", "amount", "nav", "funding", "other_account",
                    "switch_fund_code", "transfer_status", "note")
            return {"account_id": h["account_id"], "name": h["name"], "total": len(rows),
                    "trades": [{k: t[k] for k in keep if t[k] is not None} for t in rows[:n]],
                    "unlinked": h["unlinked"]}
        try:
            return await _read(run)
        except funds.Invalid as e:      # not this person's account, or not an investment account
            raise ToolError(str(e)) from None

    def _fit(points: list[dict], interval: str) -> tuple[list[dict], str]:
        """auto: daily when that is at most MAX_POINTS points, else weekly, else monthly."""
        if interval != "auto":
            return nav_history.downsample(points, nav_history.check_interval(interval)), interval
        for step in ("day", "week", "month"):
            fitted = nav_history.downsample(points, step)
            if len(fitted) <= MAX_POINTS or step == "month":
                return fitted, step

    @server.tool(annotations=READ_ONLY)
    async def fund_nav_history(fund_code: str, since: Optional[str] = None,
                               interval: Literal["auto", "day", "week", "month"] = "auto") -> dict:
        """Stored daily NAVs of one fund (by code, e.g. K-USXNDQ-A(A); see fund_portfolio), oldest first, with
        stats over the period: first, last, change_pct, high, low, max_drawdown_pct. since = YYYY-MM-DD.
        Fetched from Finnomena every day; history goes back as far as Finnomena has it."""
        start = _date(since)

        def run(conn, uid):
            s = nav_history.series(conn, fund_code, start)
            s["points"], s["interval"] = _fit(s["points"], interval)
            return s
        return await _read(run)

    @server.tool(annotations=READ_ONLY)
    async def portfolio_value_history(account_id: int, since: Optional[str] = None,
                                      interval: Literal["auto", "day", "week", "month"] = "auto") -> dict:
        """What one investment account's funds were worth over time (units held that day x that day's NAV) next
        to their cost, from the first trade, with value per fund (by_fund) and stats (change_pct,
        max_drawdown_pct). account_id from accounts or fund_portfolio; since = YYYY-MM-DD."""
        start = _date(since)

        def run(conn, uid):
            h = nav_history.value_history(conn, uid, account_id, start)
            h["points"], h["interval"] = _fit(h["points"], interval)
            return h
        try:
            return await _read(run)
        except txns.Invalid as e:      # not this person's account, or not an investment account
            raise ToolError(str(e)) from None

    return server


server = build()
# the routes of the SDK app (/mcp, /authorize, /token, ...): app/main.py mounts it under the REST API.
# TrustedHostMiddleware in app/main.py already refuses foreign Host headers; the SDK's own check would also
# refuse the public hostname.
http_app = server.streamable_http_app(
    stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
