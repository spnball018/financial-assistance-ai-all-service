from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from categorize import memory
from categorize.ai import FakeClassifier, GeminiClassifier, Item, Category, Prediction
from categorize.service import categorize_pending

TZ = timezone.utc


def add(db, key, merchant, *, parent=None, status="pending", source_cat="none", kind="expense"):
    with db.begin() as conn:
        return conn.execute(text(
            "INSERT INTO ledger.transactions (account_id, parent_id, node_type, kind, counter_account_id, status, "
            "amount, occurred_at, source, dedup_key, merchant_raw, category_source, raw) "
            "VALUES (1, :p, 'payment', :kind, CASE WHEN :kind = 'transfer' THEN 2 END, :st, 100, :at, "
            "'kbank_email', :k, :m, :cs, '{\"channel\": \"bill_payment\"}') RETURNING id"),
            {"p": parent, "st": status, "at": datetime(2026, 10, 1, tzinfo=TZ), "k": key, "m": merchant,
             "cs": source_cat, "kind": kind}).scalar()


def cat_id(db, name_en):
    with db.connect() as conn:
        return conn.execute(text("SELECT id FROM ledger.categories WHERE name_en = :n"), {"n": name_en}).scalar()


def row(db, txn_id):
    with db.connect() as conn:
        return conn.execute(text("SELECT category_id, category_source, ai_confidence, merchant_key "
                                 "FROM ledger.transactions WHERE id = :i"), {"i": txn_id}).one()


@pytest.fixture
def cdb(db):
    with db.begin() as conn:
        conn.execute(text("TRUNCATE ledger.merchant_memory RESTART IDENTITY"))
    return db


def test_memory_hit_wins_and_ai_is_not_called(cdb):
    fuel = cat_id(cdb, "Fuel")
    with cdb.begin() as conn:
        memory.remember(conn, "mcp pt ban pho", fuel)
    t = add(cdb, "k1", "MCP-PT Ban Pho 3 (พีที บ้านโพธิ์3)")
    ai = FakeClassifier({})
    res = categorize_pending(cdb, ai)
    assert res["memory"] == 1 and ai.calls == []
    r = row(cdb, t)
    assert (r.category_id, r.category_source, r.merchant_key) == (fuel, "memory", "mcp pt ban pho")


def test_ai_above_threshold_sets_category(cdb, monkeypatch):
    monkeypatch.setenv("AI_CONFIDENCE_THRESHOLD", "0.7")
    cafe = cat_id(cdb, "Cafe & Bakery")
    t = add(cdb, "k1", "STARBUCKS")
    res = categorize_pending(cdb, FakeClassifier({"STARBUCKS": (cafe, 0.93)}))
    assert res["ai"] == 1
    r = row(cdb, t)
    assert (r.category_id, r.category_source, float(r.ai_confidence)) == (cafe, "ai", 0.93)


def test_low_confidence_and_invalid_category_become_uncategorized(cdb):
    unc = cat_id(cdb, "Uncategorized")
    food_parent = cat_id(cdb, "Food & Dining")  # has children -> not a valid answer
    a = add(cdb, "k1", "SOMEONE")
    b = add(cdb, "k2", "ODD SHOP")
    res = categorize_pending(cdb, FakeClassifier({"SOMEONE": (cat_id(cdb, "Fuel"), 0.4),
                                                  "ODD SHOP": (food_parent, 0.99)}))
    assert res["uncategorized"] == 2
    assert row(cdb, a).category_id == unc and row(cdb, a).category_source == "ai"
    assert row(cdb, b).category_id == unc


def test_without_ai_rows_stay_none_for_later(cdb):
    t = add(cdb, "k1", "STARBUCKS")
    res = categorize_pending(cdb, None)
    assert res == {"memory": 0, "ai": 0, "uncategorized": 0, "left": 1}
    assert row(cdb, t).category_source == "none" and row(cdb, t).merchant_key == "starbucks"


def test_skips_parents_confirmed_already_categorized_and_transfers(cdb):
    parent = add(cdb, "p", "SHOPEE")
    add(cdb, "c", "ITEM", parent=parent)
    add(cdb, "done", "X", status="confirmed")
    add(cdb, "user", "Y", source_cat="user")
    add(cdb, "tr", "KTC", kind="transfer")
    ai = FakeClassifier({"ITEM": (cat_id(cdb, "Fashion"), 0.9)})
    categorize_pending(cdb, ai)
    assert [i.merchant for i in ai.calls[0]] == ["ITEM"]
    assert row(cdb, parent).category_source == "none"


def test_ai_failure_is_reported_and_rows_left(cdb):
    class Boom:
        def classify(self, items, categories):
            raise RuntimeError("secret prompt content")

    add(cdb, "k1", "A")
    res = categorize_pending(cdb, Boom())
    assert res["left"] == 1 and res["error"] == "ai: RuntimeError"


def test_remember_updates_and_counts(cdb):
    a, b = cat_id(cdb, "Fuel"), cat_id(cdb, "Tolls")
    with cdb.begin() as conn:
        memory.remember(conn, "k", a)
        memory.remember(conn, "k", b)
        r = conn.execute(text("SELECT category_id, hit_count FROM ledger.merchant_memory WHERE merchant_key='k'")).one()
    assert (r.category_id, r.hit_count) == (b, 2)


