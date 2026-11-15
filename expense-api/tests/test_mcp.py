"""The MCP connector for Claude: OAuth sign-in (register -> authorize -> consent -> token -> refresh), the tokens
open only /mcp, every tool reads only the signed-in person's rows, and no tool writes."""
import base64
import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import auth
from app.auth import require_user
from app.main import app
from funds import service as funds

BASE = "http://localhost:3000"             # PUBLIC_BASE_URL when app.mcp_server was imported (the default)
RESOURCE = BASE + "/mcp"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}


@pytest.fixture(scope="module")
def client():
    # one client for the module: the MCP session manager can start only once per process
    with TestClient(app) as c:
        yield c


@pytest.fixture
def c(client, db, monkeypatch):
    app.dependency_overrides.pop(require_user, None)        # real sign-in, not the test user
    monkeypatch.setattr("app.db._engine", db)
    monkeypatch.setenv("AUTH_MODE", "google")
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    with db.begin() as conn:
        conn.execute(text("TRUNCATE ledger.oauth_clients, ledger.auth_sessions, ledger.api_tokens CASCADE"))
        conn.execute(text("INSERT INTO ledger.transactions (user_id, account_id, node_type, kind, status, amount, "
                          "occurred_at, source, dedup_key, merchant_raw) VALUES (1, 1, 'payment', 'expense', "
                          "'confirmed', 89, '2026-11-05 12:00+07', 'manual', 'm:1', 'ร้านกาแฟ Amazon')"))
    client.cookies.clear()
    return client


def register(c, redirect=CALLBACK):
    return c.post("/register", json={"redirect_uris": [redirect], "token_endpoint_auth_method": "none",
                                     "grant_types": ["authorization_code", "refresh_token"],
                                     "response_types": ["code"], "client_name": "Claude"})


def sign_in(c, uid=1):
    secret, _ = auth.create_session(uid, "pytest")
    c.cookies.set(auth.SESSION_COOKIE, secret)


def approve(c, client_id, decision="allow"):
    """/authorize -> consent page -> POST; returns the redirect to Claude's callback."""
    res = c.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
                                      "code_challenge": CHALLENGE, "code_challenge_method": "S256", "state": "st8",
                                      "scope": "read", "resource": RESOURCE}, follow_redirects=False)
    assert res.status_code == 302, res.text
    consent = urlsplit(res.headers["location"])
    assert f"{consent.scheme}://{consent.netloc}{consent.path}" == BASE + "/api/oauth/consent"
    page = c.get("/oauth/consent?" + consent.query)
    assert page.status_code == 200 and "แก้ไขหรือลบอะไรไม่ได้" in page.text
    form = dict(re.findall(r'name="(req|csrf)" value="([^"]+)"', page.text))
    return c.post("/oauth/consent", data={**form, "decision": decision}, follow_redirects=False)


def token(c, client_id, **form):
    return c.post("/token", data={"client_id": client_id, **form})


def connect(c, uid=1):
    client_id = register(c).json()["client_id"]
    sign_in(c, uid)
    code = parse_qs(urlsplit(approve(c, client_id).headers["location"]).query)["code"][0]
    c.cookies.clear()
    res = token(c, client_id, grant_type="authorization_code", code=code, redirect_uri=CALLBACK,
                code_verifier=VERIFIER, resource=RESOURCE)
    assert res.status_code == 200, res.text
    return client_id, res.json()


def call(c, access, method, params=None):
    res = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                 headers={**MCP_HEADERS, "Authorization": f"Bearer {access}"})
    return res


def tool(c, access, name, **args):
    res = call(c, access, "tools/call", {"name": name, "arguments": args})
    assert res.status_code == 200, res.text
    result = res.json()["result"]
    assert not result.get("isError"), result
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


def test_metadata_points_at_this_server(c):
    meta = c.get("/.well-known/oauth-authorization-server").json()
    assert meta["issuer"].rstrip("/") == BASE and meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["registration_endpoint"] == BASE + "/register"
    pr = c.get("/.well-known/oauth-protected-resource/mcp").json()
    assert pr["resource"] == RESOURCE


