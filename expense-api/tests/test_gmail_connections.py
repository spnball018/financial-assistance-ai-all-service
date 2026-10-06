"""P7.3: each user connects their own Gmail; the refresh token is stored encrypted and never returned."""
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import auth, gmail_connections, google_oauth, secrets
from app.auth import Principal, require_user
from app.main import app

REFRESH = "1//refresh-token-that-must-stay-secret"
CSRF = {"X-Requested-With": "fetch"}


@pytest.fixture
def env(db, monkeypatch):
    monkeypatch.setattr("app.db._engine", db)
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    with db.begin() as conn:
        conn.execute(text("DELETE FROM ledger.gmail_connections"))
    kid = auth.add_user("kid@example.com", "member")["id"]
    clients = {1: TestClient(app), kid: TestClient(app)}

    def as_user(uid):
        def call(method, url, **kw):
            app.dependency_overrides[require_user] = lambda: Principal(uid, f"u{uid}", "member", "session")
            return getattr(clients[uid], method)(url, follow_redirects=False, **kw)
        return call

    revoked = []
    monkeypatch.setattr(google_oauth, "revoke", revoked.append)
    return as_user(1), as_user(kid), kid, revoked


def _connect(call, monkeypatch, *, scope=gmail_connections.GMAIL_SCOPE, refresh=REFRESH, address="kid@gmail.com"):
    res = call("get", "/gmail/connect")
    assert res.status_code == 303
    state = parse_qs(urlsplit(res.headers["location"]).query)["state"][0]
    monkeypatch.setattr(google_oauth, "exchange_code_for_tokens",
                        lambda code, uri, verifier: {"access_token": "ya29.first", "refresh_token": refresh, "scope": scope})
    monkeypatch.setattr(gmail_connections, "gmail_address", lambda token: address)
    return call("get", "/gmail/callback", params={"code": "c", "state": state})


def test_connect_asks_google_for_readonly_offline_access(env):
    _, kid, _, _ = env
    res = kid("get", "/gmail/connect")
    q = parse_qs(urlsplit(res.headers["location"]).query)
    assert q["scope"] == [gmail_connections.GMAIL_SCOPE]
    assert q["access_type"] == ["offline"] and "consent" in q["prompt"][0]
    assert q["redirect_uri"] == ["http://testserver/api/gmail/callback"]
    assert q["code_challenge_method"] == ["S256"]
    assert "csecret" not in res.headers["location"]


def test_callback_stores_the_token_encrypted_and_never_returns_it(env, db, monkeypatch):
    _, kid, kid_id, _ = env
    res = _connect(kid, monkeypatch)
    assert res.headers["location"] == "http://testserver/?gmail=connected#settings"

    status = kid("get", "/gmail/status")
    assert status.json()["connected"] is True and status.json()["gmail_address"] == "kid@gmail.com"
    assert REFRESH not in status.text
    with db.connect() as conn:
        stored = conn.execute(text("SELECT refresh_token_encrypted FROM ledger.gmail_connections WHERE user_id = :u"),
                              {"u": kid_id}).scalar()
    assert REFRESH not in stored and secrets.decrypt(stored) == REFRESH


def test_connections_are_per_user(env, monkeypatch):
    owner, kid, _, _ = env
    _connect(kid, monkeypatch)
    assert owner("get", "/gmail/status").json() == {"connected": False, "uses_token_json": True}
    assert owner("delete", "/gmail/connection", headers=CSRF).status_code == 404
    assert kid("get", "/gmail/status").json()["connected"] is True


def test_callback_finished_by_another_user_is_refused(env, monkeypatch):
    owner, kid, _, _ = env
    start = owner("get", "/gmail/connect")
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    cookie = start.cookies["expense_gmail_flow"]
    monkeypatch.setattr(google_oauth, "exchange_code_for_tokens", lambda *a: pytest.fail("must not exchange"))
    res = kid("get", "/gmail/callback", params={"code": "c", "state": state}, cookies={"expense_gmail_flow": cookie})
    assert res.headers["location"].startswith("http://testserver/?gmail=state")


@pytest.mark.parametrize("kw,result", [({"scope": "openid"}, "scope"), ({"refresh": None}, "failed")])
def test_missing_scope_or_refresh_token_is_refused(env, monkeypatch, kw, result):
    _, kid, _, _ = env
    assert _connect(kid, monkeypatch, **kw).headers["location"].startswith(f"http://testserver/?gmail={result}")
    assert kid("get", "/gmail/status").json()["connected"] is False


def test_denied_on_google(env):
    _, kid, _, _ = env
    kid("get", "/gmail/connect")
    assert kid("get", "/gmail/callback", params={"error": "access_denied"}).headers["location"].startswith(
        "http://testserver/?gmail=denied")


def test_mail_client_uses_the_users_own_access_token(env, monkeypatch):
    _, kid, kid_id, _ = env
    with pytest.raises(gmail_connections.NotConnected):
        gmail_connections.mail_client_for(kid_id)
    assert "authorization" not in gmail_connections.mail_client_for(1)._http.headers   # token.json

    _connect(kid, monkeypatch)
    seen = []
    monkeypatch.setattr(google_oauth, "refresh_access_token", lambda t: seen.append(t) or "ya29.fresh")
    client = gmail_connections.mail_client_for(kid_id)
    assert seen == [REFRESH]
    assert client._http.headers["authorization"] == "Bearer ya29.fresh"
    assert kid("get", "/gmail/status").json()["last_used_at"] is not None


def test_expired_access_asks_to_reconnect(env, monkeypatch):
    _, kid, kid_id, _ = env
    _connect(kid, monkeypatch)

    def expired(token):
        raise google_oauth.InvalidGrant("refresh token revoked or expired")
    monkeypatch.setattr(google_oauth, "refresh_access_token", expired)
    res = kid("post", "/sync", headers=CSRF)
    assert res.status_code == 409 and "connect Gmail again" in res.json()["detail"]
    assert kid("get", "/gmail/status").json()["last_error"] == "expired"


def test_member_without_gmail_cannot_sync(env):
    _, kid, _, _ = env
    res = kid("post", "/sync", headers=CSRF)
    assert res.status_code == 409 and "Settings" in res.json()["detail"]


def test_disconnect_revokes_and_forgets(env, monkeypatch):
    _, kid, _, revoked = env
    _connect(kid, monkeypatch)
    assert kid("delete", "/gmail/connection", headers=CSRF).status_code == 204
    assert revoked == [REFRESH]
    assert kid("get", "/gmail/status").json()["connected"] is False
    assert kid("delete", "/gmail/connection", headers=CSRF).status_code == 404
