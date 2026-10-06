import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import google_oauth
from app.auth import SESSION_COOKIE, require_user
from app.main import app

PUBLIC_PATHS = {"/health", "/auth/login", "/auth/callback", "/auth/logout"}
CSRF = {"X-Requested-With": "fetch"}
ADMIN = "me@example.com"


@pytest.fixture
def real_auth(signed_in, monkeypatch):
    app.dependency_overrides.pop(require_user, None)
    monkeypatch.setenv("AUTH_MODE", "google")
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("SECRET_KEY", "x3bG1nUWyZ8mU4H2s1tqB0w2m0aQyGq3oY8H7kqV0eA=")


@pytest.fixture
def api(db, real_auth, monkeypatch):
    monkeypatch.setattr("app.db._engine", db)
    with db.begin() as conn:
        conn.execute(text("TRUNCATE ledger.auth_sessions, ledger.api_tokens RESTART IDENTITY"))
        # user 1 owns the data (kept); other users come from earlier tests
        conn.execute(text("DELETE FROM ledger.users WHERE id > 1"))
        conn.execute(text("UPDATE ledger.users SET email = 'owner@localhost', role = 'admin', is_active = true, "
                          "name = NULL WHERE id = 1"))
    return TestClient(app)


def test_every_route_needs_sign_in_except_the_public_ones(real_auth):
    """Calls every route in the API spec without credentials; a new router mounted outside
    `protected` in app/main.py fails here."""
    c = TestClient(app)
    paths = app.openapi()["paths"]
    assert len(paths) > 20
    for path, ops in paths.items():
        url = re.sub(r"\{[^}]+\}", "1", path)
        for method in ops:
            status = c.request(method, url).status_code
            assert (status != 401) == (path in PUBLIC_PATHS), f"{method.upper()} {path} -> {status}"
    for hidden in ("/docs", "/openapi.json"):
        assert c.get(hidden).status_code == 401


def test_signed_out_gets_401(api):
    for path in ("/transactions", "/auth/me", "/docs", "/openapi.json", "/accounts", "/users"):
        assert api.get(path).status_code == 401, path
    assert api.get("/health").status_code == 200


def _login(client, monkeypatch, email=ADMIN, verified=True, next_path="/#settings", tokens=None):
    """Runs /auth/login then /auth/callback with Google's token endpoint replaced."""
    res = client.get("/auth/login", params={"next": next_path}, follow_redirects=False)
    assert res.status_code == 303
    google = parse_qs(urlsplit(res.headers["location"]).query)

    def fake_exchange(code, redirect_uri, verifier):
        assert code == "the-code" and redirect_uri == "http://testserver/api/auth/callback"
        assert verifier    # came back from the encrypted flow cookie
        claims = {"email": email, "email_verified": verified, "name": "Me", "nonce": google["nonce"][0]}
        return claims, tokens or {}

    monkeypatch.setattr(google_oauth, "exchange_code", fake_exchange)
    return client.get("/auth/callback", params={"code": "the-code", "state": google["state"][0]},
                      follow_redirects=False)


def test_login_redirects_to_google_with_pkce(api):
    res = api.get("/auth/login", follow_redirects=False)
    url = urlsplit(res.headers["location"])
    q = parse_qs(url.query)
    assert url.netloc == "accounts.google.com"
    assert q["client_id"] == ["cid.apps.googleusercontent.com"]
    assert q["redirect_uri"] == ["http://testserver/api/auth/callback"]
    assert q["code_challenge_method"] == ["S256"] and q["state"] and q["nonce"]
    assert "csecret" not in res.headers["location"]
    assert "expense_oauth_flow" in res.cookies


def test_first_admin_claims_the_owner_then_signs_out(api, monkeypatch):
    res = _login(api, monkeypatch)
    assert res.status_code == 303
    assert res.headers["location"] == "http://testserver/#settings"
    me = api.get("/auth/me").json()
    assert me == {"id": 1, "email": ADMIN, "name": "Me", "role": "admin", "via": "session", "auth_mode": "google"}
    assert api.get("/transactions").status_code == 200

    assert api.post("/auth/logout").status_code == 204
    assert api.get("/auth/me").status_code == 401


def test_session_is_stored_hashed(api, db, monkeypatch):
    _login(api, monkeypatch)
    cookie = api.cookies.get(SESSION_COOKIE)
    with db.connect() as conn:
        stored = conn.execute(text("SELECT token_hash FROM ledger.auth_sessions")).scalar()
    assert cookie and stored != cookie and len(stored) == 64


def test_unknown_email_is_refused(api, monkeypatch):
    res = _login(api, monkeypatch, email="stranger@example.com")
    assert res.headers["location"] == "http://testserver/?login_error=not_allowed"
    assert api.get("/auth/me").status_code == 401


def test_unverified_email_is_refused(api, monkeypatch):
    res = _login(api, monkeypatch, verified=False)
    assert res.headers["location"].endswith("login_error=failed")


