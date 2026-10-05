"""Opening balances and account balances (T6.4), computed from ledger.v_account_postings.

An opening balance is the account's balance at the start of its as-of day (Asia/Bangkok). Movements
before that day are ignored for that account; without an opening balance, all movements count.
"""
from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text

from parsers.kbank import parse_kbank

BANGKOK = ZoneInfo("Asia/Bangkok")


class NotFound(Exception):
    pass


def set_opening(conn, account_id: int, amount: Decimal, as_of: date) -> dict:
    """One opening row per account (upsert). Credit cards: use a negative amount for money owed."""
    if not conn.execute(text("SELECT EXISTS (SELECT 1 FROM ledger.accounts WHERE id = :a)"), {"a": account_id}).scalar():
        raise NotFound(account_id)
    at = datetime.combine(as_of, time(0, 0), tzinfo=BANGKOK)
    conn.execute(text("""
        INSERT INTO ledger.transactions (account_id, node_type, kind, status, amount, occurred_at, has_time, source,
                                         dedup_key, description, category_source, confirmed_at)
        VALUES (:a, 'opening_balance', 'opening', 'confirmed', :amt, :at, false, 'manual', :dk, 'ยอดยกมา', 'manual', now())
        ON CONFLICT (dedup_key) DO UPDATE SET amount = EXCLUDED.amount, occurred_at = EXCLUDED.occurred_at,
                                               updated_at = now()"""),
                 {"a": account_id, "amt": amount, "at": at, "dk": f"opening:{account_id}"})
    return get_opening(conn, account_id)


def get_opening(conn, account_id: int) -> Optional[dict]:
    r = conn.execute(text("SELECT amount, occurred_at FROM ledger.transactions WHERE dedup_key = :dk"),
                     {"dk": f"opening:{account_id}"}).first()
    return {"amount": r.amount, "as_of": r.occurred_at.astimezone(BANGKOK).date()} if r else None


def delete_opening(conn, account_id: int) -> None:
    conn.execute(text("DELETE FROM ledger.transactions WHERE dedup_key = :dk"), {"dk": f"opening:{account_id}"})


_BALANCES = text("""
WITH opening AS (
  SELECT account_id, occurred_at AS since FROM ledger.transactions WHERE node_type = 'opening_balance'
)
SELECT a.id AS account_id, a.name, a.type, a.is_active,
       o.since,
       COALESCE(sum(p.amount) FILTER (WHERE p.status = 'confirmed'), 0) AS balance,
       COALESCE(sum(p.amount) FILTER (WHERE p.status IN ('confirmed', 'pending')), 0) AS balance_with_pending,
       count(*) FILTER (WHERE p.status = 'pending') AS pending_count,
       max(p.occurred_at) FILTER (WHERE p.kind <> 'opening') AS last_movement_at
FROM ledger.accounts a
LEFT JOIN opening o ON o.account_id = a.id
LEFT JOIN ledger.v_account_postings p ON p.account_id = a.id AND (o.since IS NULL OR p.occurred_at >= o.since)
GROUP BY a.id, a.name, a.type, a.is_active, a.sort_order, o.since
ORDER BY a.sort_order, a.id""")


def _computed_until(conn, account_id: int, until: datetime, since: Optional[datetime]) -> Decimal:
    """Balance including pending rows, up to and including `until` (what the bank would show then)."""
    return conn.execute(text("""
        SELECT COALESCE(sum(amount), 0) FROM ledger.v_account_postings
        WHERE account_id = :a AND status IN ('confirmed', 'pending') AND occurred_at <= :u
          AND (CAST(:s AS timestamptz) IS NULL OR occurred_at >= :s)"""),
        {"a": account_id, "u": until, "s": since}).scalar()


def _bank_reported(conn, account_id: int, since: Optional[datetime]) -> Optional[dict]:
    """Latest "Available Balance" from a K PLUS email of this account's email source, next to what the
    ledger computes at that moment. A gap usually means money came in that no email reported
    (K PLUS sends emails for payments and transfers out, not for money in)."""
    src = conn.execute(text("SELECT source FROM ledger.email_sources WHERE account_id = :a AND source = 'kbank_email'"),
                       {"a": account_id}).scalar()
    if not src:
        return None
    for r in conn.execute(text("""
            SELECT gmail_message_id, text_body FROM ledger.raw_emails
            WHERE source = 'kbank_email' AND parse_status = 'parsed'
            ORDER BY received_at DESC NULLS LAST LIMIT 5""")):
        t = parse_kbank({"id": r.gmail_message_id, "text_body": r.text_body})
        if t and t.balance_after is not None:
            computed = _computed_until(conn, account_id, t.occurred_at, since)
            return {"balance": t.balance_after, "as_of": t.occurred_at, "computed": computed,
                    "difference": t.balance_after - computed}
    return None


def balances(conn) -> list[dict]:
    out = []
    for r in conn.execute(_BALANCES):
        row = dict(r._mapping)
        since = row.pop("since")
        row["opening"] = get_opening(conn, row["account_id"])
        row["bank_reported"] = _bank_reported(conn, row["account_id"], since)
        out.append(row)
    return out
