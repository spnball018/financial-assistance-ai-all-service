"""P7.2: each user sees and changes only their own data. User 1 owns the seeded accounts; `kid` is a
member added by the admin, with nothing yet."""
from datetime import datetime, timezone

import pytest
import sqlalchemy.exc
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import auth
from app.auth import Principal, require_user
from app.main import app
from sources.base import RawEmail, TxnNode
from sync import repo

EXPENSE = {"kind": "expense", "amount": "120.00", "occurred_at": "2026-09-05T12:00:00+07:00",
           "has_time": True, "merchant": "ร้านข้าว"}


@pytest.fixture
def two(db, monkeypatch):
    monkeypatch.setattr("app.db._engine", db)
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    with db.begin() as conn:
        conn.execute(text("DELETE FROM ledger.merchant_memory"))
        conn.execute(text("DELETE FROM ledger.settings"))
    kid = auth.add_user("kid@example.com", "member")["id"]
    owner_api, kid_api = TestClient(app), TestClient(app)

    def as_user(uid):
        def call(method, url, **kw):
            app.dependency_overrides[require_user] = lambda: Principal(uid, f"u{uid}", "member", "session")
            return getattr(owner_api if uid == 1 else kid_api, method)(url, **kw)
        return call

    with db.connect() as conn:
        food = conn.execute(text("SELECT id FROM ledger.categories WHERE name_en = 'Street Food'")).scalar()
    return as_user(1), as_user(kid), kid, food


def test_new_member_starts_empty_with_switched_off_sources(two):
    owner, kid, _, _ = two
    assert kid("get", "/accounts").json() == []
    sources = kid("get", "/email-sources").json()
    assert {s["source"] for s in sources} >= {"kbank_email", "ktc_pdf"}
    assert all(not s["is_active"] and s["gmail_label"] is None and s["account_id"] is None for s in sources)
    assert len(owner("get", "/accounts").json()) >= 10


def test_transactions_reports_and_rules_are_private(two):
    owner, kid, _, food = two
    t = owner("post", "/transactions", json={**EXPENSE, "account_id": 1, "category_id": food}).json()

    assert kid("get", "/transactions").json() == {"total": 0, "items": []}
    assert kid("get", "/reports/summary?month=2026-09").json()["total"] == 0
    assert owner("get", "/reports/summary?month=2026-09").json()["total"] == 120
    assert kid("get", "/merchant-rules").json() == []
    assert owner("get", "/merchant-rules").json()[0]["merchant_key"] == "ร้านข้าว"

    # another user's ids look like they do not exist
    tid = t["id"]
    assert kid("get", f"/transactions/{tid}").status_code == 404
    assert kid("patch", f"/transactions/{tid}", json={"description": "x"}).status_code == 404
    assert kid("post", "/transactions/confirm", json={"ids": [tid]}).status_code == 404
    assert kid("get", f"/transactions/{tid}/receipts").status_code == 404
    assert kid("delete", f"/transactions/{tid}/receipt-items").status_code == 404
    assert kid("delete", "/merchant-rules?merchant_key=ร้านข้าว").status_code == 404
    assert owner("get", f"/transactions/{tid}").json()["description"] is None


def test_cannot_use_another_users_accounts(two):
    owner, kid, _, food = two
    # same name as the owner's account is fine: names are unique per user
    mine = kid("post", "/accounts", json={"name": "KBank ใช้จ่าย", "type": "bank"})
    assert mine.status_code == 201
    mine = mine.json()["id"]

    assert kid("post", "/transactions", json={**EXPENSE, "account_id": 1, "category_id": food}).status_code == 422
    assert kid("patch", "/accounts/1", json={"name": "stolen"}).status_code == 404
    assert kid("put", "/accounts/1/opening-balance", json={"amount": "1", "as_of": "2026-09-01"}).status_code == 404
    src = {"gmail_label": "x", "sender_filter": None, "account_id": 1, "is_active": True}
    assert kid("put", "/email-sources/kbank_email", json=src).status_code == 422

    t = kid("post", "/transactions", json={**EXPENSE, "account_id": mine, "category_id": food}).json()
    assert kid("patch", f"/transactions/{t['id']}", json={"transfer_account_id": 1}).status_code == 422
    assert [a["name"] for a in kid("get", "/accounts").json()] == ["KBank ใช้จ่าย"]
    assert [b["account_id"] for b in kid("get", "/accounts/balances").json()] == [mine]
    assert next(a for a in owner("get", "/accounts").json() if a["id"] == 1)["name"] == "KBank ใช้จ่าย"  # untouched


def test_settings_are_per_user(two):
    owner, kid, _, _ = two
    assert owner("put", "/settings/ktc-password", json={"password": "owner-dob"}).status_code == 204
    assert kid("get", "/settings/ktc-password/status").json()["is_set"] is False
    kid("delete", "/settings/ktc-password")
    assert owner("get", "/settings/ktc-password/status").json()["is_set"] is True


def test_only_the_gmail_service_owner_can_sync(two):
    _, kid, _, _ = two
    for path in ("/sync", "/sync/reparse?source=kbank_email"):
        r = kid("post", path)
        assert r.status_code == 409 and "Gmail" in r.json()["detail"]
    assert kid("get", "/sync/runs").json() == []


def test_same_email_and_dedup_key_can_exist_for_two_users(two, db):
    _, kid_call, kid, _ = two
    kid_account = kid_call("post", "/accounts", json={"name": "K", "type": "bank"}).json()["id"]
    node = TxnNode(node_type="payment", kind="expense", amount=10, occurred_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                   dedup_key="kbank:same", merchant_raw="X")
    with db.begin() as conn:
        for uid, account in ((1, 1), (kid, kid_account)):
            rid = repo.insert_raw_email(conn, uid, "kbank_email", None, {"id": "same-message", "subject": "s"})
            assert rid is not None
            email = RawEmail(rid, "same-message", "s", "", None, "", "", [])
            assert repo.insert_tree(conn, node, user_id=uid, account_id=account, source="kbank_email",
                                    raw_email=email) == (1, 0)


def test_database_refuses_links_across_users(two, db):
    _, _, kid, _ = two
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        with db.begin() as conn:   # kid's row on the owner's account
            conn.execute(text(
                "INSERT INTO ledger.transactions (user_id, account_id, node_type, kind, amount, occurred_at, source, "
                "dedup_key) VALUES (:k, 1, 'manual', 'expense', 1, now(), 'manual', 'x')"), {"k": kid})
