from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_engine
from reconcile import transfer_matches as tm
from reconcile.card_payments import link_card_payments

router = APIRouter(prefix="/reconcile", tags=["reconcile"])


@router.post("")
def run_reconcile():
    """Re-run the card-payment matching (also runs after every POST /sync)."""
    with get_engine().begin() as conn:
        return {"card_payments": link_card_payments(conn)}


@router.get("/transfer-matches")
def transfer_matches():
    """Incoming rows that look like a transfer already recorded (same amount, within 2 days)."""
    with get_engine().connect() as conn:
        return tm.suggestions(conn)


class LinkRequest(BaseModel):
    income_id: int
    transfer_id: int


@router.post("/transfer-matches")
def link_transfer(body: LinkRequest):
    with get_engine().begin() as conn:
        try:
            return tm.link(conn, body.income_id, body.transfer_id)
        except tm.NotFound:
            raise HTTPException(status_code=404, detail="transaction not found")
        except tm.Invalid as e:
            raise HTTPException(status_code=422, detail=str(e))