def test_only_claude_or_localhost_may_register(c):
    assert register(c).status_code == 201
    assert register(c, "http://localhost:6274/oauth/callback").status_code == 201
    # a client that follows the metadata (client_secret_post) gets a secret and can use it at /token
    res = c.post("/register", json={"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "client_secret_post",
                                    "client_name": "Claude"})
    assert res.status_code == 201 and res.json()["client_secret"]
    secret_client = res.json()
    sign_in(c)
    code = parse_qs(urlsplit(approve(c, secret_client["client_id"]).headers["location"]).query)["code"][0]
    c.cookies.clear()
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK, "code_verifier": VERIFIER,
            "resource": RESOURCE}
    assert token(c, secret_client["client_id"], **form).status_code == 401          # no secret
    assert token(c, secret_client["client_id"], client_secret=secret_client["client_secret"], **form).status_code == 200
    for bad in ("https://evil.example/cb", "http://claude.ai/api/mcp/auth_callback", "https://claude.ai.evil.example/cb"):
        assert register(c, bad).status_code == 400, bad


def test_sign_in_flow_and_tools(c):
    client_id = register(c).json()["client_id"]
    # signed out: the consent page sends the person to Google sign-in first, then back
    res = c.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
                                      "code_challenge": CHALLENGE, "code_challenge_method": "S256", "state": "s",
                                      "resource": RESOURCE}, follow_redirects=False)
    login = c.get(res.headers["location"].replace(BASE + "/api", ""), follow_redirects=False)
    assert login.status_code == 303 and login.headers["location"].startswith("/api/auth/login?next=%2Fapi%2Foauth%2Fconsent")

    # denied
    sign_in(c)
    assert parse_qs(urlsplit(approve(c, client_id, "deny").headers["location"]).query) == {
        "error": ["access_denied"], "state": ["st8"]}
    # approved: a code for Claude's callback, exchanged once with the right verifier
    back = approve(c, client_id)
    assert back.headers["location"].startswith(CALLBACK + "?")
    q = parse_qs(urlsplit(back.headers["location"]).query)
    assert q["state"] == ["st8"]
    c.cookies.clear()
    bad = token(c, client_id, grant_type="authorization_code", code=q["code"][0], redirect_uri=CALLBACK,
                code_verifier="w" * 64, resource=RESOURCE)
    assert bad.status_code == 400
    ok = token(c, client_id, grant_type="authorization_code", code=q["code"][0], redirect_uri=CALLBACK,
               code_verifier=VERIFIER, resource=RESOURCE)
    assert ok.status_code == 200 and ok.json()["access_token"].startswith("mcp_at_")
    again = token(c, client_id, grant_type="authorization_code", code=q["code"][0], redirect_uri=CALLBACK,
                  code_verifier=VERIFIER, resource=RESOURCE)
    assert again.status_code == 400

    access = ok.json()["access_token"]
    tools = call(c, access, "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in tools} >= {"monthly_summary", "search_transactions", "account_balances", "pending_review"}
    assert all(t["annotations"]["readOnlyHint"] is True for t in tools)
    found = tool(c, access, "search_transactions", text_contains="amazon")
    assert found["total"] == 1 and found["items"][0]["merchant"] == "ร้านกาแฟ Amazon"
    assert found["items"][0]["amount"] == 89.0 and found["items"][0]["when"].startswith("2026-11-05T12:00")
    summary = tool(c, access, "monthly_summary", month="2026-11")
    assert summary["month"] == "2026-11"
    bad_month = call(c, access, "tools/call", {"name": "monthly_summary", "arguments": {"month": "Nov"}}).json()
    assert bad_month["result"]["isError"]


def test_consent_needs_the_forms_csrf_token(c):
    client_id = register(c).json()["client_id"]
    sign_in(c)
    res = c.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
                                      "code_challenge": CHALLENGE, "code_challenge_method": "S256", "state": "s",
                                      "resource": RESOURCE}, follow_redirects=False)
    req = parse_qs(urlsplit(res.headers["location"]).query)["req"][0]
    forged = c.post("/oauth/consent", data={"req": req, "csrf": "x", "decision": "allow"}, follow_redirects=False)
    assert forged.status_code == 400
    with auth.get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM ledger.oauth_codes")).scalar() == 0


