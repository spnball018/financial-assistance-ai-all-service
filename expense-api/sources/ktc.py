"""KTC credit card statements (PDF attached to email). Owned by the KTC track: docs/ktc-track.md

The PDF itself is read by credit-card-statement-service (POST /parse-ktc-pdf); this module only
turns that JSON into a statement -> statement_line tree. It never logs or returns the password or
PDF content in errors.
"""
import hashlib
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

from sources.base import ParseContext, ParseResult, RawAttachment, RawEmail, TxnNode

SOURCE = "ktc_pdf"
PARSER_VERSION = "ktc-1"

BANGKOK = ZoneInfo("Asia/Bangkok")
PASSWORD_KEY = "ktc_pdf_password"
SERVICE_TIMEOUT = 60.0
# Line types that are not expenses on this statement: payments become a transfer in T2.2,
# refunds are in the backlog. They are kept in the statement's raw.skipped_lines.
SKIPPED_TYPES = {"payment", "refund"}


def _is_pdf(a: RawAttachment) -> bool:
    return a.mime_type == "application/pdf" or a.filename.lower().endswith(".pdf")


def _midnight(day: str) -> datetime:
    return datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=BANGKOK)


def _line_key(card4: str, line: dict, amount: Decimal, seq: int) -> str:
    parts = [card4, line["posting_date"], line["description"], f"{amount:.2f}", line.get("approval_code") or "", str(seq)]
    return "ktc:line:" + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def build_tree(data: dict) -> Optional[TxnNode]:
    """Convert a /parse-ktc-pdf response into the statement tree; None if no line is an expense."""
    st = data["statement"]
    card4 = st["card_last4"]
    children: list[TxnNode] = []
    skipped: list[dict] = []
    seen: dict[tuple, int] = {}

    for line in data["lines"]:
        amount = Decimal(line["amount"])
        if line["type"] in SKIPPED_TYPES or amount <= 0:
            skipped.append(dict(line))
            continue
        ident = (line["posting_date"], line["description"], amount, line.get("approval_code") or "")
        seq = seen.get(ident, 0)  # identical lines within one statement must not dedup onto each other
        seen[ident] = seq + 1
        children.append(TxnNode(
            node_type="statement_line",
            kind="expense",
            amount=amount,
            occurred_at=_midnight(line.get("transaction_date") or line["posting_date"]),
            has_time=False,
            dedup_key=_line_key(card4, line, amount, seq),
            merchant_raw=line["description"],
            source_ref=line.get("approval_code"),
            raw=dict(line),
        ))

    if not children:
        return None
    return TxnNode(
        node_type="statement",
        kind="expense",
        amount=sum((c.amount for c in children), Decimal(0)),
        occurred_at=_midnight(st["statement_date"]),
        has_time=False,
        dedup_key=f"ktc:stmt:{card4}:{st['statement_date']}",
        description=f"KTC e-Statement {st['statement_date']}",
        raw={
            "statement_date": st["statement_date"],
            "due_date": st["due_date"],
            "total_new_balance": st["total_new_balance"],
            "skipped_lines": skipped,
        },
        children=children,
    )


def _parse_pdf_remote(content: bytes, filename: str, password: str) -> tuple[Optional[dict], Optional[str]]:
    """(json, None) on success, else (None, error message without secrets or PDF content)."""
    base = os.environ.get("CREDIT_CARD_STATEMENT_SERVICE_URL")
    if not base:
        return None, "CREDIT_CARD_STATEMENT_SERVICE_URL is not set"
    try:
        r = httpx.post(f"{base.rstrip('/')}/parse-ktc-pdf", files={"file": (filename, content, "application/pdf")},
                       data={"password": password}, timeout=SERVICE_TIMEOUT)
    except httpx.HTTPError as e:
        return None, f"statement service unreachable ({type(e).__name__})"
    if r.status_code == 401:
        return None, "KTC PDF password was rejected; update it in Settings"
    if r.status_code == 422:
        try:
            code = r.json()["detail"]["code"]
        except (ValueError, KeyError, TypeError):
            code = "unknown"
        return None, f"KTC PDF could not be parsed ({code})"
    if r.status_code != 200:
        return None, f"statement service returned HTTP {r.status_code}"
    return r.json(), None


def parse(email: RawEmail, ctx: ParseContext) -> ParseResult:
    pdfs = [a for a in email.attachments if _is_pdf(a)]
    if not pdfs:
        return ParseResult(status="skipped", error="no PDF statement attached")

    password = ctx.get_setting(PASSWORD_KEY)
    if not password:
        return ParseResult(status="failed", error="ยังไม่ได้ตั้งรหัสผ่าน KTC (PUT /settings/ktc-password)")

    nodes: list[TxnNode] = []
    for att in pdfs:
        data, error = _parse_pdf_remote(ctx.get_attachment(att.attachment_id), att.filename, password)
        if error:
            return ParseResult(status="failed", error=error)
        tree = build_tree(data)
        if tree:
            nodes.append(tree)
    if not nodes:
        return ParseResult(status="skipped", error="statement has no expense lines")
    return ParseResult(status="parsed", nodes=nodes)
