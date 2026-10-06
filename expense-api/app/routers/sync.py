import os

from fastapi import APIRouter, Depends, HTTPException

from app.db import get_engine
from categorize.ai import classifier_from_env
from categorize.service import categorize_pending
from reconcile.card_payments import link_card_payments
from sync import engine as sync_engine
from sync import repo
from sync.gmail_client import GmailServiceClient, MailClient

router = APIRouter(prefix="/sync", tags=["sync"])


def get_mail_client() -> MailClient:
    return GmailServiceClient(os.environ.get("GMAIL_SERVICE_URL", "http://gmail-service:8000"))


@router.post("")
def run_sync(mail: MailClient = Depends(get_mail_client)):
    """Pull new emails for every active source, add pending transactions, then categorize them.
    Can take minutes on the first run (3 months of mail)."""
    try:
        result = sync_engine.run_sync(get_engine(), mail)
    except sync_engine.SyncBusy:
        raise HTTPException(status_code=409, detail="A sync is already running")
    try:
        # built here, not as a dependency: a misconfigured categorizer must not block the sync
        categorized = categorize_pending(get_engine(), classifier_from_env())
    except Exception as e:  # the sync itself succeeded; categorizing can be re-run via POST /categorize
        categorized = {"error": type(e).__name__}
    try:
        with get_engine().begin() as conn:
            reconciled = link_card_payments(conn)
    except Exception as e:  # same: re-runnable via POST /reconcile
        reconciled = {"error": type(e).__name__}
    return {"run_id": result.run_id, "counts": result.counts, "categorize": categorized, "reconcile": reconciled}


@router.post("/reparse")
def reparse(source: str, mail: MailClient = Depends(get_mail_client)):
    """Re-run a source's parser over the stored raw emails (no Gmail listing)."""
    try:
        return {"source": source, "counts": sync_engine.reparse(get_engine(), mail, source)}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No parser registered for '{source}'")


@router.get("/runs")
def list_runs(limit: int = 20):
    with get_engine().connect() as conn:
        return repo.list_runs(conn, min(max(limit, 1), 100))
