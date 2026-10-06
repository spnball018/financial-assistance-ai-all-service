"""T6.7: pair an incoming row on one of the user's accounts with the transfer that already moved that
money there, so it is not counted twice.

Example: KBank -> SCB 5,000 is a transfer (SCB +5,000 already), and the user also records
"money in, SCB, 5,000" by hand. Linking marks the hand-made row ignored and points it at the transfer.
Suggestions only: the user confirms each link.
"""
from datetime import timedelta

from sqlalchemy import text

WINDOW = timedelta(days=2)


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


def suggestions(conn) -> list[dict]:
    rows = conn.execute(text("""
        SELECT i.id AS income_id, i.account_id, a.name AS account_name, i.amount, i.occurred_at AS income_at,
               i.merchant_raw AS income_from, t.id AS transfer_id, t.account_id AS from_account_id,
               fa.name AS from_account_name, t.occurred_at AS transfer_at, t.status AS transfer_status
        FROM ledger.transactions i
        JOIN ledger.transactions t
          ON t.kind = 'transfer' AND t.counter_account_id = i.account_id AND t.amount = i.amount
         AND t.status <> 'ignored' AND abs(extract(epoch FROM t.occurred_at - i.occurred_at)) <= :w
        JOIN ledger.accounts a ON a.id = i.account_id
        JOIN ledger.accounts fa ON fa.id = t.account_id
        WHERE i.kind = 'income' AND i.status <> 'ignored' AND i.ref_transaction_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM ledger.transactions x WHERE x.ref_transaction_id = t.id AND x.kind = 'income')
        ORDER BY i.occurred_at DESC, abs(extract(epoch FROM t.occurred_at - i.occurred_at))"""),
        {"w": WINDOW.total_seconds()})
    return [dict(r._mapping) for r in rows]


def link(conn, income_id: int, transfer_id: int) -> dict:
    """Keep the transfer (it moves both balances); park the duplicate income as ignored, linked to it."""
    pair = [s for s in suggestions(conn) if s["income_id"] == income_id and s["transfer_id"] == transfer_id]
    if not pair:
        exists = conn.execute(text("SELECT count(*) FROM ledger.transactions WHERE id IN (:a, :b)"),
                              {"a": income_id, "b": transfer_id}).scalar()
        if exists < 2:
            raise NotFound()
        raise Invalid("these two transactions are not a matching transfer/income pair")
    conn.execute(text("UPDATE ledger.transactions SET status = 'ignored', ref_transaction_id = :t, "
                      "description = coalesce(description || ' · ', '') || 'ซ้ำกับการโอนระหว่างบัญชี', updated_at = now() "
                      "WHERE id = :i"), {"t": transfer_id, "i": income_id})
    return pair[0]
