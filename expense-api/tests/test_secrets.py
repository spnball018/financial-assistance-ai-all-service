import pytest
from cryptography.fernet import Fernet

from app import secrets


def test_roundtrip(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    token = secrets.encrypt("pdf-pass-123")
    assert "pdf-pass-123" not in token
    assert secrets.decrypt(token) == "pdf-pass-123"


def test_wrong_key_does_not_leak(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    token = secrets.encrypt("pdf-pass-123")
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    with pytest.raises(RuntimeError) as e:
        secrets.decrypt(token)
    assert token not in str(e.value)


def test_missing_key(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError):
        secrets.encrypt("x")
