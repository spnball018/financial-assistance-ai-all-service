"""AI categorizer port and adapters (Gemini API, Vertex AI), chosen by env CATEGORIZER."""
import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Protocol

from pydantic import BaseModel


@dataclass
class Item:
    id: int
    merchant: str
    channel: Optional[str]  # e.g. bill_payment, promptpay, statement_line, order_item
    amount: Decimal
    description: Optional[str] = None


@dataclass
class Category:
    id: int
    path: str  # "Food & Dining > Cafe & Bakery / อาหารและเครื่องดื่ม > คาเฟ่/เบเกอรี่"


class Prediction(BaseModel):
    id: int
    category_id: int
    confidence: float
    reason: str


class Classifier(Protocol):
    def classify(self, items: list[Item], categories: list[Category]) -> list[Prediction]: ...


_PROMPT = """You categorize personal expenses of a Thai user.
Pick exactly one category id for every transaction from the list below. Use the "Uncategorized"
category when nothing fits. Merchant names may be Thai or English, may be a company's legal name,
or a person's name (PromptPay / bank transfer to an individual); for a person with no other clue,
choose Uncategorized with low confidence.
confidence is 0..1: how sure you are. reason: one short sentence, English.

Categories (id: path):
{categories}

Transactions (JSON):
{items}

Return one object per transaction id."""


def build_prompt(items: list[Item], categories: list[Category]) -> str:
    """Shared by every adapter so switching provider does not change what the model is asked."""
    return _PROMPT.format(
        categories="\n".join(f"{c.id}: {c.path}" for c in categories),
        items=json.dumps([{"id": i.id, "merchant": i.merchant, "channel": i.channel,
                           "amount_thb": str(i.amount), "description": i.description} for i in items],
                         ensure_ascii=False))


class GeminiClassifier:
    """Gemini through google-genai. The same class serves the Gemini API and Vertex AI;
    only the client differs (see the factories below)."""

    def __init__(self, client, model: Optional[str] = None, *, retries: int = 4, backoff: float = 3.0):
        self._client = client
        # gemini-2.5-flash returns 404 for new users (checked 2026-10-05); flash-lite is enough to classify
        self._model = model or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self._retries = retries
        self._backoff = backoff

    def classify(self, items, categories):
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=list[Prediction], temperature=0,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        for attempt in range(self._retries):
            try:
                resp = self._client.models.generate_content(
                    model=self._model, contents=build_prompt(items, categories), config=config)
                return list(resp.parsed or [])
            except (errors.ServerError, errors.ClientError) as e:
                # 503 "high demand" and 429 rate limits are common on the free tier and pass quickly
                retryable = isinstance(e, errors.ServerError) or getattr(e, "code", None) == 429
                if not retryable or attempt == self._retries - 1:
                    raise
                time.sleep(self._backoff * 2 ** attempt)
        return []


# ── adapter registry ───────────────────────────────────────────────────────
# CATEGORIZER picks one. A new provider (e.g. a local model) = one factory + one entry here.

def _gemini_api() -> Classifier:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("CATEGORIZER=gemini needs GEMINI_API_KEY")
    from google import genai  # lazy: memory-only runs and tests need no Google packages loaded
    return GeminiClassifier(genai.Client(api_key=key))


def _vertex() -> Classifier:
    """Vertex AI with Application Default Credentials (gcloud auth application-default login,
    or a service account via GOOGLE_APPLICATION_CREDENTIALS). Billed to the GCP project."""
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("CATEGORIZER=vertex needs GOOGLE_CLOUD_PROJECT")
    from google import genai
    return GeminiClassifier(genai.Client(vertexai=True, project=project,
                                         location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global")))


ADAPTERS = {"gemini": _gemini_api, "vertex": _vertex}


def classifier_from_env() -> Optional[Classifier]:
    """CATEGORIZER = gemini | vertex | none. Unset: gemini if GEMINI_API_KEY is set, else none.
    None means Merchant Memory still runs and the rest stays uncategorized for a later run."""
    choice = os.environ.get("CATEGORIZER", "").strip().lower()
    if not choice:
        choice = "gemini" if os.environ.get("GEMINI_API_KEY") else "none"
    if choice == "none":
        return None
    if choice not in ADAPTERS:
        raise RuntimeError(f"unknown CATEGORIZER '{choice}' (use {', '.join([*ADAPTERS, 'none'])})")
    return ADAPTERS[choice]()


class FakeClassifier:
    """Test double: returns canned predictions keyed by merchant."""

    def __init__(self, by_merchant: dict[str, tuple[int, float]]):
        self.by_merchant = by_merchant
        self.calls: list[list[Item]] = []

    def classify(self, items, categories):
        self.calls.append(items)
        return [Prediction(id=i.id, category_id=self.by_merchant[i.merchant][0],
                           confidence=self.by_merchant[i.merchant][1], reason="fake")
                for i in items if i.merchant in self.by_merchant]
