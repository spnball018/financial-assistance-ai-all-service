import os

from fastapi import APIRouter, Depends, HTTPException

from app.auth import current_user_id
from app.db import get_engine
from categorize.ai import classifier_from_env
from categorize.service import categorize_pending
from reconcile.card_payments import link_card_payments
from sync import engine as sync_engine
from sync import repo
from sync.gmail_client import GmailServiceClient, MailClient

router = APIRouter(prefix="/sync", tags=["sync"])

# gmail-service reads one mailbox (its token.json), which belongs to user 1. Until each user connects
# their own Gmail (P7.3), nobody else may sync: they would pull user 1's email into their ledger.
GMAIL_SERVICE_OWNER = 1


def get_mail_client(uid: int = Depends(current_user_id)) -> MailClient:
    if uid != GMAIL_SERVICE_OWNER:
        raise HTTPException(status_code=409, detail="Gmail is not connected for this user yet")
    return GmailServiceClient(os.environ.get("GMAIL_SERVICE_URL", "http://gmail-service:8000"))


@router.post("")
def run_sync(mail: MailClient = Depends(get_mail_client), uid: int = Depends(current_user_id)):
    """Pull new emails for every active source of the signed-in user, add pending transactions, then
    categorize them. Can take minutes on the first run (3 months of mail)."""
    try:
        result = sync_engine.run_sync(get_engine(), uid, mail)
    except sync_engine.SyncBusy:
        raise HTTPException(status_code=409, detail="A sync is already running")
    try:
        # built here, not as a dependency: a misconfigured categorizer must not block the sync
        categorized = categorize_pending(get_engine(), uid, classifier_from_env())
    except Exception as e:  # the sync itself succeeded; categorizing can be re-run via POST /categorize
        categorized = {"error": type(e).__name__}
    try:
        with get_engine().begin() as conn:
            reconciled = link_card_payments(conn, uid)
    except Exception as e:  # same: re-runnable via POST /reconcile
        reconciled = {"error": type(e).__name__}
    return {"run_id": result.run_id, "counts": result.counts, "categorize": categorized, "reconcile": reconciled}


@router.post("/reparse")
def reparse(source: str, mail: MailClient = Depends(get_mail_client), uid: int = Depends(current_user_id)):
    """Re-run a source's parser over the stored raw emails (no Gmail listing)."""
    try:
        return {"source": source, "counts": sync_engine.reparse(get_engine(), uid, mail, source)}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No parser registered for '{source}'")


@router.get("/runs")
def list_runs(limit: int = 20, uid: int = Depends(current_user_id)):
    with get_engine().connect() as conn:
        return repo.list_runs(conn, uid, min(max(limit, 1), 100))
