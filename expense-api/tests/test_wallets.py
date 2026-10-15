from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.routers import wallets as wallets_router
from wallets.ocr import FakeWalletReader, WalletEntry, WalletReading

BKK = timezone(timedelta(hours=7))
PNG = b"\x89PNG\r\n\x1a\nfake"

READING = WalletReading(balance=310.0, entries=[
    WalletEntry(occurred_at="2026-09-05 14:12", title="7-Eleven", amount=89.0, direction="out", kind="payment"),
    WalletEntry(occurred_at="2026-09-05 14:10", title="เติมเงินจาก KBank", amount=1200.0, direction="in", kind="top_up"),
    WalletEntry(occurred_at="2026-09-06 09:00", title="Cashback", amount=5.0, direction="in", kind="cashback"),
    # above the first date header (its date is on the previous screenshot), and a row cut off at the edge
    WalletEntry(occurred_at="2026-09-05 22:10", title="Google Play", amount=750.0, direction="out", kind="payment",
                date_shown=False),
    WalletEntry(occurred_at="2026-09-05 10:39", title="", amount=0, direction="out", kind="other", uncertain=True),
])


@pytest.fixture
def api(db, monkeypatch, tmp_path):
    monkeypatch.setenv("RECEIPTS_DIR", str(tmp_path))
    monkeypatch.setattr("app.routers.wallets.get_engine", lambda: db)
    reader = FakeWalletReader(READING)
    app.dependency_overrides[wallets_router.get_reader] = lambda: reader
    app.dependency_overrides[wallets_router.get_runner] = lambda: lambda fn: fn()  # read before responding
    monkeypatch.setattr("app.routers.wallets.classifier_from_env", lambda: None)
    yield TestClient(app)
    app.dependency_overrides.pop(wallets_router.get_reader, None)
    app.dependency_overrides.pop(wallets_router.get_runner, None)


def acct(db, name):
    with db.connect() as conn:
        return conn.execute(text("SELECT id FROM ledger.accounts WHERE user_id = 1 AND name = :n"), {"n": name}).scalar()


def bank_row(db, merchant, amount, when, *, status="pending", src="none", key=None):
    with db.begin() as conn:
        return conn.execute(text(
            "INSERT INTO ledger.transactions (user_id, account_id, node_type, kind, status, amount, occurred_at, "
            "source, dedup_key, merchant_raw, category_source) "
            "VALUES (1, 1, 'payment', 'expense', :st, :a, :w, 'kbank_email', :k, :m, :s) RETURNING id"),
            {"a": Decimal(amount), "w": when, "k": key or f"k:{merchant}:{amount}:{when}", "m": merchant,
             "st": status, "s": src}).scalar()


def rows(db, sql, **p):
    with db.connect() as conn:
        return conn.execute(text(sql), p).all()


def upload(api, account_id, data=PNG):
    return api.post(f"/wallets/{account_id}/screenshots", files={"file": ("s.png", data, "image/png")})


def test_upload_reads_and_apply_records_rows_and_links_top_up(api, db):
    tm = acct(db, "TrueMoney")
    topup = bank_row(db, "TRUE MONEY COMPANY LIMITED", "1200", datetime(2026, 9, 5, 14, 10, 2, tzinfo=BKK))
    shot = upload(api, tm)
    assert shot.status_code == 201
    body = shot.json()
    assert body["ocr_status"] == "done" and len(body["ocr_result"]["entries"]) == 5
    assert rows(db, "SELECT count(*) FROM ledger.transactions WHERE source = 'wallet_screenshot'") == [(0,)]

    res = api.post(f"/wallet-screenshots/{body['id']}/apply", json={}).json()
    assert res["new"] == 2 and res["duplicate"] == 0
    assert [(t["status"], t["transaction_id"]) for t in res["top_ups"]] == [("linked", topup)]
    assert rows(db, """SELECT kind, amount, merchant_raw, status, account_id FROM ledger.transactions
                       WHERE source = 'wallet_screenshot' ORDER BY occurred_at""") == [
        ("expense", Decimal("89.00"), "7-Eleven", "pending", tm), ("income", Decimal("5.00"), "Cashback", "pending", tm)]
    assert rows(db, "SELECT kind, counter_account_id, category_source FROM ledger.transactions WHERE id = :i", i=topup) == [
        ("transfer", tm, "account")]

    # an overlapping screenshot adds nothing new, and the top-up is already a transfer
    again = api.post(f"/wallet-screenshots/{body['id']}/apply", json={}).json()
    assert again["new"] == 0 and again["duplicate"] == 2 and again["top_ups"][0]["status"] == "already"


