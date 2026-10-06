"""Connect the signed-in user's own Gmail (P7.3).

/gmail/connect sends the browser to Google for gmail.readonly with offline access; /gmail/callback
stores the refresh token encrypted and returns to Settings with ?gmail=connected or ?gmail=<error>.
No endpoint ever returns the token.
"""
import hmac
import json
import logging
import secrets as pysecrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import auth, gmail_connections, google_oauth, secrets
from app.auth import current_user_id
from app.routers.auth import _safe_next

router = APIRouter(prefix="/gmail", tags=["gmail"])
log = logging.getLogger(__name__)

FLOW_COOKIE = "expense_gmail_flow"
FLOW_TTL = 600


def _redirect_uri() -> str:
    return auth.public_base_url() + "/api/gmail/callback"


def _back(next_path: str, result: str) -> RedirectResponse:
    # the result goes in the query string, before the #tab the UI reads
    res = RedirectResponse(f"{auth.public_base_url()}/?gmail={result}#{next_path.lstrip('/#') or 'settings'}",
                           status_code=303)
    res.delete_cookie(FLOW_COOKIE, path="/")
    return res


@router.get("/status")
def gmail_status(uid: int = Depends(current_user_id)):
    """connected, gmail_address, connected_at, last_used_at, last_error (expired | secret_key);
    uses_token_json = user 1 still reading gmail-service's token.json."""
    return gmail_connections.status(uid)


@router.get("/connect", include_in_schema=False)
def connect(request: Request, next: Optional[str] = None, uid: int = Depends(current_user_id)):
    state = pysecrets.token_urlsafe(24)
    verifier, challenge = google_oauth.new_pkce()
    try:
        url = google_oauth.authorization_url(
            _redirect_uri(), state, challenge, scope=gmail_connections.GMAIL_SCOPE,
            access_type="offline", prompt="consent select_account", include_granted_scopes="false")
        # bound to this user: a callback finished in another user's session is refused
        flow = secrets.encrypt(json.dumps({"state": state, "verifier": verifier, "uid": uid,
                                           "next": _safe_next(next or "/#settings")}))
    except (google_oauth.OAuthError, RuntimeError) as e:
        log.warning("gmail connect not configured: %s", e)
        return _back("/#settings", "config")
    res = RedirectResponse(url, status_code=303)
    res.set_cookie(FLOW_COOKIE, flow, max_age=FLOW_TTL, httponly=True, secure=auth.cookie_secure(),
                   samesite="lax", path="/")
    return res


@router.get("/callback", include_in_schema=False)
def callback(request: Request, code: Optional[str] = None, state: Optional[str] = None,
             error: Optional[str] = None, uid: int = Depends(current_user_id)):
    try:
        flow = json.loads(secrets.decrypt(request.cookies.get(FLOW_COOKIE) or "", ttl=FLOW_TTL))
    except (RuntimeError, ValueError):
        return _back("/#settings", "state")
    if error:
        return _back(flow["next"], "denied")
    if not code or not state or not hmac.compare_digest(state, flow["state"]) or flow["uid"] != uid:
        return _back(flow["next"], "state")
    try:
        tokens = google_oauth.exchange_code_for_tokens(code, _redirect_uri(), flow["verifier"])
        if gmail_connections.GMAIL_SCOPE not in tokens.get("scope", "").split():
            return _back(flow["next"], "scope")       # the user unticked Gmail on the consent screen
        if not tokens.get("refresh_token"):
            return _back(flow["next"], "failed")
        address = gmail_connections.gmail_address(tokens["access_token"])
    except google_oauth.OAuthError as e:
        log.warning("gmail connect failed: %s", e)
        return _back(flow["next"], "failed")
    gmail_connections.save(uid, address, tokens["refresh_token"], tokens["scope"])
    return _back(flow["next"], "connected")


@router.delete("/connection", status_code=204)
def disconnect(uid: int = Depends(current_user_id)):
    """Forget the stored access and revoke it at Google."""
    if not gmail_connections.disconnect(uid):
        raise HTTPException(status_code=404, detail="Gmail is not connected")
