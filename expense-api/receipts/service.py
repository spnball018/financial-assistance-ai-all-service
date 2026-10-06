"""Attach a receipt photo to a payment, read it, and split the payment into receipt items (T6.5).

The payment stays the parent (it is what left the bank account); the items become its children, so
reports count the items and the balance still moves once. An adjustment line absorbs any difference
between the items and the amount paid, so the children always add up to the parent.
"""
import hashlib
from decimal import Decimal
from typing import Optional

from sqlalchemy import text

from datetime import datetime, timezone

from categorize.service import _leaf_categories
from ledger.transactions import Invalid, NotFound, check_category, get_one
from receipts import storage
from receipts.ocr import ReceiptReader

MAX_BYTES = 10 * 1024 * 1024
MIME_TYPES = set(storage.EXT)


class Conflict(Exception):
    pass


class NoReader(Exception):
    """No vision model configured (needs GEMINI_API_KEY or Vertex)."""


def _splittable(conn, txn_id: int) -> dict:
    t = get_one(conn, txn_id)
    if t["kind"] != "expense":
        raise Invalid("only an expense can be split by a receipt")
    if t["status"] != "pending":
        raise Invalid("split a receipt before confirming the payment")
    if conn.execute(text("SELECT EXISTS (SELECT 1 FROM ledger.transactions WHERE parent_id = :id)"), {"id": txn_id}).scalar():
        raise Invalid("this payment is already split; undo that first")
    return t


def _view(row, payment_amount: Optional[Decimal]) -> dict:
    d = dict(row._mapping)
    reading = d.get("ocr_result") or {}
    items_total = sum(Decimal(str(i["amount"])) for i in reading.get("items", []))
    adj_total = sum(Decimal(str(a["amount"])) for a in reading.get("adjustments", []))
    d["payment_amount"] = payment_amount
    d["items_total"] = items_total
    # what an "adjustment" line will carry so the children add up to the payment
    d["difference"] = (payment_amount - items_total) if payment_amount is not None else None
    d["adjustments_total"] = adj_total
    return d


_COLS = ("id, transaction_id, storage_uri, mime_type, sha256, uploaded_at, ocr_status, ocr_result, ocr_model, "
         "ocr_error, applied_at")


def get_receipt(conn, receipt_id: int) -> dict:
    row = conn.execute(text(f"SELECT {_COLS} FROM ledger.receipts WHERE id = :id"), {"id": receipt_id}).first()
    if row is None:
        raise NotFound(receipt_id)
    amount = None
    if row.transaction_id:
        amount = conn.execute(text("SELECT amount FROM ledger.transactions WHERE id = :t"),
                              {"t": row.transaction_id}).scalar()
    return _view(row, amount)


def list_for_transaction(conn, txn_id: int) -> list[dict]:
    get_one(conn, txn_id)
    rows = conn.execute(text(f"SELECT {_COLS} FROM ledger.receipts WHERE transaction_id = :t ORDER BY id"), {"t": txn_id})
    return [get_receipt(conn, r.id) for r in rows]


def image(conn, receipt_id: int) -> tuple[bytes, str]:
    row = conn.execute(text("SELECT storage_uri, mime_type FROM ledger.receipts WHERE id = :id"),
                       {"id": receipt_id}).first()
    if row is None:
        raise NotFound(receipt_id)
    try:
        return storage.load(row.storage_uri), row.mime_type
    except FileNotFoundError:
        raise NotFound(receipt_id)  # the file was moved or deleted from RECEIPTS_DIR


def upload(conn, txn_id: int, data: bytes, mime_type: str) -> dict:
    """Write the photo to RECEIPTS_DIR and record its path (OCR runs separately, outside this DB transaction)."""
    _splittable(conn, txn_id)
    if mime_type not in MIME_TYPES:
        raise Invalid(f"unsupported image type {mime_type}; use JPEG, PNG, WebP or HEIC")
    if not data or len(data) > MAX_BYTES:
        raise Invalid("image must be between 1 byte and 10 MB")
    digest = hashlib.sha256(data).hexdigest()
    existing = conn.execute(text("SELECT id, transaction_id FROM ledger.receipts WHERE sha256 = :h"), {"h": digest}).first()
    if existing:
        raise Conflict(f"this image is already uploaded as receipt {existing.id}")
    # the file is named by its hash, so a write left behind by a failed insert is harmless
    rel = storage.save(data, digest, mime_type, datetime.now(timezone.utc))
    rid = conn.execute(text("INSERT INTO ledger.receipts (transaction_id, storage_uri, mime_type, sha256) "
                            "VALUES (:t, :u, :m, :h) RETURNING id"),
                       {"t": txn_id, "u": rel, "m": mime_type, "h": digest}).scalar()
    return get_receipt(conn, rid)