def test_tokens_are_separate_from_the_rest_api(c):
    _, tokens = connect(c)
    assert c.get("/transactions", headers={"Authorization": f"Bearer {tokens['access_token']}"}).status_code == 401
    api_token = auth.create_token(1, "cli", None)["token"]
    assert call(c, api_token, "tools/list").status_code == 401
    assert c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=MCP_HEADERS).status_code == 401


def test_refresh_rotates_and_revoking_the_connection_ends_it(c):
    client_id, first = connect(c)
    res = token(c, client_id, grant_type="refresh_token", refresh_token=first["refresh_token"])
    assert res.status_code == 200
    second = res.json()
    assert token(c, client_id, grant_type="refresh_token", refresh_token=first["refresh_token"]).status_code == 400
    assert call(c, first["access_token"], "tools/list").status_code == 401
    assert call(c, second["access_token"], "tools/list").status_code == 200

    sign_in(c)
    conns = c.get("/auth/mcp-connections").json()
    assert [x["client_name"] for x in conns] == ["Claude"]
    assert c.delete(f"/auth/mcp-connections/{conns[0]['grant_id']}", headers={"X-Requested-With": "fetch"}).status_code == 204
    assert call(c, second["access_token"], "tools/list").status_code == 401
    assert token(c, client_id, grant_type="refresh_token", refresh_token=second["refresh_token"]).status_code == 400


def test_each_person_reads_only_their_own_rows(c):
    kid = auth.add_user("kid@example.com", "member")["id"]
    _, owner = connect(c, 1)
    _, other = connect(c, kid)
    assert tool(c, owner["access_token"], "search_transactions")["total"] == 1
    assert tool(c, other["access_token"], "search_transactions")["total"] == 0
    assert tool(c, other["access_token"], "accounts")["items"] == []
    # the owner's transaction and account by id: refused for anyone else, without revealing them
    assert tool(c, owner["access_token"], "transaction_details", transaction_id=1)["merchant_raw"] == "ร้านกาแฟ Amazon"
    for name, args in (("transaction_details", {"transaction_id": 1}), ("account_ledger", {"account_id": 1})):
        res = call(c, other["access_token"], "tools/call", {"name": name, "arguments": args}).json()["result"]
        assert res["isError"] and "Amazon" not in json.dumps(res, ensure_ascii=False), name
    for name in ("account_balances", "fund_portfolio", "pending_review"):
        assert "Amazon" not in json.dumps(tool(c, other["access_token"], name), ensure_ascii=False), name
    assert tool(c, other["access_token"], "monthly_summary", month="2026-11")["total"] == 0
    assert tool(c, owner["access_token"], "monthly_summary", month="2026-11")["total"] == 89
    with auth.get_engine().begin() as conn:
        conn.execute(text("UPDATE ledger.users SET is_active = false WHERE id = :k"), {"k": kid})
    assert call(c, other["access_token"], "tools/list").status_code == 401
    json.dumps(owner)   # tokens are plain JSON


@pytest.fixture
def fund_account(db):
    """An investment account of user 1; dropped afterwards (other tests expect only the seeded accounts)."""
    def drop():
        with db.begin() as conn:
            conn.execute(text("TRUNCATE ledger.fund_trades, ledger.funds RESTART IDENTITY CASCADE"))
            conn.execute(text("DELETE FROM ledger.transactions"))
            conn.execute(text("DELETE FROM ledger.accounts WHERE name = 'TEST fund MCP'"))
    drop()
    with db.begin() as conn:
        acct = conn.execute(text("INSERT INTO ledger.accounts (user_id, name, type) "
                                 "VALUES (1, 'TEST fund MCP', 'investment') RETURNING id")).scalar()
    yield acct
    drop()


