"""Users (admins only): add family members by their Google address, change role, deactivate.

Each user sees only their own data. Deactivating signs the user out and stops their API tokens;
their data stays. There is no delete.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import auth
from app.auth import Principal, require_admin

router = APIRouter(prefix="/users", tags=["users"])


class UserIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    role: Literal["admin", "member"] = "member"


class UserPatch(BaseModel):
    role: Optional[Literal["admin", "member"]] = None
    is_active: Optional[bool] = None


@router.get("")
def list_users(admin: Principal = Depends(require_admin)):
    return auth.list_users()


@router.post("", status_code=201)
def add_user(body: UserIn, admin: Principal = Depends(require_admin)):
    user = auth.add_user(body.email, body.role)
    if user is None:
        raise HTTPException(status_code=409, detail="This email already has a user")
    return user


@router.patch("/{user_id}")
def update_user(user_id: int, body: UserPatch, admin: Principal = Depends(require_admin)):
    if user_id == admin.user_id and (body.is_active is False or body.role == "member"):
        raise HTTPException(status_code=422, detail="You cannot deactivate or demote yourself")
    user = auth.update_user(user_id, body.role, body.is_active)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user
