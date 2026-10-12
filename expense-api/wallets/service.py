"""E-wallet history from app screenshots (TrueMoney, ...), handled like a card statement: the wallet is an
account with its own rows, and money moved into it from the bank is a transfer, not spending.

upload -> read (vision model proposes entries) -> apply (the user may edit the entries first):
- out (payment, transfer to a person): a pending expense on the wallet account
- in (refund, cashback, transfer from a person): a pending income on the wallet account
- top_up: no row of its own. It is matched to the bank row that paid it (payee looks like the wallet, same
  amount, within 10 minutes), and that row becomes a transfer bank -> wallet, as KTC bill payments do.
Overlapping screenshots are fine: a row's dedup_key is wallet + minute + amount + direction. With the
reading as is, rows whose date is not on screen (date_shown false) and empty rows are left out; an edited
list from the user is taken as it is.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import text

from ledger.transactions import Invalid, NotFound
from receipts import storage
from receipts.service import MAX_BYTES, MIME_TYPES, Conflict, NoReader
from wallets.ocr import WalletEntry, WalletReader

BKK = timezone(timedelta(hours=7))
TOPUP_WINDOW = timedelta(minutes=10)
KIND_LABEL = {"payment": "จ่ายเงิน", "transfer_out": "โอนเงินออก", "transfer_in": "รับเงินโอน", "refund": "คืนเงิน",
              "cashback": "เงินคืน", "other": "อื่นๆ", "top_up": "เติมเงิน"}
# how the bank names the wallet when it is topped up; other wallets fall back to their institution/name
TOPUP_PAYEE = {"truemoney": r"true ?money"}

_COLS = ("id, account_id, storage_uri, mime_type, sha256, uploaded_at, ocr_status, ocr_result, ocr_model, ocr_error, "
         "applied_at")


def _wallet(conn, uid: int, account_id: int):
    a = conn.execute(text("SELECT id, name, institution, type FROM ledger.accounts WHERE id = :a AND user_id = :uid"),
                     {"a": account_id, "uid": uid}).first()
    if a is None:
        raise NotFound(account_id)
    if a.type != "ewallet":
        raise Invalid("screenshots are for e-wallet accounts")
    return a


def get(conn, uid: int, shot_id: int) -> dict:
    r = conn.execute(text(f"SELECT {_COLS} FROM ledger.wallet_screenshots WHERE id = :id AND user_id = :uid"),
                     {"id": shot_id, "uid": uid}).first()
    if r is None:
        raise NotFound(shot_id)
    return dict(r._mapping)


def list_for_account(conn, uid: int, account_id: int) -> list[dict]:
    return [dict(r._mapping) for r in conn.execute(text(
        f"SELECT {_COLS} FROM ledger.wallet_screenshots WHERE user_id = :uid AND account_id = :a ORDER BY id DESC"),
        {"uid": uid, "a": account_id})]


def image(conn, uid: int, shot_id: int) -> tuple[bytes, str]:
    s = get(conn, uid, shot_id)
    try:
        return storage.load(s["storage_uri"]), s["mime_type"]
    except FileNotFoundError:
        raise NotFound(shot_id)


def upload(conn, uid: int, account_id: int, data: bytes, mime_type: str) -> dict:
    _wallet(conn, uid, account_id)
    if mime_type not in MIME_TYPES:
        raise Invalid(f"unsupported image type {mime_type}; use JPEG, PNG, WebP or HEIC")
    if not data or len(data) > MAX_BYTES:
        raise Invalid("image must be between 1 byte and 10 MB")
    digest = hashlib.sha256(data).hexdigest()
    existing = conn.execute(text("SELECT id FROM ledger.wallet_screenshots WHERE sha256 = :h AND user_id = :uid"),
                            {"h": digest, "uid": uid}).scalar()
    if existing:
        raise Conflict(f"this screenshot is already uploaded as {existing}")
    rel = storage.save(data, digest, mime_type, datetime.now(timezone.utc), uid)
    sid = conn.execute(text("INSERT INTO ledger.wallet_screenshots (user_id, account_id, storage_uri, mime_type, sha256) "
                            "VALUES (:uid, :a, :u, :m, :h) RETURNING id"),
                       {"uid": uid, "a": account_id, "u": rel, "m": mime_type, "h": digest}).scalar()
    return get(conn, uid, sid)


def read(engine, uid: int, shot_id: int, reader: Optional[WalletReader]) -> dict:
    """Run the vision model and store its proposal. Failures are stored (class name only)."""
    if reader is None:
        raise NoReader()
    with engine.connect() as conn:
        shot = get(conn, uid, shot_id)
        data, mime = image(conn, uid, shot_id)
        wallet = _wallet(conn, uid, shot["account_id"])
    try:
        reading = reader.read(data, mime, wallet.institution or wallet.name)
        with engine.begin() as conn:
            conn.execute(text("UPDATE ledger.wallet_screenshots SET ocr_status = 'done', ocr_result = CAST(:r AS jsonb), "
                              "ocr_model = :m, ocr_error = NULL WHERE id = :id"),
                         {"r": reading.model_dump_json(), "m": reader.model, "id": shot_id})
    except Exception as e:
        with engine.begin() as conn:
            conn.execute(text("UPDATE ledger.wallet_screenshots SET ocr_status = 'failed', ocr_error = :e, ocr_model = :m "
                              "WHERE id = :id"), {"e": type(e).__name__, "m": reader.model, "id": shot_id})
    with engine.connect() as conn:
        return get(conn, uid, shot_id)


def _when(s: str) -> datetime:
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=BKK)
    except ValueError:
        raise Invalid(f"time must be YYYY-MM-DD HH:MM, got {s!r}")


def _payee_pattern(wallet) -> str:
    key = re.sub(r"\s", "", (wallet.institution or wallet.name).lower())
    return TOPUP_PAYEE.get(key) or re.escape(wallet.institution or wallet.name)


def _match_top_up(conn, uid: int, wallet, amount: Decimal, when: datetime) -> tuple[str, Optional[int]]:
    """('linked'|'already'|'not_found'|'ambiguous'|'confirmed', bank row id)."""
    rows = conn.execute(text("""
        SELECT t.id, t.kind, t.status, t.counter_account_id, t.category_source FROM ledger.transactions t
        WHERE t.user_id = :uid AND t.parent_id IS NULL AND t.account_id <> :w AND t.amount = :a
          AND t.occurred_at BETWEEN :lo AND :hi AND t.merchant_raw ~* :payee
          AND t.kind IN ('expense', 'transfer')
          AND NOT EXISTS (SELECT 1 FROM ledger.transactions c WHERE c.parent_id = t.id)"""),
        {"uid": uid, "w": wallet.id, "a": amount, "lo": when - TOPUP_WINDOW, "hi": when + TOPUP_WINDOW,
         "payee": _payee_pattern(wallet)}).all()
    if not rows:
        return "not_found", None
    if len(rows) > 1:
        return "ambiguous", None
    r = rows[0]
    if r.kind == "transfer" and r.counter_account_id == wallet.id:
        return "already", r.id
    if r.status != "pending" or r.category_source == "user":
        return "confirmed", r.id  # the user decided this row; leave it
    # a category from AI or from a Merchant Memory rule is only a guess: the screenshot shows it was a top-up
    conn.execute(text("""
        UPDATE ledger.transactions SET kind = 'transfer', counter_account_id = :w, category_id = NULL,
          category_source = 'account', ai_confidence = NULL, ai_reason = NULL, updated_at = now()
        WHERE id = :id AND user_id = :uid"""), {"w": wallet.id, "id": r.id, "uid": uid})
    return "linked", r.id


def apply(conn, uid: int, shot_id: int, entries: Optional[list[dict]] = None) -> dict:
    """Turn the entries (the stored reading, or the user's edited list) into rows. Safe to repeat."""
    shot = get(conn, uid, shot_id)
    wallet = _wallet(conn, uid, shot["account_id"])
    if entries is None:
        if not shot["ocr_result"]:
            raise Invalid("screenshot has not been read yet")
        entries = [e for e in shot["ocr_result"]["entries"] if e.get("date_shown", True) and e.get("amount", 0) > 0]
    parsed = [WalletEntry.model_validate(e) for e in entries]
    out = {"new": 0, "duplicate": 0, "top_ups": []}
    for n, e in enumerate(parsed):
        amount = Decimal(str(e.amount)).quantize(Decimal("0.01"))
        if amount <= 0:
            raise Invalid(f"entry {n + 1}: amount must be positive")
        when = _when(e.occurred_at)
        if e.kind == "top_up" and e.direction == "in":
            status, bank_id = _match_top_up(conn, uid, wallet, amount, when)
            out["top_ups"].append({"occurred_at": when, "amount": amount, "status": status, "transaction_id": bank_id})
            continue
        new_id = conn.execute(text("""
            INSERT INTO ledger.transactions (user_id, account_id, node_type, kind, status, amount, occurred_at, has_time,
              source, source_ref, dedup_key, merchant_raw, description, raw)
            VALUES (:uid, :a, 'payment', :k, 'pending', :amt, :at, true, 'wallet_screenshot', :ref, :dk, :m, :d,
                    CAST(:raw AS jsonb))
            ON CONFLICT (user_id, dedup_key) DO NOTHING RETURNING id"""),
            {"uid": uid, "a": wallet.id, "k": "expense" if e.direction == "out" else "income", "amt": amount,
             "at": when, "ref": str(shot_id), "dk": f"wallet:{wallet.id}:{when:%Y%m%d%H%M}:{amount}:{e.direction}",
             "m": e.title[:200], "d": KIND_LABEL.get(e.kind, e.kind),
             "raw": json.dumps({"screenshot_id": shot_id, "wallet_kind": e.kind})}).scalar()
        out["new" if new_id else "duplicate"] += 1
    conn.execute(text("UPDATE ledger.wallet_screenshots SET applied_at = now() WHERE id = :id"), {"id": shot_id})
    return out
