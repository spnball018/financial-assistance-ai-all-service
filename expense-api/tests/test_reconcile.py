"""T2.2 (card payments) and T6.7 (transfer/income pairs)."""
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from reconcile.card_payments import link_card_payments

TZ = timezone.utc


def acct(db, name):
    with db.connect() as conn:
        return conn.execute(text("SELECT id FROM ledger.accounts WHERE name = :n"), {"n": name}).scalar()


def add(db, key, amount, *, account=1, kind="expense", counter=None, status="pending", src="ai", node="payment",
        when=datetime(2026, 9, 8, 5, 0, tzinfo=TZ), raw=None, merchant="นาย ทดสอบ ระบบ"):
    with db.begin() as conn:
        return conn.execute(text(
            "INSERT INTO ledger.transactions (account_id, node_type, kind, counter_account_id, status, amount, occurred_at, "
            "source, dedup_key, merchant_raw, category_source, raw) VALUES (:a, :n, :k, :c, :s, :amt, :w, 'kbank_email', "
            ":dk, :m, :cs, CAST(:raw AS jsonb)) RETURNING id"),
            {"a": account, "n": node, "k": kind, "c": counter, "s": status, "amt": amount, "w": when, "dk": key,
             "m": merchant, "cs": src, "raw": json.dumps(raw or {})}).scalar()


def row(db, i):
    with db.connect() as conn:
        return conn.execute(text("SELECT kind, counter_account_id, category_source, ref_transaction_id, status "
                                 "FROM ledger.transactions WHERE id = :i"), {"i": i}).one()


@pytest.fixture
def card(db):
    ktc = acct(db, "บัตรเครดิต KTC")
    with db.begin() as conn:
        conn.execute(text("UPDATE ledger.email_sources SET account_id = :a WHERE source = 'ktc_pdf'"), {"a": ktc})
    stmt = add(db, "stmt", 2012.21, account=ktc, node="statement", src="none", status="pending",
               when=datetime(2026, 9, 16, 17, 0, tzinfo=TZ),
               raw={"skipped_lines": [{"type": "payment", "posting_date": "2026-09-08", "amount": "-4000.00",
                                       "description": "Payment-KTB Internet"},
                                      {"type": "refund", "posting_date": "2026-09-02", "amount": "-900.00"}]})
    return ktc, stmt


def test_statement_payment_matches_one_transfer_out(db, card):
    ktc, stmt = card
    t = add(db, "t", 4000, raw={"channel": "transfer"})
    other_day = add(db, "x", 4000, when=datetime(2026, 9, 20, 5, 0, tzinfo=TZ))  # outside the window
    with db.begin() as conn:
        assert link_card_payments(conn)["matched_by_amount"] == 1
    assert tuple(row(db, t))[:4] == ("transfer", ktc, "account", stmt)
    assert row(db, other_day).kind == "expense"
    with db.begin() as conn:  # idempotent
        assert link_card_payments(conn)["matched_by_amount"] == 0


def test_ambiguous_or_user_decided_is_left_alone(db, card):
    a = add(db, "a", 4000)
    b = add(db, "b", 4000, when=datetime(2026, 9, 7, 5, 0, tzinfo=TZ))
    with db.begin() as conn:
        assert link_card_payments(conn) == {"bill_payments": 0, "matched_by_amount": 0, "ambiguous": 1}
    assert row(db, a).kind == row(db, b).kind == "expense"
    with db.begin() as conn:
        conn.execute(text("DELETE FROM ledger.transactions WHERE id = :b"), {"b": b})
        conn.execute(text("UPDATE ledger.transactions SET category_source = 'user' WHERE id = :a"), {"a": a})
        assert link_card_payments(conn)["matched_by_amount"] == 0


def test_direct_bill_payment_to_krungthai_card(db, card):
    ktc, stmt = card
    t = add(db, "bill", 4000, raw={"channel": "bill_payment", "ktc_bill_payment": True}, merchant="KRUNGTHAI CARD PUBLIC COMPANY LIMITED")
    with db.begin() as conn:
        assert link_card_payments(conn)["bill_payments"] == 1
    assert tuple(row(db, t))[:4] == ("transfer", ktc, "account", stmt)


def test_card_payment_moves_both_balances(db, card, monkeypatch):
    ktc, _ = card
    add(db, "t", 4000)
    with db.begin() as conn:
        link_card_payments(conn)
    monkeypatch.setattr("app.routers.accounts.get_engine", lambda: db)
    bals = {b["account_id"]: b for b in TestClient(app).get("/accounts/balances").json()}
    assert bals[ktc]["balance_with_pending"] == pytest.approx(4000 - 2012.21)
    assert bals[1]["balance_with_pending"] == pytest.approx(-4000)


def test_transfer_income_pair_suggested_and_linked(db, monkeypatch):
    scb = acct(db, "SCB รายรับ")
    tr = add(db, "tr", 5000, kind="transfer", counter=scb, status="confirmed", src="user")
    inc = add(db, "inc", 5000, account=scb, kind="income", status="confirmed", src="manual",
              when=datetime(2026, 9, 9, 2, 0, tzinfo=TZ), merchant="เงินเข้า")
    far = add(db, "far", 5000, account=scb, kind="income", status="confirmed", src="manual",
              when=datetime(2026, 9, 20, 2, 0, tzinfo=TZ))
    monkeypatch.setattr("app.routers.reconcile.get_engine", lambda: db)
    api = TestClient(app)
    s = api.get("/reconcile/transfer-matches").json()
    assert [(x["income_id"], x["transfer_id"]) for x in s] == [(inc, tr)]
    assert api.post("/reconcile/transfer-matches", json={"income_id": far, "transfer_id": tr}).status_code == 422
    assert api.post("/reconcile/transfer-matches", json={"income_id": inc, "transfer_id": tr}).status_code == 200
    assert tuple(row(db, inc))[3:] == (tr, "ignored")
    assert api.get("/reconcile/transfer-matches").json() == []
    assert api.post("/reconcile/transfer-matches", json={"income_id": 999999, "transfer_id": tr}).status_code == 404
