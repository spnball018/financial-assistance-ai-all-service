import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app


@pytest.fixture
def api(db, monkeypatch):
    monkeypatch.setattr("app.routers.accounts.get_engine", lambda: db)
    with db.begin() as conn:  # undo accounts other tests created
        conn.execute(text("DELETE FROM ledger.accounts WHERE name LIKE 'TEST %'"))
        conn.execute(text("UPDATE ledger.accounts SET masked_ref = NULL, is_active = true"))
    return TestClient(app)


def test_seeded_accounts_in_order(api):
    names = [a["name"] for a in api.get("/accounts").json()]
    assert names[:2] == ["KBank ใช้จ่าย", "บัตรเครดิต KTC"]
    for n in ("KBank ฉุกเฉิน", "SCB ค่าเช่า", "SCB รายรับ", "Bangkok Bank ลงทุน", "BAY ออมทรัพย์",
              "Lazada", "Shopee", "TrueMoney", "เป๋าตัง (KTB G-Wallet)", "Marketplace อื่นๆ"):
        assert n in names


def test_create_and_duplicate_name(api):
    r = api.post("/accounts", json={"name": "TEST กองทุน", "type": "investment", "masked_ref": "x1234"})
    assert r.status_code == 201 and r.json()["type"] == "investment" and r.json()["is_active"]
    assert api.post("/accounts", json={"name": "TEST กองทุน", "type": "bank"}).status_code == 409
    assert api.post("/accounts", json={"name": "TEST x", "type": "crypto"}).status_code == 422


def test_masked_ref_needs_4_digits(api):
    assert api.post("/accounts", json={"name": "TEST a", "type": "bank", "masked_ref": "x12"}).status_code == 422
    assert api.post("/accounts", json={"name": "TEST b", "type": "bank", "masked_ref": "006-xxxxxxxx-7722"}).status_code == 201


def test_patch_partial_deactivate_and_errors(api):
    acc = api.post("/accounts", json={"name": "TEST p", "type": "bank", "purpose": "เดิม"}).json()
    r = api.patch(f"/accounts/{acc['id']}", json={"masked_ref": "5204", "is_active": False}).json()
    assert (r["masked_ref"], r["is_active"], r["purpose"]) == ("5204", False, "เดิม")
    assert acc["id"] not in [a["id"] for a in api.get("/accounts", params={"include_inactive": False}).json()]
    assert api.patch(f"/accounts/{acc['id']}", json={"masked_ref": ""}).json()["masked_ref"] is None
    assert api.patch(f"/accounts/{acc['id']}", json={"name": None}).status_code == 422
    assert api.patch(f"/accounts/{acc['id']}", json={"name": "KBank ใช้จ่าย"}).status_code == 409
    assert api.patch("/accounts/99999", json={"purpose": "x"}).status_code == 404
