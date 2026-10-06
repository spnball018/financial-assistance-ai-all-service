from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from app.auth import current_user_id
from app.db import get_engine
from ledger.transactions import Invalid, NotFound
from receipts import service
from receipts.ocr import ReceiptReader, reader_from_env

router = APIRouter(tags=["receipts"])


def get_reader() -> Optional[ReceiptReader]:
    return reader_from_env()


def _run(fn):
    try:
        return fn()
    except NotFound:
        raise HTTPException(status_code=404, detail="not found")
    except Invalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    except service.Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except service.NoReader:
        raise HTTPException(status_code=503, detail="receipt reading needs GEMINI_API_KEY (or Vertex) in expense-api/.env")


@router.post("/transactions/{txn_id}/receipts", status_code=201)
async def upload_receipt(txn_id: int, file: UploadFile = File(...), reader: Optional[ReceiptReader] = Depends(get_reader),
                         uid: int = Depends(current_user_id)):
    """Attach a receipt photo to a pending payment and read it. The response carries the proposal
    (ocr_result) and how far its items are from the amount paid (difference)."""
    data = await file.read(service.MAX_BYTES + 1)
    with get_engine().begin() as conn:
        rec = _run(lambda: service.upload(conn, uid, txn_id, data, file.content_type or ""))
    return _run(lambda: service.read(get_engine(), uid, rec["id"], reader))


@router.get("/transactions/{txn_id}/receipts")
def list_receipts(txn_id: int, uid: int = Depends(current_user_id)):
    with get_engine().connect() as conn:
        return _run(lambda: service.list_for_transaction(conn, uid, txn_id))


@router.get("/receipts/{receipt_id}")
def get_receipt(receipt_id: int, uid: int = Depends(current_user_id)):
    with get_engine().connect() as conn:
        return _run(lambda: service.get_receipt(conn, uid, receipt_id))


@router.get("/receipts/{receipt_id}/image")
def receipt_image(receipt_id: int, uid: int = Depends(current_user_id)):
    with get_engine().connect() as conn:
        data, mime = _run(lambda: service.image(conn, uid, receipt_id))
    return Response(content=data, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


@router.post("/receipts/{receipt_id}/read")
def reread_receipt(receipt_id: int, reader: Optional[ReceiptReader] = Depends(get_reader), uid: int = Depends(current_user_id)):
    return _run(lambda: service.read(get_engine(), uid, receipt_id, reader))


class ApplyItem(BaseModel):
    name: str = Field(min_length=1)
    amount: float = Field(gt=0)
    category_id: Optional[int] = None
    confidence: Optional[float] = Field(None, ge=0, le=1)  # set = the model's untouched suggestion


class ApplyRequest(BaseModel):
    items: Optional[list[ApplyItem]] = None


@router.post("/receipts/{receipt_id}/apply")
def apply_receipt(receipt_id: int, body: ApplyRequest, uid: int = Depends(current_user_id)):
    """Split the payment into the receipt's items (or the edited `items`), plus an adjustment line
    for any difference so the items add up to the amount paid."""
    with get_engine().begin() as conn:
        items = [i.model_dump() for i in body.items] if body.items is not None else None
        return _run(lambda: service.apply(conn, uid, receipt_id, items))


@router.delete("/transactions/{txn_id}/receipt-items")
def undo_receipt_split(txn_id: int, uid: int = Depends(current_user_id)):
    with get_engine().begin() as conn:
        return {"removed": _run(lambda: service.undo(conn, uid, txn_id))}
