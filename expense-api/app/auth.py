"""Who is calling: a browser session from Google sign-in, or an API token. Both resolve to a row in
ledger.users; only active users get in.

- Users: an admin adds family members by email in Settings (role member). Addresses in env
  ADMIN_EMAILS are always admins and are created on first sign-in.
- Sessions: cookie SESSION_COOKIE holds a random secret; the DB keeps only its sha256.
- API tokens: "Authorization: Bearer fat_..."; created in Settings, shown once, DB keeps only sha256.
  A token acts as the user who created it.
- AUTH_MODE=off skips sign-in for local development (acts as user 1), and then only answers
  Host localhost/127.0.0.1.

Every router is mounted with Depends(require_user) in app/main.py; tests/test_auth.py checks that
no route is left open except the sign-in ones and /health.
"""
import hashlib
import os
import secrets as pysecrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text

from app.db import get_engine

SESSION_COOKIE = "expense_session"
TOKEN_PREFIX = "fat_"
PLACEHOLDER_OWNER = "owner@localhost"     # see alembic 0011
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "expense-api", "testserver"}
# don't write last_seen/last_used on every request
TOUCH_INTERVAL = "5 minutes"

bearer = HTTPBearer(auto_error=False, description="API token from Settings (fat_...)")


@dataclass(frozen=True)
class Principal:
    user_id: int
    email: str
    role: str                      # admin | member
    via: str                       # session | token | off
    token_id: Optional[int] = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# ── config ────────────────────────────────────────────────────────────────

def auth_mode() -> str:
    mode = os.environ.get("AUTH_MODE", "google").strip().lower()
    if mode not in ("google", "off"):
        raise RuntimeError("AUTH_MODE must be google or off")
    return mode


def admin_emails() -> list[str]:
    return [e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]


def public_base_url() -> str:
    """Where the browser reaches the web UI, e.g. https://money.example.com (no trailing slash)."""
    return os.environ.get("PUBLIC_BASE_URL", "http://localhost:3000").rstrip("/")


def cookie_secure() -> bool:
    return urlsplit(public_base_url()).scheme == "https"


def session_days() -> int:
    return int(os.environ.get("SESSION_DAYS", "30"))


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── users ─────────────────────────────────────────────────────────────────

USER_COLUMNS = "id, email, name, role, is_active, created_at, last_login_at"


def user_for_sign_in(email: str, name: Optional[str]) -> Optional[dict]:
    """The active user for a verified Google address, or None if they may not sign in.
    ADMIN_EMAILS addresses are created (or promoted) as admins; the first one to sign in also takes
    over the placeholder owner of the existing data."""
    email = email.lower()
    with get_engine().begin() as conn:
        if email in admin_emails():
            exists = conn.execute(text("SELECT 1 FROM ledger.users WHERE email = :e"), {"e": email}).first()
            if not exists:
                conn.execute(text("UPDATE ledger.users SET email = :e WHERE email = :p"),
                             {"e": email, "p": PLACEHOLDER_OWNER})
            conn.execute(text(
                "INSERT INTO ledger.users (email, role) VALUES (:e, 'admin') "
                "ON CONFLICT (email) DO UPDATE SET role = 'admin', is_active = true"), {"e": email})
        row = conn.execute(text(
            f"UPDATE ledger.users SET last_login_at = now(), name = COALESCE(:name, name) "
            f"WHERE email = :e AND is_active RETURNING {USER_COLUMNS}"), {"e": email, "name": name}).first()
    return dict(row._mapping) if row else None


def list_users() -> list[dict]:
    with get_engine().connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(f"SELECT {USER_COLUMNS} FROM ledger.users ORDER BY id"))]


def add_user(email: str, role: str) -> Optional[dict]:
    """None if the address already has a user."""
    with get_engine().begin() as conn:
        row = conn.execute(text(
            f"INSERT INTO ledger.users (email, role) VALUES (:e, :role) ON CONFLICT (email) DO NOTHING "
            f"RETURNING {USER_COLUMNS}"), {"e": email.strip().lower(), "role": role}).first()
    return dict(row._mapping) if row else None


def update_user(user_id: int, role: Optional[str], is_active: Optional[bool]) -> Optional[dict]:
    with get_engine().begin() as conn:
        row = conn.execute(text(
            f"UPDATE ledger.users SET role = COALESCE(:role, role), is_active = COALESCE(:active, is_active) "
            f"WHERE id = :id RETURNING {USER_COLUMNS}"), {"id": user_id, "role": role, "active": is_active}).first()
        if row is not None and not row.is_active:
            # signed out everywhere at once; tokens stay but stop working while inactive
            conn.execute(text("DELETE FROM ledger.auth_sessions WHERE user_id = :id"), {"id": user_id})
    return dict(row._mapping) if row else None


def _principal(row, via: str, token_id: Optional[int] = None) -> Principal:
    return Principal(user_id=row.user_id, email=row.email, role=row.role, via=via, token_id=token_id)


# ── sessions ──────────────────────────────────────────────────────────────

def create_session(user_id: int, user_agent: Optional[str]) -> tuple[str, datetime]:
    secret = pysecrets.token_urlsafe(32)
    expires = _now() + timedelta(days=session_days())
    with get_engine().begin() as conn:
        conn.execute(text(
            "INSERT INTO ledger.auth_sessions (user_id, token_hash, user_agent, expires_at) "
            "VALUES (:uid, :h, :ua, :exp)"),
            {"uid": user_id, "h": _hash(secret), "ua": (user_agent or "")[:200] or None, "exp": expires})
        # housekeeping: expired sessions are useless
        conn.execute(text("DELETE FROM ledger.auth_sessions WHERE expires_at < now()"))
    return secret, expires


