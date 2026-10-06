"""T6.2: transfers to the user's own accounts and per-payee rules (category / transfer / ask)."""
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from categorize.ai import FakeClassifier
from categorize.service import categorize_pending

TZ = timezone.utc


def acct(db, name):
    with db.connect() as conn:
        return conn.execute(text("SELECT id FROM ledger.accounts WHERE name = :n"), {"n": name}).scalar()


def cat(db, name_en):
    with db.connect() as conn:
        return conn.execute(text("SELECT id FROM ledger.categories WHERE name_en = :n"), {"n": name_en}).scalar()


def q(db, sql, **p):
    with db.connect() as conn:
        return conn.execute(text(sql), p).scalar()


def add(db, key, merchant, *, to_account=None, channel="promptpay", amount=500):
    raw = {"channel": channel, "to_account": to_account}
    with db.begin() as conn:
        return conn.execute(text(
            "INSERT INTO ledger.transactions (user_id, account_id, node_type, kind, status, amount, occurred_at, source, "
            "dedup_key, merchant_raw, raw) VALUES (1, 1, 'payment', 'expense', 'pending', :a, :w, 'kbank_email', :k, :m, "
            "CAST(:raw AS jsonb)) RETURNING id"),
            {"a": amount, "w": datetime(2026, 9, 10, 3, 0, tzinfo=TZ), "k": key, "m": merchant,
             "raw": json.dumps(raw)}).scalar()


@pytest.fixture
def env(db, monkeypatch):
    with db.begin() as conn:
        conn.execute(text("TRUNCATE ledger.merchant_memory RESTART IDENTITY"))
        conn.execute(text("UPDATE ledger.accounts SET masked_ref = NULL"))
    monkeypatch.setattr("app.routers.transactions.get_engine", lambda: db)
    return db, TestClient(app)


def row(db, txn_id):
    with db.connect() as conn:
        return conn.execute(text("SELECT kind, counter_account_id, category_id, category_source "
                                 "FROM ledger.transactions WHERE id = :i"), {"i": txn_id}).one()


def test_destination_number_matches_own_account(env):
    db, _ = env
    wallet = acct(db, "เป๋าตัง (KTB G-Wallet)")
    with db.begin() as conn:
        conn.execute(text("UPDATE ledger.accounts SET masked_ref = 'x7722' WHERE id = :i"), {"i": wallet})
    t = add(db, "a", "สมมติ ใจดี", to_account="006-xxxxxxxx-7722")
    other = add(db, "b", "STARBUCKS", to_account=None, channel="bill_payment")
    ai = FakeClassifier({"STARBUCKS": (cat(db, "Cafe & Bakery"), 0.9)})
    res = categorize_pending(db, 1, ai)
    assert res["transfer"] == 1 and res["ai"] == 1
    assert tuple(row(db, t)) == ("transfer", wallet, None, "account")
    assert [i.merchant for i in ai.calls[0]] == ["STARBUCKS"]  # the transfer never goes to the AI
    assert row(db, other).kind == "expense"


def test_ambiguous_masked_ref_is_not_guessed(env):
    db, _ = env
    with db.begin() as conn:
        conn.execute(text("UPDATE ledger.accounts SET masked_ref = '7722' WHERE name IN ('SCB รายรับ', 'BAY ออมทรัพย์')"))
    t = add(db, "a", "X", to_account="123-4-57722-0".replace("-0", ""))
    categorize_pending(db, 1, None)
    assert row(db, t).kind == "expense"


