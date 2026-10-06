"""K4: the real KTC e-Statement end to end: PDF -> credit-card-statement-service -> sources.ktc -> ledger.

Skips unless the gitignored fixture, KTC_PDF_PASSWORD and a running service are all available:
    CREDIT_CARD_STATEMENT_SERVICE_URL=http://localhost:8003 KTC_PDF_PASSWORD=... pytest tests/test_ktc_real_pdf.py
"""
import os
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from sources import ktc
from sources.base import ParseContext, RawAttachment, RawEmail
from sync import repo

FIXTURE = Path(__file__).parent / "fixtures" / "ktc_statement_1.pdf"


@pytest.fixture
def real():
    url = os.environ.get("CREDIT_CARD_STATEMENT_SERVICE_URL")
    password = os.environ.get("KTC_PDF_PASSWORD")
    if not (FIXTURE.exists() and password and url):
        pytest.skip("needs the gitignored KTC fixture, KTC_PDF_PASSWORD and CREDIT_CARD_STATEMENT_SERVICE_URL")
    try:
        httpx.get(f"{url}/docs", timeout=3)
    except httpx.HTTPError:
        pytest.skip("credit-card-statement-service is not running")
    email = RawEmail(raw_email_id=0, gmail_message_id="ktc-real", subject="KTC e-Statement", sender="KTC",
                     received_at=None, text_body="", html_body="",
                     attachments=[RawAttachment("att1", "statement.pdf", "application/pdf", FIXTURE.stat().st_size)])
    ctx = ParseContext(account_id=2, get_attachment=lambda _id: FIXTURE.read_bytes(),
                       get_setting=lambda key: password if key == "ktc_pdf_password" else None)
    return email, ctx


def test_real_statement_becomes_a_balanced_tree(real):
    email, ctx = real
    res = ktc.parse(email, ctx)
    assert res.status == "parsed", res.error
    (stmt,) = res.nodes
    assert stmt.node_type == "statement" and stmt.children
    assert all(c.node_type == "statement_line" and c.kind == "expense" and c.amount > 0 for c in stmt.children)
    assert sum(c.amount for c in stmt.children) == stmt.amount
    assert len({c.dedup_key for c in stmt.children}) == len(stmt.children)


def test_real_statement_saves_once(real, db):
    email, ctx = real
    res = ktc.parse(email, ctx)
    with db.begin() as conn:
        rid = repo.insert_raw_email(conn, 1, "ktc_pdf", "financial-ktc-[card]", {"id": "ktc-real", "subject": "s"})
        email.raw_email_id = rid
        n, d = repo.insert_tree(conn, res.nodes[0], user_id=1, account_id=2, source="ktc_pdf", raw_email=email)
        assert d == 0 and n == 1 + len(res.nodes[0].children)
        assert repo.insert_tree(conn, res.nodes[0], user_id=1, account_id=2, source="ktc_pdf", raw_email=email) == (0, 1)
    with db.connect() as conn:
        assert conn.execute(text("SELECT needs_review FROM ledger.transactions WHERE node_type = 'statement'")).scalar() is False
        total = conn.execute(text("SELECT sum(amount) FROM ledger.transactions WHERE node_type = 'statement_line'")).scalar()
    assert total == res.nodes[0].amount and total > Decimal("0")
