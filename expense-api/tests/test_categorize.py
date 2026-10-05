from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from categorize import memory
from categorize.ai import FakeClassifier, GeminiClassifier, Item, Category, Prediction
from categorize.service import categorize_pending

TZ = timezone.utc


def add(db, key, merchant, *, parent=None, status="pending", source_cat="none", kind="expense"):
    with db.begin() as conn:
        return conn.execute(text(
            "INSERT INTO ledger.transactions (account_id, parent_id, node_type, kind, counter_account_id, status, "
            "amount, occurred_at, source, dedup_key, merchant_raw, category_source, raw) "
            "VALUES (1, :p, 'payment', :kind, CASE WHEN :kind = 'transfer' THEN 2 END, :st, 100, :at, "
            "'kbank_email', :k, :m, :cs, '{\"channel\": \"bill_payment\"}') RETURNING id"),
            {"p": parent, "st": status, "at": datetime(2026, 10, 1, tzinfo=TZ), "k": key, "m": merchant,
             "cs": source_cat, "kind": kind}).scalar()


def cat_id(db, name_en):
    with db.connect() as conn:
        return conn.execute(text("SELECT id FROM ledger.categories WHERE name_en = :n"), {"n": name_en}).scalar()


def row(db, txn_id):
    with db.connect() as conn:
        return conn.execute(text("SELECT category_id, category_source, ai_confidence, merchant_key "
                                 "FROM ledger.transactions WHERE id = :i"), {"i": txn_id}).one()


@pytest.fixture
def cdb(db):
    with db.begin() as conn:
        conn.execute(text("TRUNCATE ledger.merchant_memory RESTART IDENTITY"))
    return db


def test_memory_hit_wins_and_ai_is_not_called(cdb):
    fuel = cat_id(cdb, "Fuel")
    with cdb.begin() as conn:
        memory.remember(conn, "mcp pt ban pho", fuel)
    t = add(cdb, "k1", "MCP-PT Ban Pho 3 (พีที บ้านโพธิ์3)")
    ai = FakeClassifier({})
    res = categorize_pending(cdb, ai)
    assert res["memory"] == 1 and ai.calls == []
    r = row(cdb, t)
    assert (r.category_id, r.category_source, r.merchant_key) == (fuel, "memory", "mcp pt ban pho")


def test_ai_above_threshold_sets_category(cdb, monkeypatch):
    monkeypatch.setenv("AI_CONFIDENCE_THRESHOLD", "0.7")
    cafe = cat_id(cdb, "Cafe & Bakery")
    t = add(cdb, "k1", "STARBUCKS")
    res = categorize_pending(cdb, FakeClassifier({"STARBUCKS": (cafe, 0.93)}))
    assert res["ai"] == 1
    r = row(cdb, t)
    assert (r.category_id, r.category_source, float(r.ai_confidence)) == (cafe, "ai", 0.93)


def test_low_confidence_and_invalid_category_become_uncategorized(cdb):
    unc = cat_id(cdb, "Uncategorized")
    food_parent = cat_id(cdb, "Food & Dining")  # has children -> not a valid answer
    a = add(cdb, "k1", "SOMEONE")
    b = add(cdb, "k2", "ODD SHOP")
    res = categorize_pending(cdb, FakeClassifier({"SOMEONE": (cat_id(cdb, "Fuel"), 0.4),
                                                  "ODD SHOP": (food_parent, 0.99)}))
    assert res["uncategorized"] == 2
    assert row(cdb, a).category_id == unc and row(cdb, a).category_source == "ai"
    assert row(cdb, b).category_id == unc


def test_without_ai_rows_stay_none_for_later(cdb):
    t = add(cdb, "k1", "STARBUCKS")
    res = categorize_pending(cdb, None)
    assert res == {"memory": 0, "ai": 0, "uncategorized": 0, "left": 1}
    assert row(cdb, t).category_source == "none" and row(cdb, t).merchant_key == "starbucks"


def test_skips_parents_confirmed_already_categorized_and_transfers(cdb):
    parent = add(cdb, "p", "SHOPEE")
    add(cdb, "c", "ITEM", parent=parent)
    add(cdb, "done", "X", status="confirmed")
    add(cdb, "user", "Y", source_cat="user")
    add(cdb, "tr", "KTC", kind="transfer")
    ai = FakeClassifier({"ITEM": (cat_id(cdb, "Fashion"), 0.9)})
    categorize_pending(cdb, ai)
    assert [i.merchant for i in ai.calls[0]] == ["ITEM"]
    assert row(cdb, parent).category_source == "none"


def test_ai_failure_is_reported_and_rows_left(cdb):
    class Boom:
        def classify(self, items, categories):
            raise RuntimeError("secret prompt content")

    add(cdb, "k1", "A")
    res = categorize_pending(cdb, Boom())
    assert res["left"] == 1 and res["error"] == "ai: RuntimeError"


def test_remember_updates_and_counts(cdb):
    a, b = cat_id(cdb, "Fuel"), cat_id(cdb, "Tolls")
    with cdb.begin() as conn:
        memory.remember(conn, "k", a)
        memory.remember(conn, "k", b)
        r = conn.execute(text("SELECT category_id, hit_count FROM ledger.merchant_memory WHERE merchant_key='k'")).one()
    assert (r.category_id, r.hit_count) == (b, 2)


def test_gemini_adapter_builds_request_and_parses():
    seen = {}

    class Resp:
        parsed = [Prediction(id=1, category_id=5, confidence=0.8, reason="r")]

    class Models:
        def generate_content(self, model, contents, config):
            seen.update(model=model, contents=contents, config=config)
            return Resp()

    class Client:
        models = Models()

    out = GeminiClassifier(model="m-test", client=Client()).classify(
        [Item(id=1, merchant="STARBUCKS", channel="bill_payment", amount=Decimal("120.00"))],
        [Category(5, "Food & Dining > Cafe & Bakery / อาหาร > คาเฟ่")])
    assert out[0].category_id == 5 and seen["model"] == "m-test"
    assert "STARBUCKS" in seen["contents"] and "5: Food & Dining > Cafe" in seen["contents"]
    assert seen["config"].response_mime_type == "application/json"
