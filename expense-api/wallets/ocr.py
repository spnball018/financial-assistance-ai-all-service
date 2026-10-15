"""Read a screenshot of an e-wallet app's history (TrueMoney, ...) into entries. Port + Gemini adapter + fake.
Same shape as receipts/ocr.py."""
import os
import time
from typing import Callable, Literal, Optional, Protocol

from pydantic import BaseModel

EntryKind = Literal["payment", "top_up", "transfer_out", "transfer_in", "refund", "cashback", "other"]


class WalletEntry(BaseModel):
    occurred_at: str   # "YYYY-MM-DD HH:MM" in Thai time, Christian Era year
    title: str         # merchant or description as shown
    amount: float      # positive
    direction: Literal["out", "in"]
    kind: EntryKind
    # False for rows above the first date header on screen: their date is not visible (it is on the
    # previous screenshot), so they are not recorded unless the user fixes the date
    date_shown: bool = True
    # True when glare or the screen edge hides part of the amount or time; the user should check it
    uncertain: bool = False


class WalletReading(BaseModel):
    entries: list[WalletEntry]
    balance: Optional[float] = None  # wallet balance if the screenshot shows it


# progress(stage, **detail): "reading" (attempt, attempts) before each call to the model, "retry" (attempt,
# attempts, wait seconds, reason rate_limit | server | timeout) before waiting to try again
Progress = Callable[..., None]


class WalletReader(Protocol):
    model: str

    def read(self, image: bytes, mime_type: str, wallet: str, progress: Optional[Progress] = None) -> WalletReading: ...


_PROMPT = """This is a screenshot, or a photo of a phone screen, from the {wallet} app (Thailand):
a transaction history list or a single transaction slip. Return every transaction row visible on the
screen, top to bottom, one entry each.
- occurred_at: date and time as "YYYY-MM-DD HH:MM". Thai dates use the Buddhist Era (e.g. 2569 or 69): convert
  to the Christian Era (2569 -> 2026). In a list, rows belong to the nearest date header ABOVE them
  (e.g. "09 ตุลาคม 2569") and show only the time.
- date_shown: false for rows that sit above the first date header visible on the screen. Their date is
  not on this screen: never take it from a header below them. Still fill occurred_at with your best
  guess, the user will check it.
- title: the merchant, payee or description exactly as shown (Thai or English).
- amount: positive number in THB. direction: "out" when money left the wallet (minus sign, e.g. "-฿ 87.00"),
  "in" when it came in (plus sign, usually green, e.g. "+฿ 2,000.00").
- kind: top_up (money added from a bank or card, e.g. "เติมเงินผ่านบัญชีธนาคาร"; for a credit card, a
  payment of the card bill such as "ชำระบัตร", "ชำระยอดบัตร" or "Payment - Thank you", direction "in"),
  payment (shop, bill, app store, subscription, card purchase), transfer_out (sent to a person),
  transfer_in (received from a person), refund, cashback, or other.
  For a credit card, purchases are "out" even when the app shows them without a minus sign.
- uncertain: true when glare, reflection or the screen edge hides any digit of the amount or time; give
  your best reading anyway.
Skip a row that is cut off by the top or bottom edge of the screen so that its title or its amount is not
fully visible: never invent a title or amount for it.
Give the wallet balance only if it is shown."""


class GeminiWalletReader:
    def __init__(self, client, model: Optional[str] = None, *, retries: int = 4, backoff: float = 3.0,
                 timeout: float = 60.0):
        self._client = client
        self.model = model or os.environ.get("RECEIPT_MODEL") or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self._retries = retries
        self._backoff = backoff
        self._timeout = timeout  # seconds per call: without it a stuck call never returns

    def read(self, image, mime_type, wallet, progress=None):
        import httpx
        from google.genai import errors, types

        progress = progress or (lambda stage, **detail: None)
        contents = [types.Part.from_bytes(data=image, mime_type=mime_type), _PROMPT.format(wallet=wallet)]
        config = types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=WalletReading, temperature=0,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            http_options=types.HttpOptions(timeout=int(self._timeout * 1000)))
        for attempt in range(self._retries):
            progress("reading", attempt=attempt + 1, attempts=self._retries)
            try:
                resp = self._client.models.generate_content(model=self.model, contents=contents, config=config)
                if resp.parsed is None:
                    raise ValueError("model returned no structured reading")
                return resp.parsed
            except (errors.ServerError, errors.ClientError, httpx.TimeoutException) as e:
                if isinstance(e, httpx.TimeoutException):
                    reason = "timeout"
                elif isinstance(e, errors.ServerError):
                    reason = "server"
                elif getattr(e, "code", None) == 429:
                    reason = "rate_limit"
                else:
                    raise
                if attempt == self._retries - 1:
                    raise
                wait = self._backoff * 2 ** attempt
                progress("retry", attempt=attempt + 1, attempts=self._retries, wait=wait, reason=reason)
                time.sleep(wait)
        raise RuntimeError("unreachable")


def reader_from_env() -> Optional[WalletReader]:
    """Same model configuration as receipts (Gemini API key or Vertex); None when neither is set."""
    from receipts.ocr import reader_from_env as receipt_reader

    r = receipt_reader()
    return GeminiWalletReader(r._client, r.model) if r else None


class FakeWalletReader:
    model = "fake"

    def __init__(self, reading: WalletReading):
        self.reading = reading
        self.calls = 0

    def read(self, image, mime_type, wallet, progress=None):
        self.calls += 1
        if progress:
            progress("reading", attempt=1, attempts=1)
        return self.reading
