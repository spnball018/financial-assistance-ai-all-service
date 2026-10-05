"""Read a receipt photo into line items with a suggested category (T6.5). Port + Gemini adapter + fake."""
import os
import time
from typing import Optional, Protocol

from pydantic import BaseModel

from categorize.ai import Category


class ReceiptItem(BaseModel):
    name: str
    quantity: float
    amount: float  # line total in THB, positive
    category_id: int
    confidence: float


class ReceiptAdjustment(BaseModel):
    name: str        # e.g. discount, member points, rounding, service charge
    amount: float    # negative for discounts


class ReceiptReading(BaseModel):
    merchant: str
    total: float     # grand total printed on the receipt
    items: list[ReceiptItem]
    adjustments: list[ReceiptAdjustment]


class ReceiptReader(Protocol):
    model: str

    def read(self, image: bytes, mime_type: str, categories: list[Category]) -> ReceiptReading: ...


_PROMPT = """This is a photo of a Thai shop receipt. Read every purchased line item.
For each item give its name as printed (Thai or English), quantity, the line total in THB (positive),
and the best category id from the list below (use the Uncategorized id when unsure) with a confidence 0..1.
Put discounts, member/point redemptions, rounding and service charges in `adjustments`
(discounts negative). Give the grand total printed on the receipt as `total`.
If a line is unreadable, skip it rather than guessing an amount.

Categories (id: path):
{categories}"""


class GeminiReceiptReader:
    def __init__(self, client, model: Optional[str] = None, *, retries: int = 4, backoff: float = 3.0):
        self._client = client
        self.model = model or os.environ.get("RECEIPT_MODEL") or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self._retries = retries
        self._backoff = backoff

    def read(self, image, mime_type, categories):
        from google.genai import errors, types

        contents = [types.Part.from_bytes(data=image, mime_type=mime_type),
                    _PROMPT.format(categories="\n".join(f"{c.id}: {c.path}" for c in categories))]
        config = types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=ReceiptReading, temperature=0,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        for attempt in range(self._retries):
            try:
                resp = self._client.models.generate_content(model=self.model, contents=contents, config=config)
                if resp.parsed is None:
                    raise ValueError("model returned no structured reading")
                return resp.parsed
            except (errors.ServerError, errors.ClientError) as e:
                retryable = isinstance(e, errors.ServerError) or getattr(e, "code", None) == 429
                if not retryable or attempt == self._retries - 1:
                    raise
                time.sleep(self._backoff * 2 ** attempt)
        raise RuntimeError("unreachable")


def reader_from_env() -> Optional[ReceiptReader]:
    """Receipts need a vision model: Gemini API key or Vertex. None when neither is configured."""
    from google import genai

    if os.environ.get("CATEGORIZER", "").lower() == "vertex" or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() == "true":
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project:
            return None
        return GeminiReceiptReader(genai.Client(vertexai=True, project=project,
                                                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global")))
    key = os.environ.get("GEMINI_API_KEY")
    return GeminiReceiptReader(genai.Client(api_key=key)) if key else None


class FakeReceiptReader:
    model = "fake"

    def __init__(self, reading: ReceiptReading):
        self.reading = reading
        self.calls = 0

    def read(self, image, mime_type, categories):
        self.calls += 1
        return self.reading