def read(engine, receipt_id: int, reader: Optional[ReceiptReader]) -> dict:
    """Run OCR and store the proposal. Failures are stored on the receipt (class name only)."""
    if reader is None:
        raise NoReader()
    with engine.connect() as conn:
        data, mime = image(conn, receipt_id)
        cats, _ = _leaf_categories(conn)
    try:
        reading = reader.read(data, mime, cats)
        with engine.begin() as conn:
            conn.execute(text("UPDATE ledger.receipts SET ocr_status = 'done', ocr_result = CAST(:r AS jsonb), "
                              "ocr_model = :m, ocr_error = NULL WHERE id = :id"),
                         {"r": reading.model_dump_json(), "m": reader.model, "id": receipt_id})
    except Exception as e:
        with engine.begin() as conn:
            conn.execute(text("UPDATE ledger.receipts SET ocr_status = 'failed', ocr_error = :e, ocr_model = :m "
                              "WHERE id = :id"), {"e": type(e).__name__, "m": reader.model, "id": receipt_id})
    with engine.connect() as conn:
        return get_receipt(conn, receipt_id)


def apply(conn, receipt_id: int, items: Optional[list[dict]] = None) -> dict:
    """Create the children. `items` (name, amount, category_id) overrides the OCR proposal, e.g. after
    the user fixed a line. Returns the payment with its new children."""
    rec = conn.execute(text("SELECT transaction_id, ocr_result FROM ledger.receipts WHERE id = :id"),
                       {"id": receipt_id}).first()
    if rec is None:
        raise NotFound(receipt_id)
    if rec.transaction_id is None:
        raise Invalid("receipt is not attached to a payment")
    parent = _splittable(conn, rec.transaction_id)
    if items is None:
        if not rec.ocr_result:
            raise Invalid("receipt has not been read yet")
        items = [{"name": i["name"], "amount": i["amount"], "category_id": i["category_id"],
                  "confidence": i.get("confidence")} for i in rec.ocr_result["items"]]
    if not items:
        raise Invalid("no items to split into")
    _, unc = _leaf_categories(conn)
    total = Decimal("0")
    for n, it in enumerate(items):
        amount = Decimal(str(it["amount"])).quantize(Decimal("0.01"))
        if amount <= 0:
            raise Invalid(f"item {n + 1}: amount must be positive")
        cat = it.get("category_id") or unc
        try:
            check_category(conn, cat)
        except Invalid:
            cat = unc  # the model picked something that is not a leaf expense category
        total += amount
        conn.execute(text("""
            INSERT INTO ledger.transactions (account_id, parent_id, node_type, kind, status, amount, occurred_at,
              has_time, source, dedup_key, merchant_raw, description, category_id, category_source, ai_confidence,
              receipt_id)
            VALUES (:a, :p, 'receipt_item', 'expense', 'pending', :amt, :at, :ht, :src, :dk, :name, :desc, :c,
                    :cs, :conf, :r)"""),
            {"a": parent["account_id"], "p": parent["id"], "amt": amount, "at": parent["occurred_at"],
             "ht": parent["has_time"], "src": parent["source"], "dk": f"receipt:{receipt_id}:{n}",
             "name": it["name"][:200], "desc": parent["merchant_raw"], "c": cat,
             "cs": "ai" if it.get("confidence") is not None else "user",
             "conf": round(float(it["confidence"]), 3) if it.get("confidence") is not None else None, "r": receipt_id})
    diff = parent["amount"] - total
    if diff != 0:
        # discounts, points, rounding, VAT shown separately, or an unread line: keep children == parent
        conn.execute(text("""
            INSERT INTO ledger.transactions (account_id, parent_id, node_type, kind, status, amount, occurred_at,
              has_time, source, dedup_key, merchant_raw, description, category_id, category_source, receipt_id)
            VALUES (:a, :p, 'adjustment', 'expense', 'pending', :amt, :at, :ht, :src, :dk,
                    'ส่วนต่างจากใบเสร็จ', :desc, :c, 'user', :r)"""),
            {"a": parent["account_id"], "p": parent["id"], "amt": diff, "at": parent["occurred_at"],
             "ht": parent["has_time"], "src": parent["source"], "dk": f"receipt:{receipt_id}:adj",
             "desc": "ส่วนลด/ปัดเศษ/รายการที่อ่านไม่ได้", "c": parent["category_id"] or unc, "r": receipt_id})
    conn.execute(text("UPDATE ledger.receipts SET applied_at = now() WHERE id = :id"), {"id": receipt_id})
    return {"parent_id": parent["id"], "items": len(items), "adjustment": diff}


def undo(conn, txn_id: int) -> int:
    """Remove the receipt children of a pending payment (the photos stay)."""
    t = get_one(conn, txn_id)
    if t["status"] != "pending":
        raise Invalid("only a pending payment can be un-split")
    n = conn.execute(text("DELETE FROM ledger.transactions WHERE parent_id = :p AND receipt_id IS NOT NULL"),
                     {"p": txn_id}).rowcount
    conn.execute(text("UPDATE ledger.receipts SET applied_at = NULL WHERE transaction_id = :p"), {"p": txn_id})
    return n
