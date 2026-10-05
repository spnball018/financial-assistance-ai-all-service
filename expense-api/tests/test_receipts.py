"""T6.5: receipt photo -> proposal -> split into receipt items (+ adjustment), undo."""
import base64
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.routers import receipts as receipts_router
from receipts.ocr import FakeReceiptReader, ReceiptAdjustment, ReceiptItem, ReceiptReading

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def cat(db, name_en):
    with db.connect() as conn:
        return conn.execute(text("SELECT id FROM ledger.categories WHERE name_en = :n"), {"n": name_en}).scalar()


def q(db, sql, **p):
    with db.connect() as conn:
        return conn.execute(text(sql), p).scalar()


@pytest.fixture
def env(db, monkeypatch):
    with db.begin() as conn:
        conn.execute(text("TRUNCATE ledger.receipts RESTART IDENTITY CASCADE"))
        pay = conn.execute(text(
            "INSERT INTO ledger.transactions (account_id, node_type, kind, status, amount, occurred_at, source, dedup_key, "
            "merchant_raw, category_id, category_source) VALUES (1, 'payment', 'expense', 'pending', 250, :w, "
            "'kbank_email', 'pay', 'BIG C', :c, 'ai') RETURNING id"),
            {"w": datetime(2026, 9, 12, 5, 0, tzinfo=timezone.utc), "c": cat(db, "Supermarket")}).scalar()
    reading = ReceiptReading(merchant="BIG C", total=250, items=[
        ReceiptItem(name="นมสด", quantity=2, amount=90, category_id=cat(db, "Supermarket"), confidence=0.9),
        ReceiptItem(name="น้ำยาล้างจาน", quantity=1, amount=65, category_id=cat(db, "Cleaning Products"), confidence=0.95),
        ReceiptItem(name="ปากกา", quantity=1, amount=120, category_id=cat(db, "Food & Dining"), confidence=0.5),  # parent: invalid
    ], adjustments=[ReceiptAdjustment(name="ส่วนลดสมาชิก", amount=-25)])
    reader = FakeReceiptReader(reading)
    monkeypatch.setattr("app.routers.receipts.get_engine", lambda: db)
    monkeypatch.setattr("app.routers.transactions.get_engine", lambda: db)
    app.dependency_overrides[receipts_router.get_reader] = lambda: reader
    yield db, TestClient(app), pay, reader
    app.dependency_overrides.clear()


def upload(api, pay, data=PNG, mime="image/png"):
    return api.post(f"/transactions/{pay}/receipts", files={"file": ("r.png", data, mime)})


def test_upload_reads_and_proposes(env):
    db, api, pay, reader = env
    r = upload(api, pay)
    assert r.status_code == 201
    rec = r.json()
    assert rec["ocr_status"] == "done" and reader.calls == 1 and rec["ocr_model"] == "fake"
    assert Decimal(str(rec["items_total"])) == Decimal("275") and Decimal(str(rec["difference"])) == Decimal("-25")
    img = api.get(f"/receipts/{rec['id']}/image")
    assert img.status_code == 200 and img.content == PNG and img.headers["content-type"] == "image/png"
    assert upload(api, pay).status_code == 409                      # same image twice
    assert upload(api, pay, b"x", "application/pdf").status_code == 422


def test_apply_creates_items_and_adjustment_that_add_up(env):
    db, api, pay, _ = env
    rec = upload(api, pay).json()
    r = api.post(f"/receipts/{rec['id']}/apply", json={}).json()
    assert r["items"] == 3 and Decimal(str(r["adjustment"])) == Decimal("-25")
    assert q(db, "SELECT sum(amount) FROM ledger.transactions WHERE parent_id = :p", p=pay) == Decimal("250")
    assert q(db, "SELECT count(*) FROM ledger.transactions WHERE parent_id = :p AND node_type = 'receipt_item'", p=pay) == 3
    # invalid category from the model falls back to Uncategorized
    assert q(db, "SELECT category_id FROM ledger.transactions WHERE parent_id = :p AND merchant_raw = 'ปากกา'", p=pay) \
        == cat(db, "Uncategorized")
    tree = api.get("/transactions", params={"status": "pending"}).json()["items"][0]
    assert tree["id"] == pay and len(tree["children"]) == 4 and tree["receipts"] == 1
    # cannot split twice; undo removes the children, then it can be applied again with edited items
    assert api.post(f"/receipts/{rec['id']}/apply", json={}).status_code == 422
    assert api.delete(f"/transactions/{pay}/receipt-items").json() == {"removed": 4}
    edited = {"items": [{"name": "ของใช้", "amount": 250, "category_id": cat(db, "General Supplies")}]}
    assert api.post(f"/receipts/{rec['id']}/apply", json=edited).json() == {"parent_id": pay, "items": 1, "adjustment": 0.0}
    assert q(db, "SELECT count(*) FROM ledger.transactions WHERE parent_id = :p", p=pay) == 1


def test_confirm_and_report_count_items_not_payment(env):
    db, api, pay, _ = env
    rec = upload(api, pay).json()
    api.post(f"/receipts/{rec['id']}/apply", json={})
    api.post("/transactions/confirm", json={"ids": [pay]})
    s = api.get("/reports/summary", params={"month": "2026-09"}).json()
    assert Decimal(str(s["total"])) == Decimal("250")
    names = {sub["name_en"] for g in s["categories"] for sub in g["subcategories"]} | {g["name_en"] for g in s["categories"]}
    assert {"Supermarket", "Cleaning Products", "Uncategorized"} <= names
    assert q(db, "SELECT count(*) FROM ledger.merchant_memory") == 0  # receipt lines are not payees


def test_no_reader_keeps_photo_and_says_how_to_enable(env):
    db, api, pay, _ = env
    app.dependency_overrides[receipts_router.get_reader] = lambda: None
    r = upload(api, pay)
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["detail"]
    assert q(db, "SELECT count(*) FROM ledger.receipts") == 1


def test_only_pending_unsplit_expense(env):
    db, api, pay, _ = env
    api.post("/transactions/confirm", json={"ids": [pay]})
    assert upload(api, pay).status_code == 422
