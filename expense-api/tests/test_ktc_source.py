"""KTC source tests. The statement service is faked with JSON shaped like POST /parse-ktc-pdf (no PDF needed)."""
import copy
import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from sources import REGISTRY, ktc
from sources.base import ParseContext, RawAttachment, RawEmail

PDF = RawAttachment(attachment_id="att1", filename="statement.PDF", mime_type="application/octet-stream", size=10)

RESPONSE = {
    "statement": {"statement_date": "2026-09-18", "due_date": "2026-10-03", "card_masked": "1234-56XX-XXXX-7890",
                  "card_last4": "7890", "previous_balance": "1000.00", "total_new_balance": "1300.50",
                  "minimum_payment": "100.00"},
    "lines": [
        {"transaction_date": "2026-09-01", "posting_date": "2026-09-02", "description": "COFFEE", "amount": "120.50",
         "approval_code": None, "type": "purchase"},
        {"transaction_date": None, "posting_date": "2026-09-05", "description": "INTEREST", "amount": "15.25",
         "approval_code": None, "type": "interest"},
        {"transaction_date": "2026-09-08", "posting_date": "2026-09-08", "description": "VAT ON FEE", "amount": "1.07",
         "approval_code": "123456", "type": "fee"},
        {"transaction_date": "2026-09-12", "posting_date": "2026-09-12", "description": "PAYMENT", "amount": "-1000.00",
         "approval_code": None, "type": "payment"},
        {"transaction_date": "2026-09-14", "posting_date": "2026-09-15", "description": "SHOP REFUND", "amount": "-99.00",
         "approval_code": None, "type": "refund"},
    ],
}


def email(attachments=(PDF,)):
    return RawEmail(raw_email_id=1, gmail_message_id="m1", subject="e-Statement", sender="ktc", received_at=None,
                    text_body="", html_body="", attachments=list(attachments))


def ctx(password="pw", content=b"%PDF-fake"):
    return ParseContext(account_id=1, get_attachment=lambda a: content,
                        get_setting=lambda k: password if k == "ktc_pdf_password" else None)


class FakeService(list):
    """Records calls to the patched httpx.post; respond() sets the next reply."""

    def __init__(self, monkeypatch):
        super().__init__()
        self._mp = monkeypatch
        self.respond()

    def respond(self, status=200, body=None):
        def post(url, **kw):
            self.append((url, kw))
            return httpx.Response(status, json=RESPONSE if body is None else body)
        self._mp.setattr(ktc.httpx, "post", post)


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setenv("CREDIT_CARD_STATEMENT_SERVICE_URL", "http://svc:8000")
    return FakeService(monkeypatch)


def test_registered():
    assert REGISTRY["ktc_pdf"] is ktc and ktc.SOURCE == "ktc_pdf"


def test_tree_shape(service):
    res = ktc.parse(email(), ctx())
    assert res.status == "parsed" and len(res.nodes) == 1
    root = res.nodes[0]
    assert (root.node_type, root.kind, root.has_time) == ("statement", "expense", False)
    assert root.dedup_key == "ktc:stmt:7890:2026-09-18"
    assert root.occurred_at.isoformat() == "2026-09-18T00:00:00+07:00"
    assert [c.merchant_raw for c in root.children] == ["COFFEE", "INTEREST", "VAT ON FEE"]
    assert root.amount == sum(c.amount for c in root.children) == Decimal("136.82")
    assert all(c.node_type == "statement_line" and c.kind == "expense" and not c.has_time for c in root.children)
    # transaction date wins over posting date when present
    assert root.children[0].occurred_at.date() == date(2026, 9, 1)
    assert root.children[1].occurred_at.date() == date(2026, 9, 5)
    assert root.children[2].source_ref == "123456"


def test_payment_and_refund_go_to_skipped_lines(service):
    root = ktc.parse(email(), ctx()).nodes[0]
    assert [s["type"] for s in root.raw["skipped_lines"]] == ["payment", "refund"]
    assert root.raw["due_date"] == "2026-10-03" and root.raw["total_new_balance"] == "1300.50"
    json.dumps(root.raw)  # JSON-safe
    json.dumps(root.children[0].raw)


def test_service_called_with_password_and_attachment(service):
    ktc.parse(email(), ctx(password="s3cret", content=b"%PDF-bytes"))
    url, kw = service[0]
    assert url == "http://svc:8000/parse-ktc-pdf"
    assert kw["data"] == {"password": "s3cret"} and kw["files"]["file"][1] == b"%PDF-bytes"


def test_dedup_keys_stable_and_unique_per_line(service):
    a = ktc.parse(email(), ctx()).nodes[0]
    b = ktc.parse(email(), ctx()).nodes[0]
    keys = [c.dedup_key for c in a.children]
    assert keys == [c.dedup_key for c in b.children] and len(set(keys)) == 3
    assert all(k.startswith("ktc:line:") and len(k) == len("ktc:line:") + 64 for k in keys)


def test_identical_lines_get_sequence_numbers(service):
    body = copy.deepcopy(RESPONSE)
    body["lines"] = [body["lines"][0], dict(body["lines"][0]), dict(body["lines"][0])]
    service.respond(200, body)
    root = ktc.parse(email(), ctx()).nodes[0]
    assert len(root.children) == 3 and len({c.dedup_key for c in root.children}) == 3
    assert root.amount == Decimal("361.50")


def test_no_expense_lines_is_skipped(service):
    body = copy.deepcopy(RESPONSE)
    body["lines"] = body["lines"][3:]
    service.respond(200, body)
    res = ktc.parse(email(), ctx())
    assert res.status == "skipped" and res.nodes == []


def test_no_pdf_is_skipped(service):
    txt = RawAttachment("a", "promo.png", "image/png", 5)
    assert ktc.parse(email([txt]), ctx()).status == "skipped"
    assert ktc.parse(email([]), ctx(password=None)).status == "skipped"  # ads need no password
    assert not service


def test_missing_password_fails_without_calling_service(service):
    res = ktc.parse(email(), ctx(password=None))
    assert res.status == "failed" and "รหัสผ่าน KTC" in res.error and not service


@pytest.mark.parametrize("status,body,expected", [
    (401, {"detail": {"code": "wrong_password"}}, "rejected"),
    (422, {"detail": {"code": "balance_check_failed"}}, "balance_check_failed"),
    (422, {"oops": 1}, "unknown"),
    (500, {}, "HTTP 500"),
])
def test_service_errors_fail_without_secrets(service, status, body, expected):
    service.respond(status, body)
    res = ktc.parse(email(), ctx(password="s3cret"))
    assert res.status == "failed" and expected in res.error and "s3cret" not in res.error


def test_unreachable_service_fails(monkeypatch):
    monkeypatch.setenv("CREDIT_CARD_STATEMENT_SERVICE_URL", "http://svc:8000")

    def boom(*a, **k):
        raise httpx.ConnectError("secret detail")
    monkeypatch.setattr(ktc.httpx, "post", boom)
    res = ktc.parse(email(), ctx())
    assert res.status == "failed" and "ConnectError" in res.error and "secret detail" not in res.error


def test_missing_service_url_fails(monkeypatch):
    monkeypatch.delenv("CREDIT_CARD_STATEMENT_SERVICE_URL", raising=False)
    assert ktc.parse(email(), ctx()).status == "failed"
