from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_engine
from app.routers import accounts, categorize, ktc, sync, transactions

app = FastAPI(
    title="Expense API",
    description="Sync, reconcile, categorize and manage expense transactions.",
)

app.include_router(accounts.router)
app.include_router(ktc.router)
app.include_router(sync.router)
app.include_router(categorize.router)
app.include_router(transactions.router)


@app.get("/categories")
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


@app.get("/email-sources")
def list_email_sources():
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT id, source, gmail_label, sender_filter, account_id, is_active "
            "FROM ledger.email_sources ORDER BY id"))
        return [dict(r._mapping) for r in rows]


@app.put("/email-sources/{source}")
def update_email_source(source: str, body: EmailSourceUpdate):
    label = (body.gmail_label or "").strip() or None
    if body.is_active and not label:
        raise HTTPException(status_code=422, detail="gmail_label is required to activate a source")
    with get_engine().begin() as conn:
        row = conn.execute(text(
            "UPDATE ledger.email_sources SET gmail_label = :label, sender_filter = :sender, "
            "account_id = :account, is_active = :active, updated_at = now() "
            "WHERE source = :source "
            "RETURNING id, source, gmail_label, sender_filter, account_id, is_active"),
            {"label": label, "sender": (body.sender_filter or "").strip() or None,
             "account": body.account_id, "active": body.is_active, "source": source}).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown source '{source}'")
    return dict(row._mapping)
