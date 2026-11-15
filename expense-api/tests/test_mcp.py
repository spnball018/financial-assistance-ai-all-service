"""The MCP connector for Claude: OAuth sign-in (register -> authorize -> consent -> token -> refresh), the tokens
open only /mcp, every tool reads only the signed-in person's rows, and no tool writes."""
import base64
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import auth
from app.auth import require_user
from app.main import app

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
