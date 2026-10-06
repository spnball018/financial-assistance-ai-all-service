"""Google OAuth, authorization-code flow with PKCE (server side; the client secret never reaches the
browser). One "Web application" client serves two flows:
- sign-in (openid email profile): redirect URI PUBLIC_BASE_URL + /api/auth/callback
- connecting a user's Gmail (gmail.readonly, offline): redirect URI PUBLIC_BASE_URL + /api/gmail/callback
"""
import base64
import hashlib
import os
import secrets
from typing import Optional
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SIGN_IN_SCOPE = "openid email profile"


class OAuthError(Exception):
    """The message is safe to log (no tokens or codes)."""


class InvalidGrant(OAuthError):
    """The refresh token was revoked or expired (Testing-mode apps: after 7 days); reconnect."""


def client_id() -> str:
    value = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    if not value:
        raise OAuthError("GOOGLE_OAUTH_CLIENT_ID is not set")
    return value


def _client_secret() -> str:
    value = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if not value:
        raise OAuthError("GOOGLE_OAUTH_CLIENT_SECRET is not set")
    return value


def new_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorization_url(redirect_uri: str, state: str, challenge: str, *, nonce: Optional[str] = None,
                      scope: str = SIGN_IN_SCOPE, **extra: str) -> str:
    params = {
        "client_id": client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
        **extra,
    }
    if nonce:
        params["nonce"] = nonce
    return AUTH_URL + "?" + urlencode(params)


def _token_request(data: dict) -> dict:
    try:
        res = httpx.post(TOKEN_URL, timeout=15, data={"client_id": client_id(), "client_secret": _client_secret(), **data})
    except httpx.HTTPError as e:
        raise OAuthError(f"token endpoint unreachable: {type(e).__name__}") from None
    if res.status_code != 200:
        # Google's error code only (e.g. invalid_grant); the body never holds our secret
        try:
            err = res.json().get("error", "?")
        except ValueError:
            err = "?"
        if err == "invalid_grant" and data.get("grant_type") == "refresh_token":
            raise InvalidGrant("refresh token revoked or expired")
        raise OAuthError(f"token endpoint returned {res.status_code}: {err}")
    return res.json()


def exchange_code_for_tokens(code: str, redirect_uri: str, verifier: str) -> dict:
    """Raw token response: access_token, refresh_token (when offline access was granted), scope, ..."""
    return _token_request({"code": code, "redirect_uri": redirect_uri, "grant_type": "authorization_code",
                           "code_verifier": verifier})


def refresh_access_token(refresh_token: str) -> str:
    return _token_request({"refresh_token": refresh_token, "grant_type": "refresh_token"})["access_token"]


def revoke(token: str) -> None:
    """Best effort: the user can also remove access at myaccount.google.com/permissions."""
    try:
        httpx.post(REVOKE_URL, data={"token": token}, timeout=15)
    except httpx.HTTPError:
        pass


def exchange_code(code: str, redirect_uri: str, verifier: str) -> dict:
    """Sign-in: returns the verified ID token claims."""
    raw = exchange_code_for_tokens(code, redirect_uri, verifier).get("id_token")
    if not raw:
        raise OAuthError("no id_token in token response")
    return verify_id_token(raw)


def verify_id_token(raw: str) -> dict:
    # signature, issuer, audience and expiry
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    try:
        return id_token.verify_oauth2_token(raw, google_requests.Request(), client_id())
    except ValueError as e:
        raise OAuthError(f"invalid id_token: {e}") from None
