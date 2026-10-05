import json
from decimal import Decimal
from pathlib import Path

import pytest

from sources import REGISTRY
from sources.base import ParseContext, RawEmail
from sources.kbank import parse

FIXTURES = Path(__file__).parent / "fixtures"
CTX = ParseContext(account_id=1, get_attachment=lambda a: b"", get_setting=lambda k: None)


def raw(msg: dict) -> RawEmail:
    return RawEmail(raw_email_id=1, gmail_message_id=msg["id"], subject=msg.get("subject", ""),
                    sender="", received_at=None, text_body=msg["text_body"], html_body="", attachments=[])


def fixture(name):
    p = FIXTURES / name
    if not p.exists():
        pytest.skip(f"{name} is gitignored")
    return json.loads(p.read_text(encoding="utf-8"))


def test_registry_has_implemented_sources():
    assert {"kbank_email", "ktc_pdf"} <= set(REGISTRY)


def test_bill_payment_becomes_one_expense_payment_node():
    res = parse(raw(fixture("kbank_bill_payment_1.json")), CTX)
    assert res.status == "parsed" and len(res.nodes) == 1
    n = res.nodes[0]
    assert (n.node_type, n.kind, n.amount) == ("payment", "expense", Decimal("700.00"))
    assert n.dedup_key == "kbank:016276105202BQR06892" and n.children == []
    assert n.raw["ktc_bill_payment"] is False


def test_fee_is_added_to_amount():
    body = fixture("kbank_funds_transfer_1.json")
    body["text_body"] = body["text_body"].replace("Fee (THB): 0.00", "Fee (THB): 5.00")
    assert parse(raw(body), CTX).nodes[0].amount == Decimal("65.00")


def test_ktc_bill_flagged():
    body = fixture("kbank_bill_payment_1.json")
    body["text_body"] = body["text_body"].replace(
        "MCP-PT Ban Pho 3 (พีที บ้านโพธิ์3)", "KRUNGTHAI CARD PUBLIC COMPANY LIMITED")
    assert parse(raw(body), CTX).nodes[0].raw["ktc_bill_payment"] is True


def test_topup_and_failure_are_skipped_not_failed():
    for subject in ("Top-up (Success)", "Bill Payment (Failure)"):
        body = f"Dear Customer\nSubject: Result of {subject}\n\tTransaction Number: X\n"
        assert parse(raw({"id": "m", "text_body": body}), CTX).status == "skipped"


def test_garbled_supported_email_is_failed():
    body = "Dear Customer\nSubject: Result of Payment (Success)\n"
    assert parse(raw({"id": "m", "text_body": body}), CTX).status == "failed"


def test_unrelated_email_skipped():
    assert parse(raw({"id": "m", "text_body": "hello"}), CTX).status == "skipped"
