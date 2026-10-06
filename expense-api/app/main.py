import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.auth import current_user_id, require_user
from app.db import get_engine
from app.routers import accounts, auth, categorize, ktc, receipts, reconcile, rules, sync, transactions, users

# Swagger is mounted below behind require_user, like everything else
app = FastAPI(
    title="Expense API",
    description="Sync, reconcile, categorize and manage expense transactions. "
                "Sign in through the web UI, or send `Authorization: Bearer <API token>`.",
    docs_url=None, redoc_url=None, openapi_url=None,
)

# Refuse requests whose Host is not ours, so a web page that DNS-rebinds its own domain to 127.0.0.1
# cannot reach this API from the user's browser. Add the public hostname to ALLOWED_HOSTS.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[
    h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,expense-api,testserver").split(",") if h.strip()])

# Open without sign-in: /health and /auth/login, /auth/callback, /auth/logout (tests/test_auth.py)
app.include_router(auth.router)

protected = APIRouter(dependencies=[Depends(require_user)])


@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True}


def _prefix(request: Request) -> str:
    """/api when reached through the web proxy (nginx / Vite send X-Forwarded-Prefix)."""
    value = request.headers.get("x-forwarded-prefix", "")
    return value if re.fullmatch(r"(/[a-z]+)?", value) else ""


@protected.get("/openapi.json", include_in_schema=False)
def openapi(request: Request):
    return {**app.openapi(), "servers": [{"url": _prefix(request) or "/"}]}


@protected.get("/docs", include_in_schema=False)
def docs(request: Request):
    return get_swagger_ui_html(openapi_url=_prefix(request) + "/openapi.json", title="Expense API")


@protected.get("/categories")
def list_categories():
    """Flat list ordered so parents precede their children; the UI builds the tree."""
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT id, parent_id, name_th, name_en, kind, sort_order FROM ledger.categories "
            "ORDER BY COALESCE(parent_id, id), parent_id NULLS FIRST, sort_order"))
        return [dict(r._mapping) for r in rows]


class EmailSourceUpdate(BaseModel):
    gmail_label: Optional[str] = None
    sender_filter: Optional[str] = None
    account_id: Optional[int] = None
    is_active: bool


@protected.get("/email-sources")
def list_email_sources(uid: int = Depends(current_user_id)):
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT id, source, gmail_label, sender_filter, account_id, is_active "
            "FROM ledger.email_sources WHERE user_id = :uid ORDER BY id"), {"uid": uid})
        return [dict(r._mapping) for r in rows]


@protected.put("/email-sources/{source}")
def update_email_source(source: str, body: EmailSourceUpdate, uid: int = Depends(current_user_id)):
    label = (body.gmail_label or "").strip() or None
    if body.is_active and not label:
        raise HTTPException(status_code=422, detail="gmail_label is required to activate a source")
    try:
        with get_engine().begin() as conn:
            row = conn.execute(text(
                "UPDATE ledger.email_sources SET gmail_label = :label, sender_filter = :sender, "
                "account_id = :account, is_active = :active, updated_at = now() "
                "WHERE source = :source AND user_id = :uid "
                "RETURNING id, source, gmail_label, sender_filter, account_id, is_active"),
                {"label": label, "sender": (body.sender_filter or "").strip() or None,
                 "account": body.account_id, "active": body.is_active, "source": source, "uid": uid}).first()
    except IntegrityError:
        # the (account_id, user_id) foreign key: not one of this user's accounts
        raise HTTPException(status_code=422, detail=f"account {body.account_id} does not exist")
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown source '{source}'")
    return dict(row._mapping)


for r in (accounts, ktc, sync, categorize, transactions, rules, receipts, reconcile, users):
    protected.include_router(r.router)
app.include_router(protected)
