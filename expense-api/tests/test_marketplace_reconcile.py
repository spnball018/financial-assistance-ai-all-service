from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import count

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from categorize.service import categorize_pending
from parsers.marketplace import BKK, Order, OrderLine
from reconcile import marketplace
from sync import engine as sync_engine, repo
from tests.test_marketplace_parsers import SHOPEE

AT = datetime(2026, 9, 4, 9, 15, 22, tzinfo=BKK)
_MSG = count()


def payment(db, merchant="SHOPEEPAY", amount="214", when=AT + timedelta(minutes=1), status="pending", key=None):
    with db.begin() as conn:
        return conn.execute(text(
            "INSERT INTO ledger.transactions (user_id, account_id, node_type, kind, status, amount, occurred_at, "
            "source, dedup_key, merchant_raw) VALUES (1, 1, 'payment', 'expense', :st, :a, :w, 'kbank_email', :k, :m) "
            "RETURNING id"),
            {"a": Decimal(amount), "w": when, "k": key or f"kbank:{merchant}:{amount}:{when}", "m": merchant,
             "st": status}).scalar()


def order(db, order_id, total, *, platform="shopee", at=AT, exact=True, seller="shop"):
    lines = [OrderLine("item", f"item of {order_id}", Decimal(total) + 10, 1),
             OrderLine("discount", "โค้ดส่วนลดของ Shopee", Decimal("-10"))]
    o = Order(platform, order_id, at, exact, Decimal(total), [seller], lines)
    with db.begin() as conn:
        raw_id = repo.insert_raw_email(conn, 1, f"{platform}_email", "x", {"id": f"m{next(_MSG)}", "html_body": ""})
        return repo.upsert_order(conn, 1, o, repo.load_raw_email(conn, 1, raw_id))


def link(db):
    with db.begin() as conn:
        return marketplace.link_orders(conn, 1)


def rows(db, sql, **p):
    with db.connect() as conn:
        return conn.execute(text(sql), p).all()


def test_order_is_stored_once_and_estimate_replaced_by_exact_time(db):
    assert order(db, "L1", "100", platform="lazada", exact=False, at=AT + timedelta(days=2)) == (1, 0)
    assert order(db, "L1", "100", platform="lazada", exact=True) == (0, 1)
    assert order(db, "L1", "100", platform="lazada", exact=False, at=AT + timedelta(days=5)) == (0, 1)
    [(ordered_at, exact, n_items)] = rows(db, """
        SELECT o.ordered_at, o.raw->>'ordered_at_exact', count(i.id) FROM ledger.ecommerce_orders o
        JOIN ledger.ecommerce_order_items i ON i.order_pk = o.id GROUP BY o.id""")
    assert ordered_at == AT and exact == "true" and n_items == 2


def test_one_checkout_two_orders_split_one_payment(db):
    pid = payment(db)
    order(db, "A", "160")
    order(db, "B", "54")
    assert link(db) == {"linked_orders": 2, "linked_payments": 1, "ambiguous": 0}
    tree = rows(db, """
        WITH RECURSIVE t AS (SELECT id, 0 AS d FROM ledger.transactions WHERE id = :p
          UNION ALL SELECT c.id, t.d + 1 FROM ledger.transactions c JOIN t ON c.parent_id = t.id)
        SELECT t.d, x.node_type, x.amount, x.status, x.needs_review FROM t JOIN ledger.transactions x ON x.id = t.id
        ORDER BY x.id""", p=pid)
    assert [(d, n, a) for d, n, a, _, _ in tree] == [
        (0, "payment", Decimal("214")),
        (1, "order", Decimal("160")), (2, "order_item", Decimal("170")), (2, "adjustment", Decimal("-10")),
        (1, "order", Decimal("54")), (2, "order_item", Decimal("64")), (2, "adjustment", Decimal("-10")),
    ]
    assert {s for *_, s, _ in tree} == {"pending"} and not any(r for *_, r in tree)
    assert rows(db, "SELECT count(*) FROM ledger.ecommerce_orders WHERE matched_txn_id = :p", p=pid) == [(2,)]
    assert link(db)["linked_orders"] == 0  # idempotent


def test_orders_paid_separately_fall_back_to_single_matches(db):
    a, b = payment(db, amount="160"), payment(db, amount="54")
    order(db, "A", "160")
    order(db, "B", "54")
    assert link(db)["linked_payments"] == 2
    assert rows(db, "SELECT order_id, matched_txn_id FROM ledger.ecommerce_orders ORDER BY order_id") == [("A", a), ("B", b)]


