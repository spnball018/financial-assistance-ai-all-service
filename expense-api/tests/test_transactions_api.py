from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import db as app_db
from app.main import app

TZ = timezone.utc


@pytest.fixture
def api(db, monkeypatch):
    with db.begin() as conn:
        conn.execute(text("TRUNCATE ledger.merchant_memory RESTART IDENTITY"))
    for mod in ("app.routers.transactions",):
        monkeypatch.setattr(f"{mod}.get_engine", lambda: db)
    return TestClient(app)


def cat(db, name_en):
    with db.connect() as conn:
        return conn.execute(text("SELECT id FROM ledger.categories WHERE name_en = :n"), {"n": name_en}).scalar()


def add(db, key, merchant, amount="100", *, parent=None, node="payment", src="none", cat_id=None,
        when=datetime(2026, 9, 15, 5, 0, tzinfo=TZ), status="pending", kind="expense"):
    with db.begin() as conn:
        return conn.execute(text(
            "INSERT INTO ledger.transactions (account_id, parent_id, node_type, kind, counter_account_id, status, "
            "amount, occurred_at, source, dedup_key, merchant_raw, merchant_key, category_id, category_source) "
            "VALUES (1, :p, :n, :kind, CASE WHEN :kind = 'transfer' THEN 2 END, :st, :a, :w, 'kbank_email', :k, "
            ":m, lower(:m), :c, :s) RETURNING id"),
            {"p": parent, "n": node, "a": Decimal(amount), "w": when, "k": key, "m": merchant, "c": cat_id,
             "s": src, "st": status, "kind": kind}).scalar()


def q(db, sql, **p):
    with db.connect() as conn:
        return conn.execute(text(sql), p).scalar()


def test_list_returns_trees_and_filters(api, db):
    root = add(db, "r", "SHOPEE", "150", node="payment")
    add(db, "c1", "SHIRT", "100", parent=root, node="order_item")
    add(db, "c2", "SHIPPING", "50", parent=root, node="adjustment")
    add(db, "x", "OTHER", when=datetime(2026, 8, 1, tzinfo=TZ), status="confirmed")
    r = api.get("/transactions", params={"status": "pending"}).json()
    assert r["total"] == 1
    assert [c["merchant_raw"] for c in r["items"][0]["children"]] == ["SHIRT", "SHIPPING"]
    assert api.get("/transactions", params={"month": "2026-08"}).json()["total"] == 1
    assert api.get("/transactions", params={"month": "2026-8"}).status_code == 422


def test_patch_category_is_user_choice_and_rejects_parent_category(api, db):
    t = add(db, "a", "STARBUCKS", src="ai", cat_id=cat(db, "Fuel"))
    r = api.patch(f"/transactions/{t}", json={"category_id": cat(db, "Cafe & Bakery")})
    assert r.status_code == 200 and r.json()["category_source"] == "user"
    assert api.patch(f"/transactions/{t}", json={"category_id": cat(db, "Food & Dining")}).status_code == 422
    assert api.patch("/transactions/999999", json={"description": "x"}).status_code == 404


def test_patch_description_only_does_not_touch_category(api, db):
    fuel = cat(db, "Fuel")
    t = add(db, "a", "PTT", src="ai", cat_id=fuel)
    r = api.patch(f"/transactions/{t}", json={"description": "trip"}).json()
    assert (r["description"], r["category_id"], r["category_source"]) == ("trip", fuel, "ai")


def test_ignore_whole_tree_and_cannot_unconfirm(api, db):
    root = add(db, "r", "SHOPEE")
    child = add(db, "c", "ITEM", parent=root, node="order_item")
    assert api.patch(f"/transactions/{root}", json={"status": "ignored"}).status_code == 200
    assert q(db, "SELECT status FROM ledger.transactions WHERE id = :i", i=child) == "ignored"
    done = add(db, "d", "X", status="confirmed")
    assert api.patch(f"/transactions/{done}", json={"status": "ignored"}).status_code == 422
    assert api.patch(f"/transactions/{done}", json={"status": "confirmed"}).status_code == 422  # not allowed here


def test_confirm_teaches_memory_and_updates_same_merchant(api, db):
    cafe = cat(db, "Cafe & Bakery")
    a = add(db, "a", "STARBUCKS", src="user", cat_id=cafe)
    b = add(db, "b", "STARBUCKS", src="ai", cat_id=cat(db, "Fuel"))      # AI guessed wrong
    c = add(db, "c", "STARBUCKS", src="user", cat_id=cat(db, "Fuel"))    # user chose: keep
    r = api.post("/transactions/confirm", json={"ids": [a]}).json()
    assert r == {"confirmed": 1, "remembered": 1, "remembered_transfers": 0, "applied_to_pending": 1}
    assert q(db, "SELECT category_id FROM ledger.merchant_memory WHERE merchant_key='starbucks'") == cafe
    assert q(db, "SELECT category_source FROM ledger.transactions WHERE id=:i", i=b) == "memory"
    assert q(db, "SELECT category_id FROM ledger.transactions WHERE id=:i", i=c) == cat(db, "Fuel")


