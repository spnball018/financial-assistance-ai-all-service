"""T6.4: opening balances, postings view and balances (incl. the KBank reported balance)."""
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app

TZ = timezone.utc
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def api(db, monkeypatch):
    with db.begin() as conn:
        conn.execute(text("UPDATE ledger.email_sources SET account_id = 1 WHERE source = 'kbank_email'"))
    monkeypatch.setattr("app.routers.accounts.get_engine", lambda: db)
    return TestClient(app)


def acct(db, name):
    with db.connect() as conn:
        return conn.execute(text("SELECT id FROM ledger.accounts WHERE name = :n"), {"n": name}).scalar()


def add(db, key, amount, *, account=1, kind="expense", counter=None, status="confirmed", parent=None,
        when=datetime(2026, 9, 10, 3, 0, tzinfo=TZ)):
    with db.begin() as conn:
        return conn.execute(text(
            "INSERT INTO ledger.transactions (user_id, account_id, parent_id, node_type, kind, counter_account_id, status, "
            "amount, occurred_at, source, dedup_key) VALUES (1, :a, :p, 'payment', :k, :c, :s, :amt, :w, 'manual', :dk) "
            "RETURNING id"), {"a": account, "p": parent, "k": kind, "c": counter, "s": status,
                              "amt": Decimal(amount), "w": when, "dk": key}).scalar()


def bal(api, account_id):
    return next(b for b in api.get("/accounts/balances").json() if b["account_id"] == account_id)


def test_opening_plus_movements_and_transfer_nets_to_zero(api, db):
    wallet = acct(db, "TrueMoney")
    assert api.put("/accounts/1/opening-balance", json={"amount": "10000", "as_of": "2026-09-01"}).status_code == 200
    add(db, "e1", "700")                                   # expense out of KBank
    add(db, "t1", "1500", kind="transfer", counter=wallet)  # KBank -> TrueMoney
    add(db, "i1", "5000", kind="income")                    # income into KBank
    add(db, "p1", "999", status="pending")                  # pending: only in balance_with_pending
    add(db, "x1", "50", status="ignored")                   # ignored: nowhere
    add(db, "old", "123", when=datetime(2026, 8, 31, 16, 59, tzinfo=TZ))  # 23:59 Bangkok, before as_of
    k = bal(api, 1)
    assert Decimal(str(k["balance"])) == Decimal("12800")            # 10000 - 700 - 1500 + 5000
    assert Decimal(str(k["balance_with_pending"])) == Decimal("11801")
    assert k["pending_count"] == 1 and k["opening"]["as_of"] == "2026-09-01"
    w = bal(api, wallet)
    assert Decimal(str(w["balance"])) == Decimal("1500")


def test_children_do_not_double_count_the_account_leg(api, db):
    root = add(db, "r", "300")
    add(db, "c1", "200", parent=root)
    add(db, "c2", "100", parent=root)
    assert Decimal(str(bal(api, 1)["balance"])) == Decimal("-300")


def test_transfer_child_lands_on_counter_account(api, db):
    wallet = acct(db, "Shopee")
    root = add(db, "r", "500")
    add(db, "c1", "200", parent=root, kind="transfer", counter=wallet)  # part of the payment topped up a wallet
    add(db, "c2", "300", parent=root)
    assert Decimal(str(bal(api, 1)["balance"])) == Decimal("-500")
    assert Decimal(str(bal(api, wallet)["balance"])) == Decimal("200")


def test_opening_upsert_negative_and_delete(api, db):
    ktc = acct(db, "บัตรเครดิต KTC")
    api.put(f"/accounts/{ktc}/opening-balance", json={"amount": "-100", "as_of": "2026-09-01"})
    r = api.put(f"/accounts/{ktc}/opening-balance", json={"amount": "-2500.50", "as_of": "2026-09-05"}).json()
    assert Decimal(str(r["amount"])) == Decimal("-2500.50") and r["as_of"] == "2026-09-05"
    assert Decimal(str(bal(api, ktc)["balance"])) == Decimal("-2500.50")
    assert api.delete(f"/accounts/{ktc}/opening-balance").status_code == 204
    assert bal(api, ktc)["opening"] is None
    assert api.put("/accounts/99999/opening-balance", json={"amount": "1", "as_of": "2026-09-01"}).status_code == 404


def test_opening_rows_are_not_reported_as_spending(api, db):
    from ledger.reports import monthly_summary
    api.put("/accounts/1/opening-balance", json={"amount": "10000", "as_of": "2026-09-01"})
    with db.connect() as conn:
        assert monthly_summary(conn, 1, "2026-09")["total"] == 0


