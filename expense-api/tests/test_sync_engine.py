import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from sources.base import RawEmail, TxnNode
from sync import engine as sync_engine
from sync import repo
from sync.gmail_client import FakeMailClient

FIXTURES = Path(__file__).parent / "fixtures"
TZ = timezone.utc


def kbank_messages() -> dict:
    msgs = {}
    for name in ("kbank_bill_payment_1.json", "kbank_funds_transfer_1.json"):
        p = FIXTURES / name
        if not p.exists():
            pytest.skip(f"{name} is gitignored")
        m = json.loads(p.read_text(encoding="utf-8"))
        msgs[m["id"]] = m
    return msgs


def scalar(db, sql, **p):
    with db.connect() as conn:
        return conn.execute(text(sql), p).scalar()


def test_first_sync_creates_pending_payments(db):
    mail = FakeMailClient(kbank_messages())
    res = sync_engine.run_sync(db, 1, mail)
    assert res.counts["kbank_email"] == {"fetched": 2, "new": 2, "duplicate": 0, "skipped": 0, "failed": 0}
    assert scalar(db, "SELECT count(*) FROM ledger.transactions WHERE status='pending' AND account_id=1 "
                      "AND source='kbank_email' AND raw_email_id IS NOT NULL") == 2
    assert scalar(db, "SELECT count(*) FROM ledger.raw_emails WHERE parse_status='parsed'") == 2
    assert scalar(db, "SELECT status FROM ledger.sync_runs") == "success"


def test_second_sync_adds_nothing_and_does_not_refetch(db):
    msgs = kbank_messages()
    sync_engine.run_sync(db, 1, FakeMailClient(msgs))
    mail2 = FakeMailClient(msgs)
    res = sync_engine.run_sync(db, 1, mail2)
    assert res.counts["kbank_email"]["new"] == 0 and res.counts["kbank_email"]["fetched"] == 0
    assert mail2.fetched == []
    assert scalar(db, "SELECT count(*) FROM ledger.transactions") == 2


def test_reparse_is_idempotent(db):
    msgs = kbank_messages()
    sync_engine.run_sync(db, 1, FakeMailClient(msgs))
    counts = sync_engine.reparse(db, 1, FakeMailClient({}), "kbank_email")
    assert counts["new"] == 0 and counts["duplicate"] == 2
    assert scalar(db, "SELECT count(*) FROM ledger.transactions") == 2


def test_unparseable_email_fails_alone_and_is_retried(db):
    msgs = kbank_messages()
    msgs["bad"] = {"id": "bad", "subject": "x", "sender": "K PLUS", "internal_date": "1790000000000",
                   "text_body": "Dear Customer\nSubject: Result of Payment (Success)\n", "html_body": "",
                   "attachments": []}
    res = sync_engine.run_sync(db, 1, FakeMailClient(msgs))
    assert res.counts["kbank_email"]["new"] == 2 and res.counts["kbank_email"]["failed"] == 1
    assert scalar(db, "SELECT parse_status FROM ledger.raw_emails WHERE gmail_message_id='bad'") == "failed"
    # still failed on the next run (retried, not refetched), the good ones stay untouched
    res2 = sync_engine.run_sync(db, 1, FakeMailClient(msgs))
    assert res2.counts["kbank_email"]["failed"] == 1 and res2.counts["kbank_email"]["new"] == 0


def test_running_sync_blocks_a_second_one(db):
    with db.begin() as conn:
        repo.start_run(conn, 1, datetime.now(TZ), datetime.now(TZ))
    with pytest.raises(sync_engine.SyncBusy):
        sync_engine.run_sync(db, 1, FakeMailClient({}))


def test_stale_running_sync_does_not_block(db):
    with db.begin() as conn:
        conn.execute(text("INSERT INTO ledger.sync_runs (user_id, started_at, status) "
                          "VALUES (1, now() - interval '2 hours', 'running')"))
    sync_engine.run_sync(db, 1, FakeMailClient({}))
    assert scalar(db, "SELECT count(*) FROM ledger.sync_runs WHERE status='failed'") == 1


