"""OAuth for the MCP connector (claude.ai): the storage and policy behind the MCP SDK's authorization server.

The SDK (app/mcp_server.py) serves /register, /authorize, /token, /revoke and the metadata, and checks PKCE,
code expiry and redirect_uri; this module decides who may register, sends /authorize to our consent page
(the person signs in with Google like on the web UI), and keeps codes and tokens in Postgres.

- Clients register themselves (RFC 7591), but only with redirect URIs on claude.ai / claude.com, or on
  localhost (Claude Code, the MCP Inspector).
- Tokens are prefixed mcp_ and only sha256 is stored. require_user (app/auth.py) never accepts them, so a
  connector can read through the MCP tools and cannot call the REST API, which can write.
- AccessToken.subject is the user id as a string; the MCP tools filter every query by it.
"""
import hashlib
import json
import secrets as pysecrets
from typing import Optional
from urllib.parse import urlencode, urlsplit

import anyio
from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams, RefreshToken,
                                      RegistrationError, TokenError)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import text

from app import auth, secrets
from app.db import get_engine

SCOPE = "read"
CODE_TTL = 300                  # seconds
ACCESS_TTL = 3600
REFRESH_TTL = 60 * 24 * 3600
CONSENT_TTL = 600               # the signed consent request; long enough to sign in with Google first
MAX_CLIENTS = 200               # registration is open to anyone: cap it
CLAUDE_CALLBACK_HOSTS = {"claude.ai", "claude.com"}
LOCAL_CALLBACK_HOSTS = {"localhost", "127.0.0.1"}


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def allowed_redirect_uri(uri: str) -> bool:
    u = urlsplit(uri)
    if u.scheme == "https" and u.hostname in CLAUDE_CALLBACK_HOSTS:
        return True
    return u.scheme == "http" and u.hostname in LOCAL_CALLBACK_HOSTS


# ── consent request: what /authorize asked for, signed so the consent page can trust it ─────────────

def consent_request(client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
    return secrets.encrypt(json.dumps({
        "client_id": client.client_id, "client_name": client.client_name,
        "redirect_uri": str(params.redirect_uri), "explicit": params.redirect_uri_provided_explicitly,
        "code_challenge": params.code_challenge, "state": params.state, "scopes": params.scopes or [SCOPE],
        "resource": params.resource}))


def read_consent_request(req: str) -> Optional[dict]:
    try:
        return json.loads(secrets.decrypt(req, ttl=CONSENT_TTL))
    except (RuntimeError, ValueError):
        return None


def csrf_token(req: str, user_id: int) -> str:
    """Hidden field of the consent form: only a page we rendered for this person and request has it."""
    return secrets.encrypt(json.dumps({"req": _hash(req), "uid": user_id}))


def check_csrf(token: str, req: str, user_id: int) -> bool:
    try:
        data = json.loads(secrets.decrypt(token, ttl=CONSENT_TTL))
    except (RuntimeError, ValueError):
        return False
    return data.get("req") == _hash(req) and data.get("uid") == user_id


def redirect_with(redirect_uri: str, **params: Optional[str]) -> str:
    query = urlencode({k: v for k, v in params.items() if v is not None})
    return redirect_uri + ("&" if urlsplit(redirect_uri).query else "?") + query


def issue_code(request: dict, user_id: int) -> str:
    code = "mcp_c_" + pysecrets.token_urlsafe(32)
    with get_engine().begin() as conn:
        conn.execute(text("INSERT INTO ledger.oauth_codes (code_hash, client_id, user_id, data, expires_at) "
                          "VALUES (:h, :c, :u, CAST(:d AS jsonb), now() + make_interval(secs => :ttl))"),
                     {"h": _hash(code), "c": request["client_id"], "u": user_id, "ttl": CODE_TTL,
                      "d": json.dumps({k: request[k] for k in
                                       ("redirect_uri", "explicit", "code_challenge", "scopes", "resource")})})
    return code


# ── connections, for Settings ─────────────────────────────────────────────────────────────────────

def list_connections(user_id: int) -> list[dict]:
    """One row per approval that still has a live token."""
    with get_engine().connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text("""
            SELECT t.grant_id, t.client_id, c.info->>'client_name' AS client_name, min(t.created_at) AS connected_at,
                   max(t.last_used_at) AS last_used_at
            FROM ledger.oauth_tokens t JOIN ledger.oauth_clients c ON c.client_id = t.client_id
            WHERE t.user_id = :u AND t.revoked_at IS NULL AND t.expires_at > now()
            GROUP BY t.grant_id, t.client_id, c.info->>'client_name' ORDER BY connected_at DESC"""), {"u": user_id})]


