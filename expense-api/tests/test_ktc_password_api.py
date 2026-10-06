"""K3: KTC PDF password is stored encrypted and never returned."""
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import secrets
from app.main import app


@pytest.fixture
def api(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr("app.secrets.get_engine", lambda: db)
    monkeypatch.setattr("app.routers.ktc.get_engine", lambda: db)
    with db.begin() as conn:
        conn.execute(text("DELETE FROM ledger.settings"))
    return TestClient(app)


def test_set_status_and_never_returned(api, db):
    assert api.get("/settings/ktc-password/status").json() == {"is_set": False, "updated_at": None}
    r = api.put("/settings/ktc-password", json={"password": "s3cret-dob"})
    assert r.status_code == 204 and r.content == b""
    status = api.get("/settings/ktc-password/status").json()
    assert status["is_set"] is True and status["updated_at"]
    assert "s3cret-dob" not in str(status)
    with db.connect() as conn:
        stored = conn.execute(text("SELECT value_encrypted FROM ledger.settings WHERE key = 'ktc_pdf_password'")).scalar()
    assert "s3cret-dob" not in stored
    assert secrets.get_setting("ktc_pdf_password") == "s3cret-dob"   # what the sync uses
    assert api.delete("/settings/ktc-password").status_code == 204
    assert api.get("/settings/ktc-password/status").json()["is_set"] is False


def test_missing_secret_key_is_500_without_leaking(api, monkeypatch):
    monkeypatch.delenv("SECRET_KEY")
    r = api.put("/settings/ktc-password", json={"password": "s3cret-dob"})
    assert r.status_code == 500 and "s3cret-dob" not in r.text


def test_empty_password_rejected(api):
    assert api.put("/settings/ktc-password", json={"password": ""}).status_code == 422