def session_principal(secret: str) -> Optional[Principal]:
    with get_engine().begin() as conn:
        row = conn.execute(text(
            "SELECT s.id, s.user_id, u.email, u.role FROM ledger.auth_sessions s "
            "JOIN ledger.users u ON u.id = s.user_id "
            "WHERE s.token_hash = :h AND s.expires_at > now() AND u.is_active"), {"h": _hash(secret)}).first()
        if row is None:
            return None
        conn.execute(text(
            f"UPDATE ledger.auth_sessions SET last_seen_at = now() "
            f"WHERE id = :id AND last_seen_at < now() - interval '{TOUCH_INTERVAL}'"), {"id": row.id})
    return _principal(row, "session")


def delete_session(secret: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM ledger.auth_sessions WHERE token_hash = :h"), {"h": _hash(secret)})


# ── API tokens ────────────────────────────────────────────────────────────

TOKEN_COLUMNS = "id, name, token_prefix, created_at, expires_at, last_used_at, revoked_at"


def create_token(user_id: int, name: str, expires_in_days: Optional[int]) -> dict:
    secret = TOKEN_PREFIX + pysecrets.token_urlsafe(32)
    expires = _now() + timedelta(days=expires_in_days) if expires_in_days else None
    with get_engine().begin() as conn:
        row = conn.execute(text(
            "INSERT INTO ledger.api_tokens (user_id, name, token_prefix, token_hash, expires_at) "
            f"VALUES (:uid, :name, :prefix, :h, :exp) RETURNING {TOKEN_COLUMNS}"),
            {"uid": user_id, "name": name, "prefix": secret[:len(TOKEN_PREFIX) + 4], "h": _hash(secret),
             "exp": expires}).first()
    return {**row._mapping, "token": secret}


def list_tokens(user_id: int) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(text(f"SELECT {TOKEN_COLUMNS} FROM ledger.api_tokens WHERE user_id = :uid "
                                 f"ORDER BY id DESC"), {"uid": user_id})
        return [dict(r._mapping) for r in rows]


def revoke_token(user_id: int, token_id: int) -> bool:
    with get_engine().begin() as conn:
        return conn.execute(text(
            "UPDATE ledger.api_tokens SET revoked_at = COALESCE(revoked_at, now()) "
            "WHERE id = :id AND user_id = :uid"), {"id": token_id, "uid": user_id}).rowcount > 0


def token_principal(secret: str) -> Optional[Principal]:
    if not secret.startswith(TOKEN_PREFIX):
        return None
    with get_engine().begin() as conn:
        row = conn.execute(text(
            "SELECT t.id, t.user_id, u.email, u.role FROM ledger.api_tokens t "
            "JOIN ledger.users u ON u.id = t.user_id "
            "WHERE t.token_hash = :h AND t.revoked_at IS NULL AND (t.expires_at IS NULL OR t.expires_at > now()) "
            "AND u.is_active"), {"h": _hash(secret)}).first()
        if row is None:
            return None
        conn.execute(text(
            f"UPDATE ledger.api_tokens SET last_used_at = now() WHERE id = :id "
            f"AND (last_used_at IS NULL OR last_used_at < now() - interval '{TOUCH_INTERVAL}')"),
            {"id": row.id})
    return _principal(row, "token", token_id=row.id)


def local_principal() -> Principal:
    with get_engine().connect() as conn:
        row = conn.execute(text("SELECT id AS user_id, email, role FROM ledger.users ORDER BY id LIMIT 1")).first()
    if row is None:
        raise HTTPException(status_code=500, detail="No users; run alembic upgrade head")
    return _principal(row, "off")


# ── dependencies ──────────────────────────────────────────────────────────

def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="Sign in required", headers={"WWW-Authenticate": "Bearer"})


def require_user(request: Request,
                 credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> Principal:
    if auth_mode() == "off":
        # never let a forgotten AUTH_MODE=off serve a public hostname
        if (request.url.hostname or "") not in LOCAL_HOSTS:
            raise HTTPException(status_code=403, detail="AUTH_MODE=off only serves localhost")
        return local_principal()

    if credentials is not None:
        principal = token_principal(credentials.credentials)
        if principal is None:
            raise _unauthorized()
        return principal

    cookie = request.cookies.get(SESSION_COOKIE)
    principal = session_principal(cookie) if cookie else None
    if principal is None:
        raise _unauthorized()
    # CSRF: a cross-site form can send the cookie but cannot add a custom header (and a cross-site
    # fetch with one needs a CORS preflight that this API never grants)
    if request.method not in SAFE_METHODS and not request.headers.get("x-requested-with"):
        raise HTTPException(status_code=403, detail="Missing X-Requested-With header")
    return principal


def require_session(principal: Principal = Depends(require_user)) -> Principal:
    """Managing API tokens and users needs a signed-in person; a token cannot mint more tokens."""
    if principal.via == "token":
        raise HTTPException(status_code=403, detail="API tokens cannot do this")
    return principal


def require_admin(principal: Principal = Depends(require_session)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Admins only")
    return principal
