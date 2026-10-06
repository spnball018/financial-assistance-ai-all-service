"""All SQL used by the sync engine. Functions take an open SQLAlchemy connection and the user id;
every query is limited to that user's rows."""
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

from sources.base import RawAttachment, RawEmail, TxnNode

STALE_RUN_MINUTES = 30


def _clean(s: Optional[str]) -> Optional[str]:
    return s.replace("\x00", "") if s else s  # Postgres text rejects NUL


# ── sync_runs ──────────────────────────────────────────────────────────────

def has_running(conn, uid: int) -> bool:
    # a crashed process leaves 'running' behind; treat old ones as dead so sync is not blocked forever
    conn.execute(text(
        "UPDATE ledger.sync_runs SET status = 'failed', finished_at = now(), "
        "error = 'abandoned (process stopped)' "
        "WHERE user_id = :u AND status = 'running' AND started_at < now() - make_interval(mins => :m)"),
        {"u": uid, "m": STALE_RUN_MINUTES})
    return conn.execute(text("SELECT EXISTS (SELECT 1 FROM ledger.sync_runs WHERE user_id = :u AND status = 'running')"),
                        {"u": uid}).scalar()


def last_success_range_to(conn, uid: int) -> Optional[datetime]:
    return conn.execute(text(
        "SELECT range_to FROM ledger.sync_runs WHERE user_id = :u AND status = 'success' ORDER BY id DESC LIMIT 1"),
        {"u": uid}).scalar()


def start_run(conn, uid: int, range_from: datetime, range_to: datetime) -> int:
    return conn.execute(text(
        "INSERT INTO ledger.sync_runs (user_id, range_from, range_to) VALUES (:u, :f, :t) RETURNING id"),
        {"u": uid, "f": range_from, "t": range_to}).scalar()


def finish_run(conn, run_id: int, status: str, counts: dict, error: Optional[str] = None) -> None:
    conn.execute(text(
        "UPDATE ledger.sync_runs SET status = :s, finished_at = now(), counts = CAST(:c AS jsonb), error = :e "
        "WHERE id = :id"), {"s": status, "c": json.dumps(counts), "e": error, "id": run_id})


def list_runs(conn, uid: int, limit: int = 20) -> list[dict]:
    rows = conn.execute(text(
        "SELECT id, started_at, finished_at, status, range_from, range_to, counts, error "
        "FROM ledger.sync_runs WHERE user_id = :u ORDER BY id DESC LIMIT :n"), {"u": uid, "n": limit})
    return [dict(r._mapping) for r in rows]


# ── sources / raw emails ───────────────────────────────────────────────────

def active_sources(conn, uid: int) -> list[dict]:
    rows = conn.execute(text(
        "SELECT source, gmail_label, sender_filter, account_id FROM ledger.email_sources "
        "WHERE user_id = :u AND is_active ORDER BY id"), {"u": uid})
    return [dict(r._mapping) for r in rows]


def source_account(conn, uid: int, source: str) -> Optional[int]:
    return conn.execute(text("SELECT account_id FROM ledger.email_sources WHERE user_id = :u AND source = :s"),
                        {"u": uid, "s": source}).scalar()


def has_raw_emails(conn, uid: int, source: str) -> bool:
    return bool(conn.execute(text("SELECT EXISTS (SELECT 1 FROM ledger.raw_emails WHERE user_id = :u AND source = :s)"),
                             {"u": uid, "s": source}).scalar())


