from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.db import get_engine

router = APIRouter(tags=["merchant rules"])


@router.get("/merchant-rules")
def list_rules():
    """What Merchant Memory has learned, most used first. merchant_key is 'payee' or, for transfer
    rules learned with a destination number, 'payee|last4'."""
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT m.merchant_key, m.action, m.category_id, k.name_th AS category_name,
                   m.counter_account_id, a.name AS account_name, m.hit_count, m.last_confirmed_at,
                   (SELECT count(*) FROM ledger.transactions t
                     WHERE t.merchant_key = split_part(m.merchant_key, '|', 1)) AS transactions
            FROM ledger.merchant_memory m
            LEFT JOIN ledger.categories k ON k.id = m.category_id
            LEFT JOIN ledger.accounts a ON a.id = m.counter_account_id
            ORDER BY m.hit_count DESC, m.last_confirmed_at DESC"""))
        return [dict(r._mapping) for r in rows]


@router.delete("/merchant-rules", status_code=204)
def delete_rule(merchant_key: str = Query(..., min_length=1)):
    """Forget one rule. Transactions keep their category; only future categorizing changes."""
    with get_engine().begin() as conn:
        n = conn.execute(text("DELETE FROM ledger.merchant_memory WHERE merchant_key = :k"),
                         {"k": merchant_key}).rowcount
    if n == 0:
        raise HTTPException(status_code=404, detail="rule not found")
