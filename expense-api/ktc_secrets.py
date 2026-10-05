"""Encrypt/decrypt and persist the KTC PDF password. Owned by the KTC track: docs/ktc-track.md

The password is only ever stored Fernet-encrypted in ledger.settings (key `ktc_pdf_password`).
Nothing here logs, returns or prints the plaintext except `get_password`, which the sync engine's
`ctx.get_setting` is expected to call.

SECRET_KEY may hold several comma-separated Fernet keys: the first encrypts, all of them decrypt,
so a key can be rotated by prepending a new one and re-saving the password.
"""
import os
from datetime import datetime
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import text
from sqlalchemy.engine import Engine

SETTING_KEY = "ktc_pdf_password"


class SecretKeyError(RuntimeError):
    """SECRET_KEY missing/invalid, or the stored value cannot be decrypted with it."""


def _fernet() -> MultiFernet:
    raw = os.environ.get("SECRET_KEY", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise SecretKeyError("SECRET_KEY is not set")
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except ValueError:
        raise SecretKeyError("SECRET_KEY is not a valid Fernet key") from None


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        raise SecretKeyError("stored value cannot be decrypted with SECRET_KEY") from None


def set_password(engine: Engine, password: str) -> None:
    """Upsert the encrypted password."""
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO ledger.settings (key, value_encrypted, updated_at)
                VALUES (:k, :v, now())
                ON CONFLICT (key) DO UPDATE
                SET value_encrypted = EXCLUDED.value_encrypted, updated_at = now()"""),
            {"k": SETTING_KEY, "v": encrypt(password)},
        )


def get_password(engine: Engine) -> Optional[str]:
    with engine.connect() as conn:
        token = conn.execute(
            text("SELECT value_encrypted FROM ledger.settings WHERE key = :k"), {"k": SETTING_KEY}
        ).scalar()
    return decrypt(token) if token else None


def password_status(engine: Engine) -> dict:
    """Never touches the ciphertext's plaintext; safe to expose via the API."""
    with engine.connect() as conn:
        updated_at: Optional[datetime] = conn.execute(
            text("SELECT updated_at FROM ledger.settings WHERE key = :k"), {"k": SETTING_KEY}
        ).scalar()
    return {"is_set": updated_at is not None, "updated_at": updated_at}
