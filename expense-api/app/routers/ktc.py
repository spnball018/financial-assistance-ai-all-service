"""KTC endpoints (e.g. PUT /settings/ktc-password). Owned by the KTC track: docs/ktc-track.md"""
from fastapi import APIRouter

router = APIRouter(tags=["ktc"])
