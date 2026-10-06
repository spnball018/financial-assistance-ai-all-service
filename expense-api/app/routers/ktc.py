"""KTC settings: the e-Statement PDF password (docs/ktc-track.md, K3).

The password is stored Fernet-encrypted by app.secrets and is never returned by any endpoint;
the UI can only learn whether it is set.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import secrets
from app.auth import current_user_id
from sources.ktc import PASSWORD_KEY

router = APIRouter(tags=["ktc"])


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


def _guard(fn):
    try:
        return fn()
    except RuntimeError:
        # SECRET_KEY missing or changed; never include the value or the stored token
        raise HTTPException(status_code=500, detail="SECRET_KEY is not set or cannot decrypt stored settings "
                                                    "(see expense-api/.env.example)")


@router.put("/settings/ktc-password", status_code=204)
def set_ktc_password(body: PasswordIn, uid: int = Depends(current_user_id)):
    _guard(lambda: secrets.set_setting(uid, PASSWORD_KEY, body.password))


@router.get("/settings/ktc-password/status")
def ktc_password_status(uid: int = Depends(current_user_id)):
    updated = secrets.setting_updated_at(uid, PASSWORD_KEY)
    return {"is_set": updated is not None, "updated_at": updated}


@router.delete("/settings/ktc-password", status_code=204)
def delete_ktc_password(uid: int = Depends(current_user_id)):
    secrets.delete_setting(uid, PASSWORD_KEY)
