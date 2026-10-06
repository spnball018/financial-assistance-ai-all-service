"""Each user's own Gmail (P7.3): the refresh token is stored Fernet-encrypted (app.secrets, SECRET_KEY)
and never leaves this module. A sync exchanges it for a one-hour access token and hands that to
gmail-service in the Authorization header.

User 1 without a connection keeps using gmail-service's token.json (the mailbox it was set up with).
"""
import os
from typing import Optional

import httpx
from sqlalchemy import text

from app import google_oauth, secrets
from app.db import get_engine
from sync.gmail_client import GmailServiceClient, MailClient

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"   # the sync only reads
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
TOKEN_JSON_OWNER = 1


class NotConnected(Exception):
    """The message is shown to the user."""


def _service_url() -> str:
    return os.environ.get("GMAIL_SERVICE_URL", "http://gmail-service:8000")


def gmail_address(access_token: str) -> str:
    """The mailbox a fresh access token belongs to (may differ from the sign-in address)."""
    try:
        r = httpx.get(PROFILE_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    except httpx.HTTPError as e:
        raise google_oauth.OAuthError(f"Gmail profile unreachable: {type(e).__name__}") from None
    if r.status_code != 200:
        raise google_oauth.OAuthError(f"Gmail profile returned {r.status_code}")
    return r.json()["emailAddress"].lower()


def save(uid: int, address: str, refresh_token: str, scopes: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(text(
            "INSERT INTO ledger.gmail_connections (user_id, gmail_address, refresh_token_encrypted, scopes) "
            "VALUES (:u, :a, :t, :s) ON CONFLICT (user_id) DO UPDATE SET gmail_address = EXCLUDED.gmail_address, "
            "refresh_token_encrypted = EXCLUDED.refresh_token_encrypted, scopes = EXCLUDED.scopes, "
            "connected_at = now(), last_error = NULL"),
            {"u": uid, "a": address, "t": secrets.encrypt(refresh_token), "s": scopes})


def save_from_sign_in(uid: int, address: str, tokens: dict) -> bool:
    """Sign-in also asks for Gmail (one consent for both). Google returns a refresh token only when the
    consent screen was shown; keep an existing working connection to another mailbox."""
    if not tokens.get("refresh_token") or GMAIL_SCOPE not in tokens.get("scope", "").split():
        return False
    current = status(uid)
    if current["connected"] and not current.get("last_error") and current["gmail_address"] != address:
        return False
    save(uid, address, tokens["refresh_token"], tokens["scope"])
    return True


def status(uid: int) -> dict:
    """Safe to return over HTTP: never includes the token."""
    with get_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT gmail_address, scopes, connected_at, last_used_at, last_error "
            "FROM ledger.gmail_connections WHERE user_id = :u"), {"u": uid}).first()
    if row is None:
        return {"connected": False, "uses_token_json": uid == TOKEN_JSON_OWNER}
    return {"connected": True, "uses_token_json": False, **row._mapping}


def disconnect(uid: int) -> bool:
    with get_engine().begin() as conn:
        token = conn.execute(text("DELETE FROM ledger.gmail_connections WHERE user_id = :u "
                                  "RETURNING refresh_token_encrypted"), {"u": uid}).scalar()
    if token is None:
        return False
    try:
        google_oauth.revoke(secrets.decrypt(token))
    except RuntimeError:
        pass  # SECRET_KEY changed: nothing to revoke with; the row is gone either way
    return True


def _set_error(uid: int, error: Optional[str]) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE ledger.gmail_connections SET last_error = :e, "
                          "last_used_at = CASE WHEN CAST(:e AS text) IS NULL THEN now() ELSE last_used_at END "
                          "WHERE user_id = :u"), {"u": uid, "e": error})


def mail_client_for(uid: int) -> MailClient:
    """The mailbox this user's sync reads. Raises NotConnected with a message for the user."""
    with get_engine().connect() as conn:
        token = conn.execute(text("SELECT refresh_token_encrypted FROM ledger.gmail_connections WHERE user_id = :u"),
                             {"u": uid}).scalar()
    if token is None:
        if uid == TOKEN_JSON_OWNER:
            return GmailServiceClient(_service_url())
        raise NotConnected("Gmail is not connected for this user yet; connect it in Settings")
    try:
        access = google_oauth.refresh_access_token(secrets.decrypt(token))
    except google_oauth.InvalidGrant:
        _set_error(uid, "expired")
        raise NotConnected("Gmail access expired or was removed; connect Gmail again in Settings") from None
    except RuntimeError:   # SECRET_KEY missing or changed
        _set_error(uid, "secret_key")
        raise NotConnected("Stored Gmail access cannot be decrypted (SECRET_KEY changed); connect Gmail again") from None
    _set_error(uid, None)
    return GmailServiceClient(_service_url(), access_token=access)
