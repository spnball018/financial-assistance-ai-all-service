"""Encrypted key/value settings in ledger.settings (e.g. "ktc_pdf_password").

Values are Fernet-encrypted with env SECRET_KEY. Never log or return decrypted values over HTTP.
"""
import os
from datetime import datetime
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from app.db import get_engine


def _fernet() -> Fernet:
    key = os.environ.get("SECRET_KEY")
    if not key:
        raise RuntimeError("SECRET_KEY is not set (see expense-api/.env.example)")
    return Fernet(key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        # Wrong SECRET_KEY or tampered value; do not include the token in the message.
        raise RuntimeError("stored setting cannot be decrypted with the current SECRET_KEY") from None


def set_setting(key: str, value: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(text(
            "INSERT INTO ledger.settings (key, value_encrypted, updated_at) VALUES (:k, :v, now()) "
            "ON CONFLICT (key) DO UPDATE SET value_encrypted = EXCLUDED.value_encrypted, updated_at = now()"),
            {"k": key, "v": encrypt(value)})


def get_setting(key: str) -> Optional[str]:
    with get_engine().connect() as conn:
        token = conn.execute(text("SELECT value_encrypted FROM ledger.settings WHERE key = :k"),
                             {"k": key}).scalar()
    return decrypt(token) if token is not None else None


def setting_updated_at(key: str) -> Optional[datetime]:
    """For status endpoints: tells whether a setting exists without revealing it."""
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT updated_at FROM ledger.settings WHERE key = :k"),
                            {"k": key}).scalar()
