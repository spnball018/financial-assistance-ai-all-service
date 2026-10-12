from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel

from app.auth import current_user_id
from app.db import get_engine
from categorize.ai import classifier_from_env
from categorize.service import categorize_pending
from ledger.transactions import Invalid, NotFound
from receipts.service import MAX_BYTES, Conflict, NoReader
from wallets import service
from wallets.ocr import WalletEntry, WalletReader, reader_from_env

router = APIRouter(tags=["wallets"])


def get_reader() -> Optional[WalletReader]:
    return reader_from_env()


def _run(fn):
    try:
        return fn()
    except NotFound:
        raise HTTPException(status_code=404, detail="not found")
    except Invalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except NoReader:
        raise HTTPException(status_code=503, detail="reading screenshots needs GEMINI_API_KEY (or Vertex) in expense-api/.env")


@router.post("/wallets/{account_id}/screenshots", status_code=201)
async def upload_screenshot(account_id: int, file: UploadFile = File(...),
                            reader: Optional[WalletReader] = Depends(get_reader), uid: int = Depends(current_user_id)):
    """Upload a screenshot of the wallet app's history and read it. The response carries the proposed
    entries (ocr_result.entries); nothing is recorded until POST /wallet-screenshots/{id}/apply."""
    data = await file.read(MAX_BYTES + 1)
    with get_engine().begin() as conn:
        shot = _run(lambda: service.upload(conn, uid, account_id, data, file.content_type or ""))
    return _run(lambda: service.read(get_engine(), uid, shot["id"], reader))


@router.get("/wallets/{account_id}/screenshots")
def list_screenshots(account_id: int, uid: int = Depends(current_user_id)):
    with get_engine().connect() as conn:
        return service.list_for_account(conn, uid, account_id)


@router.get("/wallet-screenshots/{shot_id}")
def get_screenshot(shot_id: int, uid: int = Depends(current_user_id)):
    with get_engine().connect() as conn:
        return _run(lambda: service.get(conn, uid, shot_id))


@router.get("/wallet-screenshots/{shot_id}/image")
def screenshot_image(shot_id: int, uid: int = Depends(current_user_id)):
    with get_engine().connect() as conn:
        data, mime = _run(lambda: service.image(conn, uid, shot_id))
    return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})


@router.post("/wallet-screenshots/{shot_id}/read")
def reread_screenshot(shot_id: int, reader: Optional[WalletReader] = Depends(get_reader),
                      uid: int = Depends(current_user_id)):
    return _run(lambda: service.read(get_engine(), uid, shot_id, reader))


class ApplyRequest(BaseModel):
    entries: Optional[list[WalletEntry]] = None  # the user's edited list; omit to use the reading as is


@router.post("/wallet-screenshots/{shot_id}/apply")
def apply_screenshot(shot_id: int, body: ApplyRequest, uid: int = Depends(current_user_id)):
    """Record the entries as pending rows on the wallet and turn matching bank top-ups into transfers."""
    entries = [e.model_dump() for e in body.entries] if body.entries is not None else None
    with get_engine().begin() as conn:
        result = _run(lambda: service.apply(conn, uid, shot_id, entries))
    try:
        result["categorize"] = categorize_pending(get_engine(), uid, classifier_from_env())
    except Exception as e:  # rows are saved; categorizing can be re-run via POST /categorize
        result["categorize"] = {"error": type(e).__name__}
    return result
