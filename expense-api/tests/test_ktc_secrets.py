import pytest
from cryptography.fernet import Fernet

import ktc_secrets


def test_roundtrip(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    token = ktc_secrets.encrypt("s3cret-ก")
    assert "s3cret" not in token
    assert ktc_secrets.decrypt(token) == "s3cret-ก"


def test_key_rotation(monkeypatch):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv("SECRET_KEY", old)
    token = ktc_secrets.encrypt("pw")
    monkeypatch.setenv("SECRET_KEY", f"{new},{old}")
    assert ktc_secrets.decrypt(token) == "pw"
    monkeypatch.setenv("SECRET_KEY", new)
    with pytest.raises(ktc_secrets.SecretKeyError):
        ktc_secrets.decrypt(token)


@pytest.mark.parametrize("value", ["", "not-a-key"])
def test_bad_key(monkeypatch, value):
    monkeypatch.setenv("SECRET_KEY", value)
    with pytest.raises(ktc_secrets.SecretKeyError):
        ktc_secrets.encrypt("pw")
