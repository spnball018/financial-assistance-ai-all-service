"""Sync orchestration: Gmail -> raw_emails -> source parser -> transactions.

No SQL here (sync/repo.py) and no Gmail HTTP details (sync/gmail_client.py).
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.engine import Engine

from app import secrets
from sources import REGISTRY
from sources.base import ParseContext
from sync import repo
from sync.gmail_client import MailClient, MailError

log = logging.getLogger("sync")

FIRST_RUN_DAYS = 90
OVERLAP_DAYS = 1


class SyncBusy(Exception):
    """Another sync is still running."""


@dataclass
class SourceCounts:
    fetched: int = 0     # new emails stored in raw_emails
    new: int = 0         # transactions inserted
    duplicate: int = 0   # transactions already present (dedup_key)
    skipped: int = 0     # emails the parser chose to ignore
    failed: int = 0      # emails that could not be fetched or parsed
    error: Optional[str] = None

    def as_dict(self) -> dict:
        d = {"fetched": self.fetched, "new": self.new, "duplicate": self.duplicate,
             "skipped": self.skipped, "failed": self.failed}
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class SyncResult:
    run_id: Optional[int]
    counts: dict[str, dict] = field(default_factory=dict)


def _safe_setting(key: str) -> Optional[str]:
    return secrets.get_setting(key)


def _parse_one(engine: Engine, mail: MailClient, source: str, account_id: Optional[int],
               raw_email_id: int, c: SourceCounts) -> None:
    module = REGISTRY[source]
    try:
        with engine.begin() as conn:
            email = repo.load_raw_email(conn, raw_email_id)

            def get_attachment(att_id: str) -> bytes:
                cached = repo.cached_attachment(conn, raw_email_id, att_id)
                if cached is not None:
                    return cached
                content = mail.get_attachment(email.gmail_message_id, att_id)
                repo.save_attachment(conn, raw_email_id, att_id, content, email)
                return content

            result = module.parse(email, ParseContext(account_id=account_id, get_attachment=get_attachment,
                                                      get_setting=_safe_setting))
            if result.status == "parsed":
                if account_id is None:
                    raise RuntimeError(f"source {source} has no account_id")
                for node in result.nodes:
                    i, d = repo.insert_tree(conn, node, account_id=account_id, source=source, raw_email=email)
                    c.new += i
                    c.duplicate += d
            repo.set_parse_result(conn, raw_email_id, result.status, module.PARSER_VERSION, result.error)
        if result.status == "skipped":
            c.skipped += 1
        elif result.status == "failed":
            c.failed += 1
    except Exception as e:
        # Only the class name is stored: messages may contain PDF content or passwords.
        log.warning("parse failed raw_email_id=%s source=%s: %s", raw_email_id, source, type(e).__name__)
        with engine.begin() as conn:
            repo.set_parse_result(conn, raw_email_id, "failed", module.PARSER_VERSION, type(e).__name__)
        c.failed += 1


def _fetch_source(engine: Engine, mail: MailClient, src: dict, since: datetime, c: SourceCounts) -> None:
    date_after = since.strftime("%Y/%m/%d")
    search = f"from:{src['sender_filter']}" if src["sender_filter"] else None
    token = None
    while True:
        ids, token = mail.list_message_ids(src["gmail_label"], date_after, search, token)
        with engine.connect() as conn:
            known = repo.known_message_ids(conn, ids)
        for mid in ids:
            if mid in known:
                continue
            try:
                msg = mail.get_message(mid)
                with engine.begin() as conn:
                    if repo.insert_raw_email(conn, src["source"], src["gmail_label"], msg) is not None:
                        c.fetched += 1
            except MailError as e:
                log.warning("fetch failed source=%s id=%s: %s", src["source"], mid, e)
                c.failed += 1
        if not token:
            break


def run_sync(engine: Engine, mail: MailClient, now: Optional[datetime] = None) -> SyncResult:
    now = now or datetime.now(timezone.utc)
    with engine.begin() as conn:
        if repo.has_running(conn):
            raise SyncBusy()
        last = repo.last_success_range_to(conn)
        since = (last - timedelta(days=OVERLAP_DAYS)) if last else now - timedelta(days=FIRST_RUN_DAYS)
        run_id = repo.start_run(conn, since, now)
        sources = repo.active_sources(conn)

    counts: dict[str, SourceCounts] = {}
    error = None
    try:
        for src in sources:
            c = counts[src["source"]] = SourceCounts()
            if src["source"] not in REGISTRY:
                c.error = "no parser registered"
                continue
            # a source switched on later (or never fetched) gets its own first-run window
            with engine.connect() as conn:
                src_since = since if repo.has_raw_emails(conn, src["source"]) else now - timedelta(days=FIRST_RUN_DAYS)
            try:
                _fetch_source(engine, mail, src, src_since, c)
            except MailError as e:
                c.error = f"gmail: {e}"
                continue
            with engine.connect() as conn:
                ids = repo.parseable_raw_email_ids(conn, src["source"])
            for raw_id in ids:
                _parse_one(engine, mail, src["source"], src["account_id"], raw_id, c)
    except Exception as e:  # keep the run row accurate even on unexpected errors
        error = type(e).__name__
        log.exception("sync crashed")

    out = {k: v.as_dict() for k, v in counts.items()}
    # a source that could not reach Gmail makes the run failed, so the next run does not skip its window
    failed = error is not None or any(v.error and v.error != "no parser registered" for v in counts.values())
    with engine.begin() as conn:
        repo.finish_run(conn, run_id, "failed" if failed else "success", out, error)
    return SyncResult(run_id=run_id, counts=out)


def reparse(engine: Engine, mail: MailClient, source: str) -> dict:
    """Re-run the parser over every stored email of a source; no Gmail listing, dedup keeps it idempotent."""
    if source not in REGISTRY:
        raise KeyError(source)
    c = SourceCounts()
    with engine.connect() as conn:
        account_id = repo.source_account(conn, source)
        ids = repo.parseable_raw_email_ids(conn, source, everything=True)
    for raw_id in ids:
        _parse_one(engine, mail, source, account_id, raw_id, c)
    return c.as_dict()