def test_confirm_child_confirms_whole_tree_and_fills_uncategorized(api, db):
    root = add(db, "r", "SHOPEE", "150")
    child = add(db, "c1", "SHIRT", "150", parent=root, node="order_item")
    r = api.post("/transactions/confirm", json={"ids": [child]}).json()
    assert r["confirmed"] == 2 and r["remembered"] == 0  # Uncategorized teaches nothing
    assert q(db, "SELECT count(*) FROM ledger.transactions WHERE status='confirmed'") == 2
    assert q(db, "SELECT category_id FROM ledger.transactions WHERE id=:i", i=child) == cat(db, "Uncategorized")
    assert q(db, "SELECT category_id FROM ledger.transactions WHERE id=:i", i=root) is None  # parent: never reported


def test_confirm_all_and_errors(api, db):
    add(db, "a", "A")
    add(db, "b", "B")
    assert api.post("/transactions/confirm", json={"all": True}).json()["confirmed"] == 2
    assert api.post("/transactions/confirm", json={}).status_code == 422
    assert api.post("/transactions/confirm", json={"ids": [987654]}).status_code == 404


def test_manual_entry_is_confirmed_and_learns(api, db):
    body = {"account_id": 1, "amount": "45.50", "occurred_at": "2026-09-20T12:30:00+07:00",
            "category_id": cat(db, "Street Food"), "merchant": "ร้านข้าวมันไก่ 2"}
    r = api.post("/transactions", json=body)
    assert r.status_code == 201
    t = r.json()
    assert (t["status"], t["category_source"], t["node_type"], t["merchant_key"]) == \
        ("confirmed", "manual", "manual", "ร้านข้าวมันไก่")
    assert q(db, "SELECT count(*) FROM ledger.merchant_memory") == 1
    assert api.post("/transactions", json={**body, "amount": "-1"}).status_code == 422
    assert api.post("/transactions", json={**body, "occurred_at": "2026-09-20T12:30:00"}).status_code == 422
    assert api.post("/transactions", json={**body, "account_id": 999}).status_code == 422


def test_summary_counts_only_confirmed_leaf_expenses(api, db):
    cafe, fuel = cat(db, "Cafe & Bakery"), cat(db, "Fuel")
    add(db, "a", "SB", "100", cat_id=cafe, status="confirmed")
    add(db, "b", "PTT", "700", cat_id=fuel, status="confirmed")
    root = add(db, "r", "SHOPEE", "300", status="confirmed")  # parent: not counted itself
    add(db, "c", "MUG", "300", parent=root, node="order_item", cat_id=cafe, status="confirmed")
    add(db, "p", "PENDING", "999", cat_id=cafe)                 # pending: not counted
    add(db, "t", "KTC BILL", "5000", kind="transfer", status="confirmed")
    # months follow Bangkok time: 2026-08-31 18:00 UTC is 2026-09-01 01:00 in Bangkok -> September
    add(db, "o", "MIDNIGHT", "50", cat_id=cafe, status="confirmed", when=datetime(2026, 8, 31, 18, 0, tzinfo=TZ))
    # ...while 2026-08-31 16:00 UTC is 23:00 in Bangkok -> August
    add(db, "e", "LATE AUG", "20", cat_id=cafe, status="confirmed", when=datetime(2026, 8, 31, 16, 0, tzinfo=TZ))
    r = api.get("/reports/summary", params={"month": "2026-09"}).json()
    assert Decimal(str(r["total"])) == Decimal("1150") and r["count"] == 4 and r["pending_not_counted"] == 1
    food = next(g for g in r["categories"] if g["name_en"] == "Food & Dining")
    assert Decimal(str(food["amount"])) == Decimal("450") and food["subcategories"][0]["count"] == 3
    aug = api.get("/reports/summary", params={"month": "2026-08"}).json()
    assert aug["count"] == 1 and Decimal(str(aug["total"])) == Decimal("20")


def test_manual_income_and_monthly_net(api, db):
    rent = cat(db, "Rental Income")
    body = {"account_id": 1, "amount": "8000", "occurred_at": "2026-09-01T10:00:00+07:00",
            "category_id": rent, "merchant": "ผู้เช่า", "kind": "income"}
    r = api.post("/transactions", json=body)
    assert r.status_code == 201 and r.json()["kind"] == "income"
    assert q(db, "SELECT count(*) FROM ledger.merchant_memory") == 0  # payers are not learned
    # an expense category on income (and vice versa) is rejected
    assert api.post("/transactions", json={**body, "category_id": cat(db, "Fuel")}).status_code == 422
    assert api.post("/transactions", json={**body, "kind": "expense"}).status_code == 422
    add(db, "e", "SHOP", "1200", cat_id=cat(db, "Fuel"), status="confirmed")
    s = api.get("/reports/summary", params={"month": "2026-09"}).json()
    assert Decimal(str(s["income"]["total"])) == Decimal("8000") and s["income"]["categories"][0]["name_en"] == "Rental Income"
    assert Decimal(str(s["net"])) == Decimal("6800")
    # re-categorizing an income keeps it income; it cannot become a transfer out
    t = r.json()["id"]
    assert api.patch(f"/transactions/{t}", json={"category_id": cat(db, "Freelance")}).json()["kind"] == "income"
    assert api.patch(f"/transactions/{t}", json={"transfer_account_id": 3}).status_code == 422
