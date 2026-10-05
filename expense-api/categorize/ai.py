"""AI categorizer port + Gemini adapter (google-genai, structured output)."""
import json
import os
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


class GeminiClassifier:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, client=None):
        if client is None:
            from google import genai  # imported lazily so tests and memory-only runs need no key
            client = genai.Client(api_key=api_key)  # GOOGLE_GENAI_USE_VERTEXAI=true switches to Vertex
        self._client = client
        self._model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    def classify(self, items, categories):
        from google.genai import types

        prompt = _PROMPT.format(
            categories="\n".join(f"{c.id}: {c.path}" for c in categories),
            items=json.dumps([{"id": i.id, "merchant": i.merchant, "channel": i.channel,
                               "amount_thb": str(i.amount), "description": i.description} for i in items],
                             ensure_ascii=False))
        resp = self._client.models.generate_content(
            model=self._model, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json",
                                               response_schema=list[Prediction], temperature=0))
        return list(resp.parsed or [])


def classifier_from_env() -> Optional[Classifier]:
    """None when no AI is configured: memory still runs, the rest stays uncategorized for later."""
    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() == "true":
        return GeminiClassifier()
    key = os.environ.get("GEMINI_API_KEY")
    return GeminiClassifier(api_key=key) if key else None


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
