"""K PLUS result emails -> one `payment` node (expense). Text extraction lives in parsers/kbank.py."""
from sources.base import ParseContext, ParseResult, RawEmail, TxnNode
from parsers.kbank import _CHANNELS, _SUBJECT_RE, parse_kbank

SOURCE = "kbank_email"
PARSER_VERSION = "kbank-1"

KTC_PAYEE = "KRUNGTHAI CARD PUBLIC COMPANY LIMITED"


def parse(email: RawEmail, ctx: ParseContext) -> ParseResult:
    subj = _SUBJECT_RE.search(email.text_body or "")
    if not subj:
        return ParseResult(status="skipped", error="not a K PLUS result email")
    if subj.group(1).strip().lower() not in _CHANNELS:
        # e.g. Top-up: not supported yet. Raw email is kept, so /sync/reparse can pick it up later.
        return ParseResult(status="skipped", error=f"unsupported type: {subj.group(1).strip()}")

    t = parse_kbank({"id": email.gmail_message_id, "text_body": email.text_body})
    if t is None:
        return ParseResult(status="failed", error="K PLUS email did not match the expected layout")

    node = TxnNode(
        node_type="payment",
        kind="expense",
        amount=t.amount + t.fee,  # fee is folded into the amount (almost always 0.00)
        occurred_at=t.occurred_at,
        dedup_key=t.dedup_key,
        has_time=True,
        merchant_raw=t.merchant_raw or None,
        description=t.channel,
        source_ref=t.txn_no,
        raw={
            "channel": t.channel,
            "fee": str(t.fee),
            "from_account": t.from_account,
            "to_account": t.to_account,
            # T2.2 turns these into a KBank -> KTC transfer
            "ktc_bill_payment": t.channel == "bill_payment" and KTC_PAYEE in (t.merchant_raw or "").upper(),
        },
    )
    return ParseResult(status="parsed", nodes=[node])