def test_fund_trades(c, db, fund_account):
    kid = auth.add_user("kid@example.com", "member")["id"]
    acct = fund_account
    with db.begin() as conn:
        usx = funds.create_fund(conn, 1, account_id=acct, code="K-USXNDQ-A(A)")["id"]
        set50 = funds.create_fund(conn, 1, account_id=acct, code="SCBSET50")["id"]
        funds.add_trade(conn, 1, fund_id=usx, side="buy", trade_date=date(2026, 9, 1), units=Decimal("850.1234"),
                        amount=Decimal("10000"), nav=Decimal("11.763"), counter_account_id=1)
        funds.add_trade(conn, 1, fund_id=set50, side="buy", trade_date=date(2026, 8, 15), units=Decimal("1000"),
                        amount=Decimal("8000"), counter_account_id=1, note="DCA")
        funds.add_trade(conn, 1, fund_id=set50, side="sell", trade_date=date(2026, 10, 20), units=Decimal("300"),
                        amount=Decimal("2550"), counter_account_id=1)
    _, owner = connect(c, 1)
    _, other = connect(c, kid)

    h = tool(c, owner["access_token"], "fund_trades", account_id=acct)
    assert h["total"] == 3 and h["unlinked"] == []
    assert [(t["trade_date"], t["fund_code"], t["side"]) for t in h["trades"]] == [
        ("2026-10-20", "SCBSET50", "sell"), ("2026-09-01", "K-USXNDQ-A(A)", "buy"), ("2026-08-15", "SCBSET50", "buy")]
    first = h["trades"][1]
    assert first["units"] == 850.1234 and first["amount"] == 10000.0 and first["nav"] == 11.763
    assert first["other_account"] == "KBank ใช้จ่าย" and first["funding"] == "new_transfer"
    assert h["trades"][2]["note"] == "DCA" and h["trades"][2]["nav"] == 8.0      # no NAV given: amount / units
    one = tool(c, owner["access_token"], "fund_trades", account_id=acct, fund_code="scbset50", limit=1)
    assert one["total"] == 2 and [t["side"] for t in one["trades"]] == ["sell"]

    # a bank account, and someone else's fund account: errors that reveal nothing
    for token_, aid in ((owner, 1), (other, acct)):
        res = call(c, token_["access_token"], "tools/call",
                   {"name": "fund_trades", "arguments": {"account_id": aid}}).json()["result"]
        assert res["isError"] and "SCBSET50" not in json.dumps(res), aid


def test_fund_nav_and_value_history_tools(c, db):
    with db.begin() as conn:
        conn.execute(text("TRUNCATE ledger.fund_navs"))
        conn.execute(text("INSERT INTO ledger.fund_navs (code, nav_date, nav) "
                          "SELECT 'K-USA-A(A)', d::date, 10 + extract(doy FROM d) / 100 "
                          "FROM generate_series(date '2025-01-01', date '2026-10-31', interval '1 day') d"))
        acc = conn.execute(text("INSERT INTO ledger.accounts (user_id, name, type) VALUES (1, 'TEST fund MCP', "
                                "'investment') RETURNING id")).scalar()
        fund = conn.execute(text("INSERT INTO ledger.funds (user_id, account_id, code) VALUES (1, :a, 'K-USA-A(A)') "
                                 "RETURNING id"), {"a": acc}).scalar()
        conn.execute(text("INSERT INTO ledger.fund_trades (user_id, fund_id, side, trade_date, units, amount, nav, "
                          "funding) VALUES (1, :f, 'buy', '2026-10-01', 100, 1000, 10, 'held_before')"), {"f": fund})
    kid = auth.add_user("kid2@example.com", "member")["id"]
    _, owner = connect(c, 1)
    _, other = connect(c, kid)
    try:
        h = tool(c, owner["access_token"], "fund_nav_history", fund_code="k-usa-a(a)")
        assert h["interval"] == "week" and len(h["points"]) <= 400 and h["stats"]["from"] == "2025-01-01"
        assert tool(c, owner["access_token"], "fund_nav_history", fund_code="K-USA-A(A)", since="2026-10-25",
                    interval="day")["points"][0]["date"] == "2026-10-25"
        v = tool(c, owner["access_token"], "portfolio_value_history", account_id=acc)
        assert v["interval"] == "day" and v["points"][0]["value"] == 100 * (10 + 274 / 100)    # 2026-10-01 = day 274
        res = call(c, other["access_token"], "tools/call",
                   {"name": "portfolio_value_history", "arguments": {"account_id": acc}}).json()["result"]
        assert res["isError"] and "TEST fund" not in json.dumps(res, ensure_ascii=False)
    finally:
        with db.begin() as conn:
            conn.execute(text("TRUNCATE ledger.fund_trades, ledger.funds, ledger.fund_navs CASCADE"))
            conn.execute(text("DELETE FROM ledger.accounts WHERE name = 'TEST fund MCP'"))