def test_admin_adds_a_member_who_can_then_sign_in(api, monkeypatch):
    _login(api, monkeypatch)
    res = api.post("/users", json={"email": "Kid@Example.com"}, headers=CSRF)
    assert res.status_code == 201 and res.json()["email"] == "kid@example.com"
    assert res.json()["role"] == "member"
    assert api.post("/users", json={"email": "kid@example.com"}, headers=CSRF).status_code == 409
    assert api.post("/users", json={"email": "not-an-email"}, headers=CSRF).status_code == 422

    kid = TestClient(app)
    _login(kid, monkeypatch, email="kid@example.com")
    assert kid.get("/auth/me").json()["role"] == "member"
    assert kid.get("/users").status_code == 403
    assert kid.post("/users", json={"email": "x@example.com"}, headers=CSRF).status_code == 403


def test_deactivating_a_user_signs_them_out_and_stops_their_tokens(api, monkeypatch):
    _login(api, monkeypatch)
    kid_id = api.post("/users", json={"email": "kid@example.com"}, headers=CSRF).json()["id"]
    kid = TestClient(app)
    _login(kid, monkeypatch, email="kid@example.com")
    token = kid.post("/auth/tokens", json={"name": "t"}, headers=CSRF).json()["token"]

    assert api.patch(f"/users/{kid_id}", json={"is_active": False}, headers=CSRF).json()["is_active"] is False
    assert kid.get("/auth/me").status_code == 401
    assert TestClient(app).get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert _login(kid, monkeypatch, email="kid@example.com").headers["location"].endswith("not_allowed")


def test_admin_cannot_lock_themselves_out(api, monkeypatch):
    _login(api, monkeypatch)
    assert api.patch("/users/1", json={"is_active": False}, headers=CSRF).status_code == 422
    assert api.patch("/users/1", json={"role": "member"}, headers=CSRF).status_code == 422


def test_state_mismatch_is_refused(api, monkeypatch):
    api.get("/auth/login", follow_redirects=False)
    res = api.get("/auth/callback", params={"code": "c", "state": "forged"}, follow_redirects=False)
    assert res.headers["location"].endswith("login_error=state")


def test_callback_without_flow_cookie_is_refused(api):
    res = api.get("/auth/callback", params={"code": "c", "state": "s"}, follow_redirects=False)
    assert res.headers["location"].endswith("login_error=state")


@pytest.mark.parametrize("bad", ["//evil.example/x", "https://evil.example", "\\\\evil.example", "evil", "/\t/evil.example"])
def test_next_cannot_leave_the_site(api, monkeypatch, bad):
    res = _login(api, monkeypatch, next_path=bad)
    assert res.headers["location"] == "http://testserver/"


def test_cookie_write_without_csrf_header_is_refused(api, monkeypatch):
    _login(api, monkeypatch)
    body = {"kind": "expense", "account_id": 1, "amount": "1", "occurred_at": "2026-10-01T00:00:00+07:00",
            "has_time": False, "category_id": 1}
    assert api.post("/transactions", json=body).status_code == 403
    assert api.post("/auth/tokens", json={"name": "x"}).status_code == 403


def test_api_token_lifecycle(api, db, monkeypatch):
    _login(api, monkeypatch)
    res = api.post("/auth/tokens", json={"name": "bruno", "expires_in_days": 30}, headers=CSRF)
    assert res.status_code == 201
    created = res.json()
    token = created["token"]
    assert token.startswith("fat_") and created["token_prefix"] == token[:8]

    listed = api.get("/auth/tokens").json()
    assert [t["name"] for t in listed] == ["bruno"] and "token" not in listed[0]
    with db.connect() as conn:
        assert conn.execute(text("SELECT token_hash FROM ledger.api_tokens")).scalar() != token

    bare = TestClient(app)    # no cookies
    hdr = {"Authorization": f"Bearer {token}"}
    assert bare.get("/transactions", headers=hdr).status_code == 200
    me = bare.get("/auth/me", headers=hdr).json()
    assert me["via"] == "token" and me["id"] == 1
    # tokens are for scripts: writes need no CSRF header, but they cannot mint tokens or manage users
    assert bare.post("/categorize", headers=hdr).status_code != 403
    assert bare.post("/auth/tokens", json={"name": "x"}, headers=hdr).status_code == 403
    assert bare.get("/users", headers=hdr).status_code == 403
    assert bare.get("/transactions", headers={"Authorization": "Bearer fat_wrong"}).status_code == 401

    assert api.delete(f"/auth/tokens/{created['id']}", headers=CSRF).status_code == 204
    assert bare.get("/transactions", headers=hdr).status_code == 401
    assert api.delete("/auth/tokens/999", headers=CSRF).status_code == 404


def test_users_see_and_revoke_only_their_own_tokens(api, monkeypatch):
    _login(api, monkeypatch)
    api.post("/users", json={"email": "kid@example.com"}, headers=CSRF)
    mine = api.post("/auth/tokens", json={"name": "mine"}, headers=CSRF).json()
    kid = TestClient(app)
    _login(kid, monkeypatch, email="kid@example.com")
    assert kid.get("/auth/tokens").json() == []
    assert kid.delete(f"/auth/tokens/{mine['id']}", headers=CSRF).status_code == 404


