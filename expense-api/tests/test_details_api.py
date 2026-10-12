from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from sync import repo
from tests.test_transactions_api import add, cat


@pytest.fixture
def api(db, monkeypatch):
    monkeypatch.setattr("app.routers.transactions.get_engine", lambda: db)
    return TestClient(app)


def with_email(db, txn_id, html="", body=""):
    with db.begin() as conn:
        rid = repo.insert_raw_email(conn, 1, "kbank_email", "x", {"id": f"m{txn_id}", "subject": "K PLUS",
                                                                  "html_body": html, "text_body": body})
        conn.execute(text("UPDATE ledger.transactions SET raw_email_id = :r, raw = '{\"to_account\": \"xxx-1234\"}' "
                          "WHERE id = :t"), {"r": rid, "t": txn_id})


def test_details_shows_parser_fields_ancestors_and_inherited_email(api, db):
    root = add(db, "r", "SHOPEEPAY", "150")
    child = add(db, "c", "item", "150", parent=root, node="order_item")
    with_email(db, root, body="Paid <b>150</b>")
    d = api.get(f"/transactions/{child}/details").json()
    assert d["account_name"] and [a["id"] for a in d["ancestors"]] == [root]
    assert d["email"]["subject"] == "K PLUS" and d["email"]["has_html"] is False
    assert api.get(f"/transactions/{root}/details").json()["raw"] == {"to_account": "xxx-1234"}

    res = api.get(f"/transactions/{child}/email")
    assert "sandbox" in res.headers["content-security-policy"]
    assert res.text.startswith("<pre") and "&lt;b&gt;150&lt;/b&gt;" in res.text  # plain text is escaped


def test_email_404_without_source_and_for_other_users(api, db):
    manual = add(db, "m", "cash", "10")
    assert api.get(f"/transactions/{manual}/email").status_code == 404
    assert api.get("/transactions/999999/details").status_code == 404


def test_report_expenses_lists_a_category_with_its_subcategories(api, db):
    food = cat(db, "Food & Dining")
    with db.connect() as conn:
        sub = conn.execute(text("SELECT id FROM ledger.categories WHERE parent_id = :p ORDER BY id LIMIT 1"),
                           {"p": food}).scalar()
    when = datetime(2026, 9, 15, 5, 0, tzinfo=timezone.utc)
    root = add(db, "r", "SHOPEEPAY", "300", status="confirmed", cat_id=sub, when=when)
    add(db, "a", "noodles", "120", parent=root, node="order_item", status="confirmed", cat_id=sub, src="user", when=when)
    add(db, "b", "rice", "180", parent=root, node="order_item", status="confirmed", cat_id=sub, src="user", when=when)
    add(db, "p", "pending one", "50", cat_id=sub, when=when)
    rows = api.get(f"/reports/expenses?month=2026-09&category_id={food}").json()
    assert [(r["merchant_raw"], Decimal(str(r["amount"])), r["path"]) for r in rows] == [
        ("rice", Decimal("180"), ["SHOPEEPAY"]), ("noodles", Decimal("120"), ["SHOPEEPAY"])]
    assert api.get(f"/reports/expenses?month=2026-08&category_id={food}").json() == []
    assert api.get(f"/reports/expenses?month=bad&category_id={food}").status_code == 422
