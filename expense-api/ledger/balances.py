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


def set_opening(conn, uid: int, account_id: int, amount: Decimal, as_of: date) -> dict:
    """One opening row per account (upsert). Credit cards: use a negative amount for money owed."""
    if not conn.execute(text("SELECT EXISTS (SELECT 1 FROM ledger.accounts WHERE id = :a AND user_id = :uid)"),
                        {"a": account_id, "uid": uid}).scalar():
        raise NotFound(account_id)
    at = datetime.combine(as_of, time(0, 0), tzinfo=BANGKOK)
    conn.execute(text("""
        INSERT INTO ledger.transactions (user_id, account_id, node_type, kind, status, amount, occurred_at, has_time, source,
                                         dedup_key, description, category_source, confirmed_at)
        VALUES (:uid, :a, 'opening_balance', 'opening', 'confirmed', :amt, :at, false, 'manual', :dk, 'ยอดยกมา', 'manual', now())
        ON CONFLICT (user_id, dedup_key) DO UPDATE SET amount = EXCLUDED.amount, occurred_at = EXCLUDED.occurred_at,
                                               updated_at = now()"""),
                 {"a": account_id, "amt": amount, "at": at, "dk": f"opening:{account_id}", "uid": uid})
    return get_opening(conn, uid, account_id)


def get_opening(conn, uid: int, account_id: int) -> Optional[dict]:
    r = conn.execute(text("SELECT amount, occurred_at FROM ledger.transactions WHERE user_id = :uid AND dedup_key = :dk"),
                     {"dk": f"opening:{account_id}", "uid": uid}).first()
    return {"amount": r.amount, "as_of": r.occurred_at.astimezone(BANGKOK).date()} if r else None


def delete_opening(conn, uid: int, account_id: int) -> None:
    conn.execute(text("DELETE FROM ledger.transactions WHERE user_id = :uid AND dedup_key = :dk"),
                 {"dk": f"opening:{account_id}", "uid": uid})


_BALANCES = text("""
WITH opening AS (
  SELECT account_id, occurred_at AS since FROM ledger.transactions
  WHERE user_id = :uid AND node_type = 'opening_balance'
)
SELECT a.id AS account_id, a.name, a.type, a.is_active,
       o.since,
       COALESCE(sum(p.amount) FILTER (WHERE p.status = 'confirmed'), 0) AS balance,
       COALESCE(sum(p.amount) FILTER (WHERE p.status IN ('confirmed', 'pending')), 0) AS balance_with_pending,
       count(*) FILTER (WHERE p.status = 'pending') AS pending_count,
       max(p.occurred_at) FILTER (WHERE p.kind <> 'opening') AS last_movement_at
FROM ledger.accounts a
LEFT JOIN opening o ON o.account_id = a.id
LEFT JOIN ledger.v_account_postings p ON p.account_id = a.id AND p.user_id = a.user_id
  AND (o.since IS NULL OR p.occurred_at >= o.since)
WHERE a.user_id = :uid
GROUP BY a.id, a.name, a.type, a.is_active, a.sort_order, o.since
ORDER BY a.sort_order, a.id""")


def _computed_until(conn, uid: int, account_id: int, until: datetime, since: Optional[datetime]) -> Decimal:
    """Balance including pending rows, up to and including `until` (what the bank would show then)."""
    return conn.execute(text("""
        SELECT COALESCE(sum(amount), 0) FROM ledger.v_account_postings
        WHERE user_id = :uid AND account_id = :a AND status IN ('confirmed', 'pending') AND occurred_at <= :u
          AND (CAST(:s AS timestamptz) IS NULL OR occurred_at >= :s)"""),
        {"a": account_id, "u": until, "s": since, "uid": uid}).scalar()