@pytest.mark.parametrize("merchant,amount,when,status", [
    ("Payment to Shopee", "215", AT, "pending"),                       # amount differs
    ("LAZADA", "214", AT, "pending"),                                  # other platform
    ("SHOPEEPAY", "214", AT - timedelta(minutes=10), "pending"),      # paid before the order
    ("SHOPEEPAY", "214", AT + timedelta(hours=25), "pending"),        # too late
    ("SHOPEEPAY", "214", AT, "confirmed"),                             # already confirmed
])
def test_no_match(db, merchant, amount, when, status):
    payment(db, merchant, amount, when, status)
    order(db, "A", "214")
    assert link(db)["linked_orders"] == 0


def test_two_candidate_payments_are_ambiguous(db):
    payment(db, "Payment to Shopee", "214", AT + timedelta(minutes=1))
    payment(db, "ชำระสินค้าช้อปปี้", "214", AT + timedelta(minutes=3))
    order(db, "A", "214")
    assert link(db) == {"linked_orders": 0, "linked_payments": 0, "ambiguous": 1}


def test_estimated_lazada_time_looks_back_two_weeks(db):
    pid = payment(db, "บริษัท บลูเพย์ จำกัด(บลูเพย์-ลาซาด้าเพย์)", "547", AT - timedelta(days=4))
    order(db, "L1", "547", platform="lazada", exact=False)
    assert link(db)["linked_orders"] == 1
    assert rows(db, "SELECT merchant_raw, description FROM ledger.transactions WHERE parent_id = :p", p=pid) == [
        ("Lazada · shop", "คำสั่งซื้อ #L1")]


def test_unlink_removes_children_and_is_not_proposed_again(db):
    pid = payment(db)
    order(db, "A", "214")
    link(db)
    with db.begin() as conn:
        assert marketplace.unlink(conn, 1, pid) == 1
    assert rows(db, "SELECT count(*) FROM ledger.transactions") == [(1,)]
    assert link(db)["linked_orders"] == 0
    other = payment(db, "Payment to Shopee", "214", AT + timedelta(minutes=2))
    assert link(db)["linked_orders"] == 1
    assert rows(db, "SELECT matched_txn_id FROM ledger.ecommerce_orders") == [(other,)]


def test_adjustments_take_the_largest_item_category_and_are_not_learned(db):
    pid = payment(db, amount="204")
    order(db, "A", "204")
    link(db)
    # the classifier only sees the item; the discount is left for adjustments_follow_items
    seen = []

    class Fake:
        def classify(self, items, cats):
            from categorize.ai import Prediction
            seen.extend(i.merchant for i in items)
            leaf = next(c.id for c in cats if not c.path.startswith("Uncategorized"))
            return [Prediction(id=i.id, category_id=leaf, confidence=0.99, reason="t") for i in items]

    categorize_pending(db, 1, Fake())
    assert seen == ["item of A"]
    with db.begin() as conn:
        assert marketplace.adjustments_follow_items(conn, 1) == 1
    [(item_cat, adj_cat, adj_key)] = rows(db, """
        SELECT i.category_id, a.category_id, a.merchant_key FROM ledger.transactions i
        JOIN ledger.transactions a ON a.parent_id = i.parent_id AND a.node_type = 'adjustment'
        WHERE i.node_type = 'order_item'""")
    assert item_cat == adj_cat and adj_key is None


def test_sync_engine_stores_orders_from_registered_source(db):
    class Mail:
        def list_message_ids(self, label, date_after, search, page_token):
            return (["s1"], None) if label == "marketplace-shopee" else ([], None)

        def get_message(self, mid):
            return {"id": mid, "subject": "shipped", "sender": "Shopee <info@mail.shopee.co.th>",
                    "internal_date": "1789000000000", "text_body": "", "html_body": SHOPEE, "attachments": []}

    with db.begin() as conn:
        conn.execute(text("UPDATE ledger.email_sources SET is_active = true, gmail_label = 'marketplace-shopee' "
                          "WHERE source = 'shopee_email'"))
    res = sync_engine.run_sync(db, 1, Mail())
    assert res.counts["shopee_email"]["new"] == 1
    assert rows(db, "SELECT order_id, total_amount FROM ledger.ecommerce_orders") == [("2607219JCGSV15", Decimal("117"))]
    assert rows(db, "SELECT count(*) FROM ledger.transactions") == [(0,)]


def test_unlink_endpoint(db, monkeypatch):
    monkeypatch.setattr("app.routers.reconcile.get_engine", lambda: db)
    client = TestClient(app)
    pid = payment(db)
    order(db, "A", "214")
    assert client.post("/reconcile").json()["orders"]["linked_orders"] == 1
    assert client.delete(f"/reconcile/orders/{pid}").json() == {"removed": 1}
    assert client.delete("/reconcile/orders/999999").status_code == 404