def test_kbank_reported_balance_vs_computed(api, db):
    p = FIXTURES / "kbank_bill_payment_1.json"
    if not p.exists():
        pytest.skip("fixture is gitignored")
    msg = json.loads(p.read_text(encoding="utf-8"))
    with db.begin() as conn:
        conn.execute(text(
            "INSERT INTO ledger.raw_emails (user_id, gmail_message_id, source, text_body, received_at, parse_status) "
            "VALUES (1, :m, 'kbank_email', :b, now(), 'parsed')"), {"m": msg["id"], "b": msg["text_body"]})
    # the fixture: paid 700.00 on 2026-10-03 10:52:02 (Bangkok), available balance 14,189.79
    api.put("/accounts/1/opening-balance", json={"amount": "15000", "as_of": "2026-10-01"})
    add(db, "pay", "700", when=datetime(2026, 10, 3, 3, 52, 2, tzinfo=TZ))
    rep = bal(api, 1)["bank_reported"]
    assert Decimal(str(rep["balance"])) == Decimal("14189.79")
    assert Decimal(str(rep["computed"])) == Decimal("14300")
    assert Decimal(str(rep["difference"])) == Decimal("-110.21")


def set_bank_balance(db, txn_id, amount):
    with db.begin() as conn:
        conn.execute(text("UPDATE ledger.transactions SET raw = jsonb_build_object('balance_after', CAST(:b AS text)) "
                          "WHERE id = :id"), {"b": amount, "id": txn_id})


def at(day, hour=3):
    return datetime(2026, 9, day, hour, 0, tzinfo=TZ)


def test_ledger_running_balance_matches_balances(api, db):
    wallet = acct(db, "TrueMoney")
    api.put("/accounts/1/opening-balance", json={"amount": "10000", "as_of": "2026-09-01"})
    add(db, "old", "123", when=datetime(2026, 8, 31, 16, 59, tzinfo=TZ))   # before the opening day
    add(db, "e1", "700", when=at(2))
    add(db, "t1", "1500", kind="transfer", counter=wallet, when=at(3))
    add(db, "x1", "50", status="ignored", when=at(4))
    add(db, "p1", "999", status="pending", when=at(5))
    add(db, "i1", "5000", kind="income", when=at(6))
    led = api.get("/accounts/1/ledger").json()
    rows = led["rows"]
    assert [r["kind"] for r in rows] == ["income", "expense", "expense", "transfer", "expense", "opening", "expense"]
    assert [r["counted"] for r in rows] == [True, True, False, True, True, True, False]
    newest = rows[0]
    k = bal(api, 1)
    assert Decimal(str(newest["balance"])) == Decimal(str(k["balance"])) == Decimal("12800")
    assert Decimal(str(newest["balance_with_pending"])) == Decimal(str(k["balance_with_pending"]))
    assert Decimal(str(rows[3]["amount"])) == Decimal("-1500") and rows[3]["other_account"] == "TrueMoney"
    # the ignored row does not move the running balance
    assert rows[2]["balance"] == rows[3]["balance"]
    # the counter side of the transfer shows up on the wallet as money in, from KBank
    w = api.get(f"/accounts/{wallet}/ledger").json()["rows"]
    assert len(w) == 1 and Decimal(str(w[0]["amount"])) == Decimal("1500") and w[0]["other_account"] == "KBank ใช้จ่าย"


def test_ledger_marks_where_bank_balance_starts_to_differ(api, db):
    api.put("/accounts/1/opening-balance", json={"amount": "1000", "as_of": "2026-09-01"})
    set_bank_balance(db, add(db, "a", "100", when=at(2)), "900")      # matches
    add(db, "in", "500", kind="income", when=at(3))                   # recorded, but the bank never got it
    set_bank_balance(db, add(db, "b", "100", when=at(4)), "800")      # bank 800, computed 1300
    set_bank_balance(db, add(db, "c", "100", when=at(5)), "700")      # same gap, not a new one
    rows = {r["transaction_id"]: r for r in api.get("/accounts/1/ledger").json()["rows"]}
    by_bank = sorted((r for r in rows.values() if r["bank_balance"] is not None), key=lambda r: r["occurred_at"])
    assert [Decimal(str(r["difference"])) for r in by_bank] == [0, -500, -500]
    assert [r["gap_starts"] for r in by_bank] == [False, True, False]


def test_ledger_unknown_account_is_404(api):
    assert api.get("/accounts/99999/ledger").status_code == 404