def _bank_reported(conn, uid: int, account_id: int, since: Optional[datetime]) -> Optional[dict]:
    """Latest "Available Balance" from a K PLUS email of this account's email source, next to what the
    ledger computes at that moment. A gap usually means money came in that no email reported
    (K PLUS sends emails for payments and transfers out, not for money in)."""
    src = conn.execute(text("SELECT source FROM ledger.email_sources WHERE user_id = :uid AND account_id = :a "
                            "AND source = 'kbank_email'"), {"a": account_id, "uid": uid}).scalar()
    if not src:
        return None
    for r in conn.execute(text("""
            SELECT gmail_message_id, text_body FROM ledger.raw_emails
            WHERE user_id = :uid AND source = 'kbank_email' AND parse_status = 'parsed'
            ORDER BY received_at DESC NULLS LAST LIMIT 5"""), {"uid": uid}):
        t = parse_kbank({"id": r.gmail_message_id, "text_body": r.text_body})
        if t and t.balance_after is not None:
            computed = _computed_until(conn, uid, account_id, t.occurred_at, since)
            return {"balance": t.balance_after, "as_of": t.occurred_at, "computed": computed,
                    "difference": t.balance_after - computed}
    return None


def balances(conn, uid: int) -> list[dict]:
    out = []
    for r in conn.execute(_BALANCES, {"uid": uid}):
        row = dict(r._mapping)
        since = row.pop("since")
        row["opening"] = get_opening(conn, uid, row["account_id"])
        row["bank_reported"] = _bank_reported(conn, uid, row["account_id"], since)
        out.append(row)
    return out


# Every posting of one account, oldest first, with running balances that add up exactly like _BALANCES:
# rows before the opening day and ignored rows are listed but do not count. `bank_balance` is the
# "Available Balance" of the K PLUS email behind the row (only on the row's own account, not the
# counter side of a transfer).
_LEDGER = text("""
WITH opening AS (
  SELECT occurred_at AS since FROM ledger.transactions
  WHERE user_id = :uid AND node_type = 'opening_balance' AND account_id = :a
), p AS (
  SELECT p.transaction_id, p.status, p.occurred_at, p.kind, p.amount,
         p.status IN ('confirmed', 'pending') AND (o.since IS NULL OR p.occurred_at >= o.since) AS counted
  FROM ledger.v_account_postings p LEFT JOIN opening o ON true
  WHERE p.user_id = :uid AND p.account_id = :a
)
SELECT p.transaction_id, p.status, p.occurred_at, p.kind, p.amount, p.counted,
       t.has_time, t.node_type, t.source, t.merchant_raw, t.description, t.note,
       t.raw->>'channel' AS channel,
       c.name_th AS category_th,
       ca.name AS other_account,
       CASE WHEN t.account_id = :a THEN CAST(t.raw->>'balance_after' AS numeric) END AS bank_balance,
       sum(p.amount) FILTER (WHERE p.counted AND p.status = 'confirmed')
         OVER (ORDER BY p.occurred_at, p.transaction_id) AS balance,
       sum(p.amount) FILTER (WHERE p.counted)
         OVER (ORDER BY p.occurred_at, p.transaction_id) AS balance_with_pending
FROM p
JOIN ledger.transactions t ON t.id = p.transaction_id AND t.user_id = :uid
LEFT JOIN ledger.categories c ON c.id = t.category_id
LEFT JOIN ledger.accounts ca ON ca.user_id = :uid
  AND ca.id = CASE WHEN t.account_id = :a THEN t.counter_account_id ELSE t.account_id END
ORDER BY p.occurred_at, p.transaction_id""")


def account_ledger(conn, uid: int, account_id: int) -> dict:
    """The account's postings newest first. `difference` = bank_balance - balance_with_pending on rows with
    a bank balance; `gap_starts` marks the row where the difference first changes (the row itself or a
    movement just before it is missing or wrong)."""
    acc = conn.execute(text("SELECT id, name, type, is_active FROM ledger.accounts WHERE id = :a AND user_id = :uid"),
                       {"a": account_id, "uid": uid}).first()
    if acc is None:
        raise NotFound(account_id)
    rows, prev_diff = [], Decimal(0)
    for r in conn.execute(_LEDGER, {"a": account_id, "uid": uid}):
        row = dict(r._mapping)
        row["balance"] = row["balance"] or Decimal(0)
        row["balance_with_pending"] = row["balance_with_pending"] or Decimal(0)
        row["difference"], row["gap_starts"] = None, False
        if row["bank_balance"] is not None and row["counted"]:
            row["difference"] = row["bank_balance"] - row["balance_with_pending"]
            row["gap_starts"] = row["difference"] != prev_diff
            prev_diff = row["difference"]
        rows.append(row)
    rows.reverse()
    return {**dict(acc._mapping), "opening": get_opening(conn, uid, account_id), "rows": rows}