def test_gemini_adapter_builds_request_and_parses():
    seen = {}

    class Resp:
        parsed = [Prediction(id=1, category_id=5, confidence=0.8, reason="r")]

    class Models:
        def generate_content(self, model, contents, config):
            seen.update(model=model, contents=contents, config=config)
            return Resp()

    class Client:
        models = Models()

    out = GeminiClassifier(Client(), model="m-test").classify(
        [Item(id=1, merchant="STARBUCKS", channel="bill_payment", amount=Decimal("120.00"))],
        [Category(5, "Food & Dining > Cafe & Bakery / อาหาร > คาเฟ่")])
    assert out[0].category_id == 5 and seen["model"] == "m-test"
    assert "STARBUCKS" in seen["contents"] and "5: Food & Dining > Cafe" in seen["contents"]
    assert seen["config"].response_mime_type == "application/json"


def test_batch_size_from_env(cdb, monkeypatch):
    monkeypatch.setenv("CATEGORIZE_BATCH_SIZE", "2")
    for i in range(5):
        add(cdb, f"k{i}", f"M{i}")
    ai = FakeClassifier({f"M{i}": (cat_id(cdb, "Fuel"), 0.9) for i in range(5)})
    categorize_pending(cdb, ai)
    assert [len(c) for c in ai.calls] == [2, 2, 1]


class TestAdapterSelection:
    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch):
        for k in ("CATEGORIZER", "GEMINI_API_KEY", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"):
            monkeypatch.delenv(k, raising=False)

    def test_default_without_key_is_none(self):
        from categorize.ai import classifier_from_env
        assert classifier_from_env() is None

    def test_default_with_key_is_gemini_api(self, monkeypatch):
        from categorize.ai import classifier_from_env
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        c = classifier_from_env()
        assert isinstance(c, GeminiClassifier) and not c._client._api_client.vertexai

    def test_vertex(self, monkeypatch):
        from categorize.ai import classifier_from_env
        monkeypatch.setenv("CATEGORIZER", "vertex")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "asia-southeast1")
        try:
            c = classifier_from_env()
        except Exception as e:  # no Application Default Credentials on this machine
            pytest.skip(f"Vertex client needs ADC: {type(e).__name__}")
        api = c._client._api_client
        assert api.vertexai and api.project == "my-proj" and api.location == "asia-southeast1"

    @pytest.mark.parametrize("env,msg", [
        ({"CATEGORIZER": "vertex"}, "GOOGLE_CLOUD_PROJECT"),
        ({"CATEGORIZER": "gemini"}, "GEMINI_API_KEY"),
        ({"CATEGORIZER": "llama"}, "unknown CATEGORIZER"),
    ])
    def test_misconfiguration_is_explicit(self, monkeypatch, env, msg):
        from categorize.ai import classifier_from_env
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        with pytest.raises(RuntimeError, match=msg):
            classifier_from_env()

    def test_none_wins_over_key(self, monkeypatch):
        from categorize.ai import classifier_from_env
        monkeypatch.setenv("CATEGORIZER", "none")
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        assert classifier_from_env() is None


def test_misconfigured_categorizer_does_not_block_sync_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import sync as sync_router
    from sync.engine import SyncResult

    monkeypatch.setenv("CATEGORIZER", "vertex")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setattr(sync_router.sync_engine, "run_sync", lambda engine, mail: SyncResult(run_id=7, counts={}))
    monkeypatch.setattr(sync_router, "get_engine", lambda: None)
    app.dependency_overrides[sync_router.get_mail_client] = lambda: None
    try:
        client = TestClient(app)
        r = client.post("/sync")
        assert r.status_code == 200
        assert r.json()["run_id"] == 7 and r.json()["categorize"] == {"error": "RuntimeError"}
        r2 = client.post("/categorize")
        assert r2.status_code == 500 and "GOOGLE_CLOUD_PROJECT" in r2.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def _client_raising(*excs):
    """Fake google-genai client that raises the given exceptions in order, then succeeds."""
    calls = []

    class Resp:
        parsed = [Prediction(id=1, category_id=5, confidence=0.9, reason="r")]

    class Models:
        def generate_content(self, model, contents, config):
            calls.append(1)
            if len(calls) <= len(excs):
                raise excs[len(calls) - 1]
            return Resp()

    class Client:
        models = Models()

    return Client(), calls


def _api_error(cls, code):
    return cls(code, {"error": {"code": code, "message": "x", "status": "S"}})


ITEMS = [Item(id=1, merchant="M", channel="c", amount=Decimal("1"))]
CATS = [Category(5, "x")]


def test_gemini_retries_503_and_429():
    from google.genai import errors
    client, calls = _client_raising(_api_error(errors.ServerError, 503), _api_error(errors.ClientError, 429))
    out = GeminiClassifier(client, model="m", backoff=0).classify(ITEMS, CATS)
    assert out[0].category_id == 5 and len(calls) == 3


def test_gemini_does_not_retry_404_and_gives_up_eventually():
    from google.genai import errors
    client, calls = _client_raising(_api_error(errors.ClientError, 404))
    with pytest.raises(errors.ClientError):
        GeminiClassifier(client, model="m", backoff=0).classify(ITEMS, CATS)
    assert len(calls) == 1
    client, calls = _client_raising(*[_api_error(errors.ServerError, 503)] * 5)
    with pytest.raises(errors.ServerError):
        GeminiClassifier(client, model="m", retries=3, backoff=0).classify(ITEMS, CATS)
    assert len(calls) == 3


def test_donation_category_is_offered_to_ai_and_uncategorized_stays_last(cdb):
    from categorize.service import _leaf_categories
    with cdb.connect() as conn:
        cats, unc = _leaf_categories(conn)
        order = conn.execute(text("SELECT name_en FROM ledger.categories WHERE parent_id IS NULL "
                                  "ORDER BY sort_order")).scalars().all()
    assert any(c.path.startswith("Donations & Merit-making / บริจาค/ทำบุญ") for c in cats)
    assert order[-2:] == ["Donations & Merit-making", "Uncategorized"]
