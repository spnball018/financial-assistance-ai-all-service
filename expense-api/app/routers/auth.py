"""Sign-in with Google, sign-out, who am I, and API token management.

The browser reaches these as /api/auth/*; Google redirects back to PUBLIC_BASE_URL/api/auth/callback.
Failures redirect to the web UI with ?login_error=<code> instead of showing JSON.
"""
import hmac
import json
import logging
import secrets as pysecrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app import auth, google_oauth, secrets
from app.auth import Principal, require_session, require_user

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)

FLOW_COOKIE = "expense_oauth_flow"
FLOW_TTL = 600


def _redirect_uri() -> str:
    return auth.public_base_url() + "/api/auth/callback"


def _safe_next(value: Optional[str]) -> str:
    """Only same-site paths, so the sign-in link cannot bounce the user to another site."""
    if (not value or not value.startswith("/") or value.startswith("//") or "\\" in value
            or any(c <= " " for c in value)):
        return "/"
    return value


def _to_ui(path: str) -> RedirectResponse:
    res = RedirectResponse(auth.public_base_url() + path, status_code=303)
    res.delete_cookie(FLOW_COOKIE, path="/")
    return res


def _fail(code: str, reason: str) -> RedirectResponse:
    log.warning("sign-in failed: %s", reason)
    return _to_ui(f"/?login_error={code}")


@router.get("/login", include_in_schema=False)
def login(next: Optional[str] = None):
    if auth.auth_mode() == "off":
        return _to_ui(_safe_next(next))
    state, nonce = pysecrets.token_urlsafe(24), pysecrets.token_urlsafe(24)
    verifier, challenge = google_oauth.new_pkce()
    try:
        url = google_oauth.authorization_url(_redirect_uri(), state, nonce, challenge)
        flow = secrets.encrypt(json.dumps({"state": state, "nonce": nonce, "verifier": verifier,
                                           "next": _safe_next(next)}))
    except (google_oauth.OAuthError, RuntimeError) as e:
        return _fail("config", str(e))
    res = RedirectResponse(url, status_code=303)
    res.set_cookie(FLOW_COOKIE, flow, max_age=FLOW_TTL, httponly=True, secure=auth.cookie_secure(),
                   samesite="lax", path="/")
    return res


@router.get("/callback", include_in_schema=False)
def callback(request: Request, code: Optional[str] = None, state: Optional[str] = None,
             error: Optional[str] = None):
    if error:
        return _fail("denied", f"google returned {error}")
    try:
        flow = json.loads(secrets.decrypt(request.cookies.get(FLOW_COOKIE) or "", ttl=FLOW_TTL))
    except (RuntimeError, ValueError):
        return _fail("state", "missing or expired flow cookie")
    if not code or not state or not hmac.compare_digest(state, flow["state"]):
        return _fail("state", "state mismatch")

    try:
        claims = google_oauth.exchange_code(code, _redirect_uri(), flow["verifier"])
    except google_oauth.OAuthError as e:
        return _fail("failed", str(e))
    if not hmac.compare_digest(str(claims.get("nonce", "")), flow["nonce"]):
        return _fail("failed", "nonce mismatch")
    email = str(claims.get("email", "")).lower()
    if not email or claims.get("email_verified") is not True:
        return _fail("failed", "email missing or not verified")
    user = auth.user_for_sign_in(email, claims.get("name"))
    if user is None:
        return _fail("not_allowed", f"{email} is not an active user")

    secret, expires = auth.create_session(user["id"], request.headers.get("user-agent"))
    res = _to_ui(flow["next"])
    res.set_cookie(auth.SESSION_COOKIE, secret, expires=expires, httponly=True, secure=auth.cookie_secure(),
                   samesite="lax", path="/")
    return res


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    cookie = request.cookies.get(auth.SESSION_COOKIE)
    if cookie:
        auth.delete_session(cookie)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")


@router.get("/me")
def me(principal: Principal = Depends(require_user)):
    user = next(u for u in auth.list_users() if u["id"] == principal.user_id)
    return {"id": principal.user_id, "email": principal.email, "name": user["name"], "role": principal.role,
            "via": principal.via, "auth_mode": auth.auth_mode()}


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_in_days: Optional[int] = Field(default=90, ge=1, le=3650, description="null = never expires")


@router.get("/tokens")
def list_tokens(principal: Principal = Depends(require_session)):
    """Your own tokens."""
    return auth.list_tokens(principal.user_id)


@router.post("/tokens", status_code=201)
def create_token(body: TokenIn, principal: Principal = Depends(require_session)):
    """The response holds the token itself; it cannot be shown again. It acts as you."""
    return auth.create_token(principal.user_id, body.name.strip(), body.expires_in_days)


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(token_id: int, principal: Principal = Depends(require_session)):
    if not auth.revoke_token(principal.user_id, token_id):
        raise HTTPException(status_code=404, detail="Token not found")