def revoke_connection(user_id: int, grant_id: str) -> bool:
    with get_engine().begin() as conn:
        return conn.execute(text("UPDATE ledger.oauth_tokens SET revoked_at = now() "
                                 "WHERE user_id = :u AND grant_id = :g AND revoked_at IS NULL"),
                            {"u": user_id, "g": grant_id}).rowcount > 0


# ── the SDK provider ──────────────────────────────────────────────────────────────────────────────

def _issue_pair(conn, client_id: str, user_id: int, scopes: list[str], resource: Optional[str],
                grant_id: str) -> OAuthToken:
    access, refresh = "mcp_at_" + pysecrets.token_urlsafe(32), "mcp_rt_" + pysecrets.token_urlsafe(32)
    for secret, kind, ttl in ((access, "access", ACCESS_TTL), (refresh, "refresh", REFRESH_TTL)):
        conn.execute(text(
            "INSERT INTO ledger.oauth_tokens (token_hash, kind, grant_id, client_id, user_id, scopes, resource, "
            "expires_at) VALUES (:h, :k, :g, :c, :u, :s, :r, now() + make_interval(secs => :ttl))"),
            {"h": _hash(secret), "k": kind, "g": grant_id, "c": client_id, "u": user_id, "s": scopes,
             "r": resource, "ttl": ttl})
    return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL, scope=" ".join(scopes),
                      refresh_token=refresh)


def _token_row(conn, secret: str, kind: str):
    """A live token of an active user, or None."""
    return conn.execute(text("""
        SELECT t.id, t.client_id, t.user_id, t.scopes, t.resource, t.grant_id,
               extract(epoch FROM t.expires_at)::bigint AS expires_at
        FROM ledger.oauth_tokens t JOIN ledger.users u ON u.id = t.user_id
        WHERE t.token_hash = :h AND t.kind = :k AND t.revoked_at IS NULL AND t.expires_at > now() AND u.is_active"""),
        {"h": _hash(secret), "k": kind}).first()


