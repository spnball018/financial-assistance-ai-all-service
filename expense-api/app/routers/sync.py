import os

from fastapi import APIRouter, Depends, HTTPException

from app.db import get_engine
from sync import engine as sync_engine
from sync import repo
from sync.gmail_client import GmailServiceClient, MailClient

router = APIRouter(prefix="/sync", tags=["sync"])


def get_mail_client() -> MailClient:
    return GmailServiceClient(os.environ.get("GMAIL_SERVICE_URL", "http://gmail-service:8000"))


@router.post("")
def run_sync(mail: MailClient = Depends(get_mail_client)):
    """Pull new emails for every active source and add pending transactions.
    Can take minutes on the first run (3 months of mail)."""
    try:
        result = sync_engine.run_sync(get_engine(), mail)
    except sync_engine.SyncBusy:
        raise HTTPException(status_code=409, detail="A sync is already running")
    return {"run_id": result.run_id, "counts": result.counts}


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