def test_expired_token_is_refused(api, db, monkeypatch):
    _login(api, monkeypatch)
    token = api.post("/auth/tokens", json={"name": "old"}, headers=CSRF).json()["token"]
    with db.begin() as conn:
        conn.execute(text("UPDATE ledger.api_tokens SET expires_at = now() - interval '1 second'"))
    assert TestClient(app).get("/transactions", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_docs_behind_sign_in_use_the_proxy_prefix(api, monkeypatch):
    _login(api, monkeypatch)
    html = api.get("/docs", headers={"X-Forwarded-Prefix": "/api"}).text
    assert "/api/openapi.json" in html
    spec = api.get("/openapi.json", headers={"X-Forwarded-Prefix": "/api"}).json()
    assert spec["servers"] == [{"url": "/api"}]
    assert api.get("/openapi.json", headers={"X-Forwarded-Prefix": "//evil"}).json()["servers"] == [{"url": "/"}]


def test_auth_off_acts_as_user_1_on_localhost_only(api, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "off")
    me = TestClient(app).get("/auth/me").json()
    assert me["via"] == "off" and me["id"] == 1
    # a public hostname (one the operator added to ALLOWED_HOSTS) is refused even so
    public = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                      "headers": [(b"host", b"money.example.com")]})
    with pytest.raises(HTTPException) as e:
        require_user(public, None)
    assert e.value.status_code == 403


def test_unconfigured_google_client_fails_closed(api, monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID")
    res = api.get("/auth/login", follow_redirects=False)
    assert res.headers["location"].endswith("login_error=config")


# ── one consent for sign-in and Gmail ─────────────────────────────────────

GMAIL = "https://www.googleapis.com/auth/gmail.readonly"
GRANTED = {"refresh_token": "1//from-sign-in", "scope": f"openid email profile {GMAIL}"}


@pytest.fixture
def revoked(monkeypatch):
    out = []
    monkeypatch.setattr(google_oauth, "revoke", out.append)
    return out


def _gmail(db, uid=1):
    with db.connect() as conn:
        return conn.execute(text("SELECT gmail_address, last_error FROM ledger.gmail_connections WHERE user_id = :u"),
                            {"u": uid}).first()


def test_sign_in_also_asks_for_readonly_gmail_offline(api):
    q = parse_qs(urlsplit(api.get("/auth/login", follow_redirects=False).headers["location"]).query)
    assert q["scope"] == [f"openid email profile {GMAIL}"]
    assert q["access_type"] == ["offline"] and q["include_granted_scopes"] == ["true"]


def test_first_sign_in_connects_gmail_later_ones_keep_it(api, db, monkeypatch, revoked):
    with db.begin() as conn:
        conn.execute(text("DELETE FROM ledger.gmail_connections"))
    _login(api, monkeypatch, tokens=GRANTED)
    assert _gmail(db) == (ADMIN, None)
    assert api.get("/gmail/status").json()["connected"] is True
    # Google does not show the consent screen again, so no refresh token: the connection stays
    _login(api, monkeypatch, tokens={"scope": f"openid email profile {GMAIL}"})
    assert _gmail(db) == (ADMIN, None)
    assert revoked == []


def test_unticking_gmail_still_signs_in(api, db, monkeypatch):
    with db.begin() as conn:
        conn.execute(text("DELETE FROM ledger.gmail_connections"))
    _login(api, monkeypatch, tokens={"refresh_token": "1//x", "scope": "openid email profile"})
    assert api.get("/auth/me").status_code == 200
    assert _gmail(db) is None


def test_refused_sign_in_gives_the_gmail_grant_back(api, db, monkeypatch, revoked):
    with db.begin() as conn:
        conn.execute(text("DELETE FROM ledger.gmail_connections"))
    res = _login(api, monkeypatch, email="stranger@example.com", tokens=GRANTED)
    assert res.headers["location"].endswith("login_error=not_allowed")
    assert revoked == ["1//from-sign-in"]
    with db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM ledger.gmail_connections")).scalar() == 0


def test_sign_in_does_not_replace_a_working_connection_to_another_mailbox(api, db, monkeypatch, revoked):
    from app import gmail_connections
    with db.begin() as conn:
        conn.execute(text("DELETE FROM ledger.gmail_connections"))
    gmail_connections.save(1, "bank-mail@gmail.com", "1//other", GMAIL)
    _login(api, monkeypatch, tokens=GRANTED)
    assert _gmail(db) == ("bank-mail@gmail.com", None)
    # ...but an expired one is replaced
    with db.begin() as conn:
        conn.execute(text("UPDATE ledger.gmail_connections SET last_error = 'expired'"))
    _login(api, monkeypatch, tokens=GRANTED)
    assert _gmail(db) == (ADMIN, None)
