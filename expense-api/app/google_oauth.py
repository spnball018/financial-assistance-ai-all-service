"""Google OpenID Connect, authorization-code flow with PKCE (server side; the client secret never
reaches the browser). Needs a "Web application" OAuth client whose redirect URI is
PUBLIC_BASE_URL + /api/auth/callback. This is a different client from gmail-service's credentials.json.
"""
import base64
import hashlib
import os
import secrets
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class OAuthError(Exception):
    """Sign-in failed; the message is safe to log (no tokens or codes)."""


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


def authorization_url(redirect_uri: str, state: str, nonce: str, challenge: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    })


def exchange_code(code: str, redirect_uri: str, verifier: str) -> dict:
    """Returns the verified ID token claims."""
    try:
        res = httpx.post(TOKEN_URL, timeout=15, data={
            "code": code, "client_id": client_id(), "client_secret": _client_secret(),
            "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier})
    except httpx.HTTPError as e:
        raise OAuthError(f"token endpoint unreachable: {type(e).__name__}") from None
    if res.status_code != 200:
        # Google's error code only (e.g. invalid_grant); the body never holds our secret
        try:
            err = res.json().get("error", "?")
        except ValueError:
            err = "?"
        raise OAuthError(f"token endpoint returned {res.status_code}: {err}")
    raw = res.json().get("id_token")
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
