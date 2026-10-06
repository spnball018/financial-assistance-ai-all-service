"""T2.2: money that paid the KTC card is a transfer to the card, not an expense.

Two ways to recognise it, applied only to pending root rows the user has not decided on
(category_source none/ai):
1. a K PLUS bill payment to KRUNGTHAI CARD (sources/kbank.py flags it as raw.ktc_bill_payment);
2. a payment line on a KTC statement ("Payment-KTB Internet", skipped from the statement's children)
   that matches exactly one transfer/expense out of another account: same amount, from 3 days before
   to 1 day after the posting date. This catches KBank -> own KTB account -> KTC, the user's usual path.
Matched rows become kind=transfer to the KTC account and point at the statement (ref_transaction_id).
"""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text

WINDOW_BEFORE = timedelta(days=3)
WINDOW_AFTER = timedelta(days=1)

_TO_CARD = text("""
UPDATE ledger.transactions
SET kind = 'transfer', counter_account_id = :card, category_id = NULL, category_source = 'account',
    ai_confidence = NULL, ai_reason = NULL, ref_transaction_id = :stmt, updated_at = now()
WHERE id = :id""")

_UNDECIDED = """
  t.parent_id IS NULL AND t.status = 'pending' AND t.category_source IN ('none', 'ai')
  AND t.kind IN ('expense', 'transfer')
  AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = t.id)"""


def _card_account(conn):
    return conn.execute(text("SELECT account_id FROM ledger.email_sources WHERE source = 'ktc_pdf'")).scalar()


def _payment_lines(conn, card: int) -> list[tuple[int, Decimal, date]]:
    """(statement id, amount, posting date) for every payment line on the card's statements."""
    out = []
    rows = conn.execute(text("SELECT id, raw FROM ledger.transactions WHERE node_type = 'statement' AND account_id = :c"),
                        {"c": card})
    for sid, raw in rows:
        for line in (raw or {}).get("skipped_lines", []):
            if line.get("type") == "payment" and line.get("posting_date"):
                out.append((sid, abs(Decimal(str(line["amount"]))), date.fromisoformat(line["posting_date"])))
    return out


def link_card_payments(conn) -> dict:
    card = _card_account(conn)
    counts = {"bill_payments": 0, "matched_by_amount": 0, "ambiguous": 0}
    if card is None:
        return counts
    lines = _payment_lines(conn, card)

    def statement_for(amount: Decimal, when: date):
        hits = [sid for sid, a, d in lines if a == amount and d - WINDOW_AFTER <= when <= d + WINDOW_BEFORE]
        return hits[0] if len(hits) == 1 else None

    # 1. direct bill payments to KRUNGTHAI CARD
    for r in conn.execute(text(f"""
            SELECT t.id, t.amount, (t.occurred_at AT TIME ZONE 'Asia/Bangkok')::date AS day FROM ledger.transactions t
            WHERE {_UNDECIDED} AND t.account_id <> :card AND t.raw->>'ktc_bill_payment' = 'true'"""), {"card": card}).all():
        conn.execute(_TO_CARD, {"card": card, "stmt": statement_for(r.amount, r.day), "id": r.id})
        counts["bill_payments"] += 1

    # 2. statement payment lines matched to one outgoing row by amount and date
    for sid, amount, posted in lines:
        already = conn.execute(text("SELECT EXISTS (SELECT 1 FROM ledger.transactions WHERE ref_transaction_id = :s "
                                    "AND amount = :a AND kind = 'transfer')"), {"s": sid, "a": amount}).scalar()
        if already:
            continue
        cands = conn.execute(text(f"""
            SELECT t.id FROM ledger.transactions t
            WHERE {_UNDECIDED} AND t.account_id <> :card AND t.amount = :a
              AND (t.kind = 'expense' OR t.counter_account_id IS DISTINCT FROM :card)
              AND (t.occurred_at AT TIME ZONE 'Asia/Bangkok')::date BETWEEN :lo AND :hi"""),
            {"card": card, "a": amount, "lo": posted - WINDOW_BEFORE, "hi": posted + WINDOW_AFTER}).scalars().all()
        if len(cands) == 1:
            conn.execute(_TO_CARD, {"card": card, "stmt": sid, "id": cands[0]})
            counts["matched_by_amount"] += 1
        elif len(cands) > 1:
            counts["ambiguous"] += 1  # leave it to the user rather than guess
    return counts