def test_apply_uses_the_edited_entries(api, db):
    shot = upload(api, acct(db, "TrueMoney")).json()
    edited = [{"occurred_at": "2026-09-05 14:12", "title": "7-Eleven สาขา 1", "amount": 99, "direction": "out",
               "kind": "payment"}]
    assert api.post(f"/wallet-screenshots/{shot['id']}/apply", json={"entries": edited}).json()["new"] == 1
    assert rows(db, "SELECT merchant_raw, amount FROM ledger.transactions") == [("7-Eleven สาขา 1", Decimal("99.00"))]
    bad = [{**edited[0], "occurred_at": "5 Sep"}]
    assert api.post(f"/wallet-screenshots/{shot['id']}/apply", json={"entries": bad}).status_code == 422


@pytest.mark.parametrize("merchant,delta,status,src,expected", [
    ("Payment to Shopee", 0, "pending", "none", "not_found"),     # not the wallet
    ("TrueMoney", 30, "pending", "none", "not_found"),            # too far apart
    ("TrueMoney", 0, "confirmed", "user", "confirmed"),           # the user already decided
    ("TrueMoney", 0, "pending", "user", "confirmed"),
])
def test_top_up_left_alone(api, db, merchant, delta, status, src, expected):
    tm = acct(db, "TrueMoney")
    rid = bank_row(db, merchant, "1200", datetime(2026, 9, 5, 14, 10, tzinfo=BKK) + timedelta(minutes=delta),
                   status=status, src=src)
    shot = upload(api, tm).json()
    res = api.post(f"/wallet-screenshots/{shot['id']}/apply", json={}).json()
    assert res["top_ups"][0]["status"] == expected
    assert rows(db, "SELECT kind FROM ledger.transactions WHERE id = :i", i=rid) == [("expense",)]


def test_top_up_overrides_a_memory_category(api, db):
    rid = bank_row(db, "TRUE MONEY COMPANY LIMITED", "1200", datetime(2026, 9, 5, 14, 10, tzinfo=BKK), src="memory")
    shot = upload(api, acct(db, "TrueMoney")).json()
    assert api.post(f"/wallet-screenshots/{shot['id']}/apply", json={}).json()["top_ups"][0]["status"] == "linked"
    assert rows(db, "SELECT kind FROM ledger.transactions WHERE id = :i", i=rid) == [("transfer",)]


def test_two_bank_rows_are_ambiguous(api, db):
    for m in range(2):
        bank_row(db, "TrueMoney", "1200", datetime(2026, 9, 5, 14, 8 + m * 3, tzinfo=BKK))
    shot = upload(api, acct(db, "TrueMoney")).json()
    assert api.post(f"/wallet-screenshots/{shot['id']}/apply", json={}).json()["top_ups"][0]["status"] == "ambiguous"


def test_only_ewallets_and_cards_duplicates_and_image(api, db):
    assert upload(api, 1).status_code == 422          # a bank account
    assert upload(api, acct(db, "บัตรเครดิต KTC"), PNG + b"card").status_code == 201
    assert upload(api, 999999).status_code == 404
    tm = acct(db, "TrueMoney")
    shot = upload(api, tm).json()
    again = upload(api, tm)                            # e.g. after reloading the page: same screenshot back
    assert again.status_code == 201 and again.json()["id"] == shot["id"]
    assert upload(api, acct(db, "Shopee")).status_code == 409   # the same image on another wallet
    img = api.get(f"/wallet-screenshots/{shot['id']}/image")
    assert img.content == PNG and img.headers["content-type"] == "image/png"
    assert [s["id"] for s in api.get(f"/wallets/{tm}/screenshots").json()] == [shot["id"]]


def test_without_reader_is_503(api, db):
    app.dependency_overrides[wallets_router.get_reader] = lambda: None
    assert upload(api, acct(db, "TrueMoney")).status_code == 503