def known_message_ids(conn, uid: int, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    rows = conn.execute(text("SELECT gmail_message_id FROM ledger.raw_emails "
                             "WHERE user_id = :u AND gmail_message_id = ANY(:ids)"), {"u": uid, "ids": ids})
    return {r[0] for r in rows}


def insert_raw_email(conn, uid: int, source: str, label: Optional[str], msg: dict) -> Optional[int]:
    """Store a gmail-service message. Returns the new id, or None if it was already stored."""
    received = None
    if msg.get("internal_date"):
        received = datetime.fromtimestamp(int(msg["internal_date"]) / 1000, tz=timezone.utc)
    return conn.execute(text(
        "INSERT INTO ledger.raw_emails (user_id, gmail_message_id, source, gmail_label, subject, sender, received_at, "
        "text_body, html_body, attachments) "
        "VALUES (:u, :mid, :src, :label, :subject, :sender, :received, :text, :html, CAST(:att AS jsonb)) "
        "ON CONFLICT (user_id, gmail_message_id) DO NOTHING RETURNING id"),
        {"u": uid, "mid": msg["id"], "src": source, "label": label, "subject": _clean(msg.get("subject")),
         "sender": _clean(msg.get("sender")), "received": received, "text": _clean(msg.get("text_body")),
         "html": _clean(msg.get("html_body")), "att": json.dumps(msg.get("attachments", []))}).scalar()


def parseable_raw_email_ids(conn, uid: int, source: str, everything: bool = False) -> list[int]:
    """Emails still to parse; with everything=True also re-parse parsed/skipped ones (reparse)."""
    statuses = ("pending", "failed", "parsed", "skipped") if everything else ("pending", "failed")
    rows = conn.execute(text(
        "SELECT id FROM ledger.raw_emails WHERE user_id = :u AND source = :s AND parse_status = ANY(:st) "
        "ORDER BY received_at NULLS LAST, id"), {"u": uid, "s": source, "st": list(statuses)})
    return [r[0] for r in rows]


def load_raw_email(conn, uid: int, raw_email_id: int) -> RawEmail:
    r = conn.execute(text(
        "SELECT id, gmail_message_id, subject, sender, received_at, text_body, html_body, attachments "
        "FROM ledger.raw_emails WHERE id = :id AND user_id = :u"), {"id": raw_email_id, "u": uid}).one()
    return RawEmail(
        raw_email_id=r.id, gmail_message_id=r.gmail_message_id, subject=r.subject or "", sender=r.sender or "",
        received_at=r.received_at, text_body=r.text_body or "", html_body=r.html_body or "",
        attachments=[RawAttachment(a["attachment_id"], a.get("filename", ""), a.get("mime_type", ""),
                                   a.get("size", 0)) for a in r.attachments])


def cached_attachment(conn, raw_email_id: int, attachment_id: str) -> Optional[bytes]:
    row = conn.execute(text(
        "SELECT content FROM ledger.raw_attachments WHERE raw_email_id = :r AND attachment_id = :a"),
        {"r": raw_email_id, "a": attachment_id}).first()
    return bytes(row[0]) if row else None


def save_attachment(conn, raw_email_id: int, attachment_id: str, content: bytes, email: RawEmail) -> None:
    meta = next((a for a in email.attachments if a.attachment_id == attachment_id), None)
    conn.execute(text(
        "INSERT INTO ledger.raw_attachments (raw_email_id, attachment_id, filename, mime_type, content) "
        "VALUES (:r, :a, :f, :m, :c) ON CONFLICT (raw_email_id, attachment_id) DO NOTHING"),
        {"r": raw_email_id, "a": attachment_id, "f": meta.filename if meta else None,
         "m": meta.mime_type if meta else None, "c": content})


def set_parse_result(conn, raw_email_id: int, status: str, version: Optional[str], error: Optional[str]) -> None:
    conn.execute(text(
        "UPDATE ledger.raw_emails SET parse_status = :s, parser_version = :v, parse_error = :e, parsed_at = now() "
        "WHERE id = :id"), {"s": status, "v": version, "e": (error or None) and error[:500], "id": raw_email_id})


# ── transactions ───────────────────────────────────────────────────────────

_INSERT_TXN = text("""
INSERT INTO ledger.transactions
  (user_id, account_id, parent_id, node_type, kind, counter_account_id, needs_review, amount, occurred_at, has_time,
   source, source_ref, dedup_key, gmail_message_id, merchant_raw, description, raw, raw_email_id)
VALUES
  (:user_id, :account_id, :parent_id, :node_type, :kind, :counter, :needs_review, :amount, :occurred_at, :has_time,
   :source, :source_ref, :dedup_key, :gmail_message_id, :merchant_raw, :description, CAST(:raw AS jsonb), :raw_email_id)
ON CONFLICT (user_id, dedup_key) DO NOTHING
RETURNING id""")


def insert_tree(conn, node: TxnNode, *, user_id: int, account_id: int, source: str, raw_email: RawEmail,
                parent_id: Optional[int] = None) -> tuple[int, int]:
    """Insert a node and its children. Returns (inserted, duplicates).

    A node whose dedup_key already exists is skipped together with its subtree: re-seeing the same
    email must not add rows. needs_review is set when the children do not add up to the parent."""
    needs_review = bool(node.children) and sum(c.amount for c in node.children) != node.amount
    new_id = conn.execute(_INSERT_TXN, {
        "user_id": user_id, "account_id": account_id, "parent_id": parent_id, "node_type": node.node_type, "kind": node.kind,
        "counter": node.counter_account_id, "needs_review": needs_review, "amount": node.amount,
        "occurred_at": node.occurred_at, "has_time": node.has_time, "source": source,
        "source_ref": node.source_ref, "dedup_key": node.dedup_key,
        "gmail_message_id": raw_email.gmail_message_id, "merchant_raw": _clean(node.merchant_raw),
        "description": _clean(node.description), "raw": json.dumps(node.raw, default=str),
        "raw_email_id": raw_email.raw_email_id}).scalar()
    if new_id is None:
        return 0, 1
    inserted, dups = 1, 0
    for child in node.children:
        i, d = insert_tree(conn, child, user_id=user_id, account_id=account_id, source=source, raw_email=raw_email,
                           parent_id=new_id)
        inserted, dups = inserted + i, dups + d
    return inserted, dups