def test_active_source_without_parser_is_reported_not_fatal(db):
    with db.begin() as conn:
        conn.execute(text("UPDATE ledger.email_sources SET is_active=true, gmail_label='x' "
                          "WHERE source='shopee_email'"))
    res = sync_engine.run_sync(db, 1, FakeMailClient(kbank_messages()))
    assert res.counts["shopee_email"]["error"] == "no parser registered"
    assert res.counts["kbank_email"]["new"] == 2
    assert scalar(db, "SELECT status FROM ledger.sync_runs") == "success"


def _node(kind_amount, children=()):
    return TxnNode(node_type="statement", kind="expense", amount=Decimal(kind_amount),
                   occurred_at=datetime(2026, 10, 1, tzinfo=TZ), dedup_key="t:root", has_time=False,
                   children=list(children))


def _line(key, amount):
    return TxnNode(node_type="statement_line", kind="expense", amount=Decimal(amount),
                   occurred_at=datetime(2026, 10, 1, tzinfo=TZ), dedup_key=key, has_time=False)


def _email():
    return RawEmail(raw_email_id=0, gmail_message_id="g", subject="", sender="", received_at=None,
                    text_body="", html_body="", attachments=[])


def test_tree_links_children_and_flags_mismatch(db):
    with db.begin() as conn:
        rid = repo.insert_raw_email(conn, 1, "ktc_pdf", None, {"id": "g", "subject": "s"})
        email = _email()
        email.raw_email_id = rid
        tree = _node("100.00", [_line("t:a", "60.00"), _line("t:b", "30.00")])
        assert repo.insert_tree(conn, tree, user_id=1, account_id=2, source="ktc_pdf", raw_email=email) == (3, 0)
        # same tree again: everything is a duplicate, nothing inserted
        assert repo.insert_tree(conn, tree, user_id=1, account_id=2, source="ktc_pdf", raw_email=email) == (0, 1)
    assert scalar(db, "SELECT needs_review FROM ledger.transactions WHERE dedup_key='t:root'") is True
    assert scalar(db, "SELECT count(*) FROM ledger.transactions c JOIN ledger.transactions p "
                      "ON c.parent_id = p.id WHERE p.dedup_key='t:root'") == 2


def test_balanced_tree_not_flagged_and_only_leaves_are_reportable(db):
    with db.begin() as conn:
        rid = repo.insert_raw_email(conn, 1, "ktc_pdf", None, {"id": "g2", "subject": "s"})
        email = _email()
        email.raw_email_id = rid
        repo.insert_tree(conn, _node("90.00", [_line("t:a", "60.00"), _line("t:b", "30.00")]),
                         user_id=1, account_id=2, source="ktc_pdf", raw_email=email)
        conn.execute(text("UPDATE ledger.transactions SET status='confirmed'"))
    assert scalar(db, "SELECT needs_review FROM ledger.transactions WHERE dedup_key='t:root'") is False
    assert scalar(db, "SELECT sum(amount) FROM ledger.v_reportable_expenses") == Decimal("90.00")


def test_new_source_gets_its_own_first_run_window(db):
    class RecordingMail(FakeMailClient):
        def __init__(self):
            super().__init__({})
            self.queries = []

        def list_message_ids(self, label, date_after, search_text=None, page_token=None):
            self.queries.append((label, date_after))
            return [], None

    now = datetime(2026, 10, 6, tzinfo=TZ)
    sync_engine.run_sync(db, 1, FakeMailClient(kbank_messages()), now=now)   # kbank fetched once
    with db.begin() as conn:
        conn.execute(text("UPDATE ledger.email_sources SET is_active = true, gmail_label = 'x' "
                          "WHERE source = 'ktc_pdf'"))
    mail = RecordingMail()
    sync_engine.run_sync(db, 1, mail, now=now + timedelta(days=1))
    after = dict(mail.queries)
    assert after["financial-kasikorn"] == "2026/10/05"   # last success (10/06) - 1 day overlap
    assert after["x"] == "2026/07/09"                    # 90 days back: ktc never fetched before
