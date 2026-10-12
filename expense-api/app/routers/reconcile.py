from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import current_user_id
from app.db import get_engine
from ledger.transactions import Invalid, NotFound
from reconcile import transfer_matches as tm
from reconcile.card_payments import link_card_payments
from reconcile.marketplace import link_orders, unlink

router = APIRouter(prefix="/reconcile", tags=["reconcile"])


@router.post("")
def run_reconcile(uid: int = Depends(current_user_id)):
    """Re-run the order and card-payment matching (also runs after every POST /sync)."""
    with get_engine().begin() as conn:
        return {"orders": link_orders(conn, uid), "card_payments": link_card_payments(conn, uid)}


@router.get("/transfer-matches")
def transfer_matches(uid: int = Depends(current_user_id)):
    """Incoming rows that look like a transfer already recorded (same amount, within 2 days)."""
    with get_engine().connect() as conn:
        return tm.suggestions(conn, uid)


class LinkRequest(BaseModel):
    income_id: int
    transfer_id: int


@router.post("/transfer-matches")
def link_transfer(body: LinkRequest, uid: int = Depends(current_user_id)):
    with get_engine().begin() as conn:
        try:
            return tm.link(conn, uid, body.income_id, body.transfer_id)
        except tm.NotFound:
            raise HTTPException(status_code=404, detail="transaction not found")
        except tm.Invalid as e:
            raise HTTPException(status_code=422, detail=str(e))


@router.delete("/orders/{payment_id}")
def unlink_orders(payment_id: int, uid: int = Depends(current_user_id)):
    """Undo the Shopee/Lazada order split of a pending payment; that pairing is not proposed again."""
    with get_engine().begin() as conn:
        try:
            return {"removed": unlink(conn, uid, payment_id)}
        except NotFound:
            raise HTTPException(status_code=404, detail="transaction not found")
        except Invalid as e:
            raise HTTPException(status_code=422, detail=str(e))