class Provider:
    """OAuthAuthorizationServerProvider for the MCP SDK. DB calls are short; they run in a worker thread so
    the event loop never waits on Postgres."""

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        def run():
            with get_engine().connect() as conn:
                info = conn.execute(text("SELECT info FROM ledger.oauth_clients WHERE client_id = :c"),
                                    {"c": client_id}).scalar()
            return OAuthClientInformationFull.model_validate(info) if info else None
        return await anyio.to_thread.run_sync(run)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        bad = [str(u) for u in client_info.redirect_uris or [] if not allowed_redirect_uri(str(u))]
        if not client_info.redirect_uris or bad:
            raise RegistrationError("invalid_redirect_uri",
                                    "only Claude's callback (claude.ai / claude.com) or localhost is allowed")
        if client_info.token_endpoint_auth_method not in (None, "none"):
            raise RegistrationError("invalid_client_metadata", "only public clients (PKCE, no secret)")

        def run():
            with get_engine().begin() as conn:
                if conn.execute(text("SELECT count(*) FROM ledger.oauth_clients")).scalar() >= MAX_CLIENTS:
                    raise RegistrationError("invalid_client_metadata", "too many registered clients")
                conn.execute(text("INSERT INTO ledger.oauth_clients (client_id, info) VALUES (:c, CAST(:i AS jsonb))"),
                             {"c": client_info.client_id, "i": client_info.model_dump_json()})
        await anyio.to_thread.run_sync(run)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        # the person approves on our page (Google sign-in first if needed); it then redirects with the code
        return auth.public_base_url() + "/api/oauth/consent?" + urlencode({"req": consent_request(client, params)})

    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> Optional[AuthorizationCode]:
        def run():
            with get_engine().connect() as conn:
                return conn.execute(text(
                    "SELECT client_id, user_id, data, extract(epoch FROM expires_at) AS expires_at "
                    "FROM ledger.oauth_codes WHERE code_hash = :h AND client_id = :c"),
                    {"h": _hash(authorization_code), "c": client.client_id}).first()
        r = await anyio.to_thread.run_sync(run)
        if r is None:
            return None
        return AuthorizationCode(code=authorization_code, scopes=r.data["scopes"], expires_at=float(r.expires_at),
                                 client_id=r.client_id, code_challenge=r.data["code_challenge"],
                                 redirect_uri=r.data["redirect_uri"],
                                 redirect_uri_provided_explicitly=r.data["explicit"], resource=r.data["resource"],
                                 subject=str(r.user_id))

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        def run():
            with get_engine().begin() as conn:
                # one-time: the row goes now, so a second exchange finds nothing
                gone = conn.execute(text("DELETE FROM ledger.oauth_codes WHERE code_hash = :h RETURNING user_id"),
                                    {"h": _hash(authorization_code.code)}).scalar()
                if gone is None:
                    raise TokenError("invalid_grant", "authorization code already used")
                return _issue_pair(conn, client.client_id, gone, authorization_code.scopes,
                                   authorization_code.resource, pysecrets.token_urlsafe(16))
        return await anyio.to_thread.run_sync(run)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> Optional[RefreshToken]:
        def run():
            with get_engine().connect() as conn:
                return _token_row(conn, refresh_token, "refresh")
        r = await anyio.to_thread.run_sync(run)
        if r is None or r.client_id != client.client_id:
            return None
        return RefreshToken(token=refresh_token, client_id=r.client_id, scopes=list(r.scopes),
                            expires_at=int(r.expires_at), resource=r.resource, subject=str(r.user_id))

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        def run():
            with get_engine().begin() as conn:
                # rotate: the old refresh token and its access tokens stop working, the approval (grant) goes on
                grant = conn.execute(text(
                    "UPDATE ledger.oauth_tokens SET revoked_at = now() WHERE token_hash = :h AND revoked_at IS NULL "
                    "RETURNING grant_id"), {"h": _hash(refresh_token.token)}).scalar()
                if grant is None:
                    raise TokenError("invalid_grant", "refresh token already used")
                conn.execute(text("UPDATE ledger.oauth_tokens SET revoked_at = now() "
                                  "WHERE grant_id = :g AND kind = 'access' AND revoked_at IS NULL"), {"g": grant})
                return _issue_pair(conn, client.client_id, int(refresh_token.subject), scopes,
                                   refresh_token.resource, grant)
        return await anyio.to_thread.run_sync(run)

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        def run():
            with get_engine().begin() as conn:
                r = _token_row(conn, token, "access")
                if r is not None:
                    conn.execute(text(f"UPDATE ledger.oauth_tokens SET last_used_at = now() WHERE id = :id AND "
                                      f"(last_used_at IS NULL OR last_used_at < now() - interval '{auth.TOUCH_INTERVAL}')"),
                                 {"id": r.id})
                return r
        r = await anyio.to_thread.run_sync(run)
        if r is None:
            return None
        return AccessToken(token=token, client_id=r.client_id, scopes=list(r.scopes), expires_at=int(r.expires_at),
                           resource=r.resource, subject=str(r.user_id))

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        def run():
            with get_engine().begin() as conn:
                conn.execute(text("""
                    UPDATE ledger.oauth_tokens SET revoked_at = now()
                    WHERE revoked_at IS NULL AND grant_id = (SELECT grant_id FROM ledger.oauth_tokens WHERE token_hash = :h)"""),
                    {"h": _hash(token.token)})
        await anyio.to_thread.run_sync(run)