def test_user_marks_transfer_then_confirm_learns_per_destination(env):
    db, api = env
    wallet, scb = acct(db, "เป๋าตัง (KTB G-Wallet)"), acct(db, "SCB รายรับ")
    a = add(db, "a", "นาย ทดสอบ ระบบ", to_account="006-xxxxxxxx-7722")
    b = add(db, "b", "นาย ทดสอบ ระบบ", to_account="006-xxxxxxxx-7722")  # same destination
    c = add(db, "c", "นาย ทดสอบ ระบบ", to_account="123-4-55555-6")     # same name, other account
    categorize_pending(db, 1, None)
    r = api.patch(f"/transactions/{a}", json={"transfer_account_id": wallet})
    assert r.status_code == 200 and r.json()["kind"] == "transfer" and r.json()["category_id"] is None
    res = api.post("/transactions/confirm", json={"ids": [a]}).json()
    assert res["remembered_transfers"] == 1 and res["applied_to_pending"] == 1
    assert tuple(row(db, b))[:2] == ("transfer", wallet)
    assert row(db, c).kind == "expense"  # not guessed: different destination
    assert q(db, "SELECT action FROM ledger.merchant_memory WHERE merchant_key = 'ทดสอบ ระบบ|7722'") == "transfer"
    # next sync: a new transfer to the same destination is recognised from the rule
    d = add(db, "d", "นาย ทดสอบ ระบบ", to_account="006-xxxxxxxx-7722")
    assert categorize_pending(db, 1, None)["transfer"] == 1 and row(db, d).category_source == "memory"
    # ...and the other destination still is not
    api.patch(f"/transactions/{c}", json={"transfer_account_id": scb})
    assert tuple(row(db, c))[:2] == ("transfer", scb)


def test_ask_payee_is_never_learned_or_applied(env):
    db, api = env
    food = cat(db, "Street Food")
    a = add(db, "a", "เพื่อน คนหนึ่ง", channel="transfer")
    b = add(db, "b", "เพื่อน คนหนึ่ง", channel="transfer")
    categorize_pending(db, 1, None)
    r = api.patch(f"/transactions/{a}", json={"remember": False, "category_id": food}).json()
    assert r["memory_action"] == "ask"
    res = api.post("/transactions/confirm", json={"ids": [a]}).json()
    assert res["remembered"] == 0 and res["applied_to_pending"] == 0
    assert row(db, b).category_id is None
    # AI still gets asked for "ask" payees
    e = add(db, "e", "เพื่อน คนหนึ่ง", channel="transfer")
    ai = FakeClassifier({"เพื่อน คนหนึ่ง": (food, 0.9)})
    categorize_pending(db, 1, ai)
    assert row(db, e).category_source == "ai"
    # lifting it
    assert api.patch(f"/transactions/{b}", json={"remember": True}).json()["memory_action"] is None


def test_transfer_back_to_expense_and_validation(env):
    db, api = env
    t = add(db, "a", "TrueMoney", channel="bill_payment")
    tm = acct(db, "TrueMoney")
    api.patch(f"/transactions/{t}", json={"transfer_account_id": tm})
    r = api.patch(f"/transactions/{t}", json={"category_id": cat(db, "Fuel")}).json()
    assert (r["kind"], r["counter_account_id"]) == ("expense", None)
    assert api.patch(f"/transactions/{t}", json={"transfer_account_id": 1}).status_code == 422  # own account
    assert api.patch(f"/transactions/{t}", json={"transfer_account_id": 99999}).status_code == 422
    assert api.patch(f"/transactions/{t}", json={"transfer_account_id": tm, "category_id": 3}).status_code == 422


def test_transfers_excluded_from_spending_but_listed_in_summary(env):
    db, api = env
    tm = acct(db, "TrueMoney")
    t = add(db, "a", "TrueMoney", channel="bill_payment", amount=300)
    s = add(db, "b", "SHOP", channel="bill_payment", amount=100)
    api.patch(f"/transactions/{t}", json={"transfer_account_id": tm})
    api.patch(f"/transactions/{s}", json={"category_id": cat(db, "Fuel")})
    api.post("/transactions/confirm", json={"ids": [t, s]})
    r = api.get("/reports/summary", params={"month": "2026-09"}).json()
    assert r["total"] == 100
    assert r["transfers_out"] == [{"account_id": tm, "name": "TrueMoney", "count": 1, "amount": 300}]