def test_reupload_does_not_read_again_and_discard_only_unrecorded(api, db):
    tm = acct(db, "TrueMoney")
    reader = FakeWalletReader(READING)
    app.dependency_overrides[wallets_router.get_reader] = lambda: reader
    first = upload(api, tm).json()
    upload(api, tm)
    assert reader.calls == 1
    other = upload(api, tm, PNG + b"2").json()
    assert api.delete(f"/wallet-screenshots/{other['id']}").status_code == 204
    assert [s["id"] for s in api.get(f"/wallets/{tm}/screenshots").json()] == [first["id"]]
    api.post(f"/wallet-screenshots/{first['id']}/apply", json={})
    assert api.delete(f"/wallet-screenshots/{first['id']}").status_code == 422


def test_reading_runs_in_the_background_with_progress(api, db):
    queued = []
    app.dependency_overrides[wallets_router.get_runner] = lambda: queued.append
    tm = acct(db, "TrueMoney")
    shot = upload(api, tm).json()
    assert shot["ocr_status"] == "pending" and shot["ocr_progress"]["stage"] == "queued" and len(queued) == 1
    upload(api, tm)                                      # already being read: not queued twice
    api.post(f"/wallet-screenshots/{shot['id']}/read")
    assert len(queued) == 1

    seen = []

    class Watching(FakeWalletReader):
        def read(self, image, mime_type, wallet, progress=None):
            progress("retry", attempt=1, attempts=4, wait=3.0, reason="rate_limit")
            seen.append(api.get(f"/wallet-screenshots/{shot['id']}").json()["ocr_progress"])
            return super().read(image, mime_type, wallet, progress)

    app.dependency_overrides[wallets_router.get_reader] = lambda: Watching(READING)
    with db.begin() as conn:                             # the queued job, run with the watching reader
        conn.execute(text("UPDATE ledger.wallet_screenshots SET ocr_progress_at = now() - interval '1 hour'"))
    api.post(f"/wallet-screenshots/{shot['id']}/read")   # stalled, so it is queued again
    queued[-1]()
    assert seen[0]["stage"] == "retry" and seen[0]["reason"] == "rate_limit" and seen[0]["since"]
    done = api.get(f"/wallet-screenshots/{shot['id']}").json()
    assert done["ocr_status"] == "done" and done["ocr_progress"] is None and len(done["ocr_result"]["entries"]) == 5


def test_stalled_reading_shows_as_failed(api, db):
    app.dependency_overrides[wallets_router.get_runner] = lambda: lambda fn: None   # the job never runs
    shot = upload(api, acct(db, "TrueMoney")).json()
    assert api.get(f"/wallet-screenshots/{shot['id']}").json()["ocr_status"] == "pending"
    with db.begin() as conn:
        conn.execute(text("UPDATE ledger.wallet_screenshots SET ocr_progress_at = now() - interval '4 minutes'"))
    stalled = api.get(f"/wallet-screenshots/{shot['id']}").json()
    assert (stalled["ocr_status"], stalled["ocr_error"]) == ("failed", "Stalled")


def test_failed_reading_is_stored(api, db):
    class Broken(FakeWalletReader):
        def read(self, image, mime_type, wallet, progress=None):
            raise TimeoutError()

    app.dependency_overrides[wallets_router.get_reader] = lambda: Broken(READING)
    shot = upload(api, acct(db, "TrueMoney")).json()
    assert (shot["ocr_status"], shot["ocr_error"], shot["ocr_progress"]) == ("failed", "TimeoutError", None)
    app.dependency_overrides[wallets_router.get_reader] = lambda: FakeWalletReader(READING)
    assert api.post(f"/wallet-screenshots/{shot['id']}/read").json()["ocr_status"] == "done"


def test_gemini_reader_retries_a_timeout_and_reports_it():
    import httpx
    from types import SimpleNamespace
    from wallets.ocr import GeminiWalletReader

    calls = []

    def generate_content(model, contents, config):
        calls.append(config.http_options.timeout)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow")
        return SimpleNamespace(parsed=READING)

    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    events = []
    reader = GeminiWalletReader(client, "m", backoff=0, timeout=5)
    assert reader.read(PNG, "image/png", "TrueMoney", lambda stage, **d: events.append((stage, d))) == READING
    assert calls == [5000, 5000]
    assert [(s, d.get("attempt"), d.get("reason")) for s, d in events] == [
        ("reading", 1, None), ("retry", 1, "timeout"), ("reading", 2, None)]
