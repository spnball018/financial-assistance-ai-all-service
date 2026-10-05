import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from parsers.kbank import BANGKOK, parse_kbank

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"fixture {name} is gitignored; fetch it from gmail-service first")
    return json.loads(path.read_text(encoding="utf-8"))


def test_bill_payment():
    t = parse_kbank(load("kbank_bill_payment_1.json"))
    assert t.channel == "bill_payment"
    assert t.txn_no == "016276105202BQR06892"
    assert t.amount == Decimal("700.00")
    assert t.fee == Decimal("0.00")
    assert t.occurred_at == datetime(2026, 10, 3, 10, 52, 2, tzinfo=BANGKOK)
    assert t.merchant_raw.startswith("MCP-PT Ban Pho 3")
    assert "ชื่อบัญชี" not in t.merchant_raw
    assert t.dedup_key == "kbank:016276105202BQR06892"


def test_funds_transfer_uses_thai_account_name():
    t = parse_kbank(load("kbank_funds_transfer_1.json"))
    assert t.channel == "transfer"
    assert t.amount == Decimal("60.00")
    assert t.to_account == "145-8-33894-5"
    assert t.merchant_raw.startswith("นาง")  # Thai name, not the transliterated one


def test_promptpay_uses_holder_name_not_wallet_brand():
    t = parse_kbank(load("kbank_promptpay_funds_transfer_1.json"))
    assert t.channel == "promptpay"
    assert t.amount == Decimal("500.00")
    assert t.merchant_raw != "KTB G-WALLET"


def test_donation():
    t = parse_kbank(load("kbank_donation_1.json"))
    assert t.channel == "donation"
    assert t.amount == Decimal("40.00")
    assert t.merchant_raw


def test_non_kbank_email_returns_none():
    assert parse_kbank({"id": "x", "text_body": "hello"}) is None


def test_all_fixtures_parse():
    files = sorted(FIXTURES.glob("kbank_*.json"))
    if not files:
        pytest.skip("no fixtures")
    for f in files:
        assert parse_kbank(json.loads(f.read_text(encoding="utf-8"))) is not None, f.name


PROMPTPAY_ID_EMAIL = """เรื่อง แจ้งผลการทำรายการโอนเงินพร้อมเพย์ (สำเร็จ)

	วันที่ทำรายการ: 05/10/2026  09:00:01
	เลขที่รายการ: 016278090001BPP00001
	โอนเงินจากบัญชี: xxx-x-x5204-x
	ให้รหัสพร้อมเพย์: 08x-xxx-1234
	ชื่อผู้รับเงิน: นาย ทดสอบ ระบบ
	จำนวนเงิน (บาท): 1,250.50
	ค่าธรรมเนียม (บาท): 0.00

Dear Customer of mobile number 08-XXXX-0000
Subject: Result of PromptPay Funds Transfer (Success)

	Transaction Date: 05/10/2026  09:00:01
	Transaction Number: 016278090001BPP00001
	From Account: xxx-x-x5204-x
	To PromptPay ID: 08x-xxx-1234
	Received Name: MR. TEST RABOB
	Amount (THB): 1,250.50
	Fee (THB): 0.00
"""


def test_promptpay_to_id_variant():
    t = parse_kbank({"id": "m1", "text_body": PROMPTPAY_ID_EMAIL})
    assert t.channel == "promptpay"
    assert t.merchant_raw == "นาย ทดสอบ ระบบ"
    assert t.to_account == "08x-xxx-1234"
    assert t.amount == Decimal("1250.50")


def test_failure_email_is_skipped():
    body = "Dear Customer\nSubject: Result of Bill Payment (Failure)\n\tTransaction Number: X\n"
    assert parse_kbank({"id": "m2", "text_body": body}) is None
