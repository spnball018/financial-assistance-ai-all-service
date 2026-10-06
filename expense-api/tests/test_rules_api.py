import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from categorize import memory


@pytest.fixture
def api(db, monkeypatch):
    with db.begin() as conn:
        conn.execute(text("TRUNCATE ledger.merchant_memory RESTART IDENTITY"))
    monkeypatch.setattr("app.routers.rules.get_engine", lambda: db)
    return TestClient(app)


def test_list_and_delete_rules(api, db):
    with db.begin() as conn:
        fuel = conn.execute(text("SELECT id FROM ledger.categories WHERE name_en = 'Fuel'")).scalar()
        memory.remember(conn, 1, "ptt", fuel)
        memory.remember(conn, 1, "ptt", fuel)
        wallet = conn.execute(text("SELECT id FROM ledger.accounts WHERE name = 'TrueMoney'")).scalar()
        memory.remember(conn, 1, "ทดสอบ ระบบ", action="transfer", counter_account_id=wallet, to_account="006-xxxxxxxx-7722")
        memory.set_ask(conn, 1, "เพื่อน", True)
    rules = api.get("/merchant-rules").json()
    assert rules[0]["merchant_key"] == "ptt" and rules[0]["hit_count"] == 2 and rules[0]["category_name"] == "ค่าน้ำมัน"
    t = next(r for r in rules if r["action"] == "transfer")
    assert t["merchant_key"] == "ทดสอบ ระบบ|7722" and t["account_name"] == "TrueMoney"
    assert {r["action"] for r in rules} == {"category", "transfer", "ask"}
    assert api.delete("/merchant-rules", params={"merchant_key": "ทดสอบ ระบบ|7722"}).status_code == 204
    assert api.delete("/merchant-rules", params={"merchant_key": "nope"}).status_code == 404
    assert len(api.get("/merchant-rules").json()) == 2
