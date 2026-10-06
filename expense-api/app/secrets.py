"""Encrypted per-user key/value settings in ledger.settings (e.g. "ktc_pdf_password").

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


def decrypt(token: str, ttl: Optional[int] = None) -> str:
    """ttl (seconds): also reject tokens older than this, e.g. the short-lived sign-in cookie."""
    try:
        return _fernet().decrypt(token.encode(), ttl=ttl).decode()
    except InvalidToken:
        # Wrong SECRET_KEY or tampered value; do not include the token in the message.
        raise RuntimeError("stored setting cannot be decrypted with the current SECRET_KEY") from None


def set_setting(user_id: int, key: str, value: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(text(
            "INSERT INTO ledger.settings (user_id, key, value_encrypted, updated_at) VALUES (:u, :k, :v, now()) "
            "ON CONFLICT (user_id, key) DO UPDATE SET value_encrypted = EXCLUDED.value_encrypted, updated_at = now()"),
            {"u": user_id, "k": key, "v": encrypt(value)})


def get_setting(user_id: int, key: str) -> Optional[str]:
    with get_engine().connect() as conn:
        token = conn.execute(text("SELECT value_encrypted FROM ledger.settings WHERE user_id = :u AND key = :k"),
                             {"u": user_id, "k": key}).scalar()
    return decrypt(token) if token is not None else None


def setting_updated_at(user_id: int, key: str) -> Optional[datetime]:
    """For status endpoints: tells whether a setting exists without revealing it."""
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT updated_at FROM ledger.settings WHERE user_id = :u AND key = :k"),
                            {"u": user_id, "k": key}).scalar()


def delete_setting(user_id: int, key: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM ledger.settings WHERE user_id = :u AND key = :k"), {"u": user_id, "k": key})
