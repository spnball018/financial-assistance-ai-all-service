"""Port to Gmail (via gmail-service) used by the sync engine, plus an in-memory fake for tests."""
import time
from typing import Optional, Protocol

import httpx

PAGE_SIZE = 50  # gmail-service returns 500 intermittently on large pages


class MailError(Exception):
    pass


class MailClient(Protocol):
    def list_message_ids(self, label: str, date_after: str, search_text: Optional[str] = None,
                         page_token: Optional[str] = None) -> tuple[list[str], Optional[str]]:
        """One page of message ids (newest first) and the next page token. date_after is YYYY/MM/DD."""

    def get_message(self, message_id: str) -> dict:
        """gmail-service GET /api/messages/{id} payload."""

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes: ...


class GmailServiceClient:
    def __init__(self, base_url: str, *, access_token: Optional[str] = None, retries: int = 5, backoff: float = 2.0,
                 transport: Optional[httpx.BaseTransport] = None):
        """access_token: read that user's mailbox; without it gmail-service uses its token.json."""
        headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=60, transport=transport, headers=headers)
        self._retries = retries
        self._backoff = backoff

    def _get(self, path: str, **params) -> httpx.Response:
        last: Optional[Exception] = None
        for attempt in range(self._retries):
            try:
                r = self._http.get(path, params={k: v for k, v in params.items() if v is not None})
                if r.status_code < 500:
                    r.raise_for_status()
                    return r
                last = MailError(f"gmail-service {path} returned {r.status_code}")
            except httpx.HTTPStatusError as e:  # 4xx: retrying will not help
                raise MailError(f"gmail-service {path} returned {e.response.status_code}") from None
            except httpx.TransportError as e:
                last = e
            time.sleep(self._backoff * (attempt + 1))
        raise MailError(f"gmail-service {path} failed after {self._retries} attempts: {last}")

    def list_message_ids(self, label, date_after, search_text=None, page_token=None):
        data = self._get("/api/emails", labels=label, date_after=date_after, search_text=search_text,
                         max_results=PAGE_SIZE, page_token=page_token).json()
        return [e["id"] for e in data["emails"]], data.get("next_page_token")

    def get_message(self, message_id):
        return self._get(f"/api/messages/{message_id}").json()

    def get_attachment(self, message_id, attachment_id):
        return self._get(f"/api/messages/{message_id}/attachments/{attachment_id}").content


class FakeMailClient:
    """Serves messages from memory. `messages` maps id -> gmail-service message dict."""

    def __init__(self, messages: dict[str, dict], attachments: Optional[dict[tuple[str, str], bytes]] = None):
        self.messages = messages
        self.attachments = attachments or {}
        self.fetched: list[str] = []  # ids passed to get_message, to assert nothing is re-fetched

    def list_message_ids(self, label, date_after, search_text=None, page_token=None):
        return list(self.messages), None

    def get_message(self, message_id):
        self.fetched.append(message_id)
        return self.messages[message_id]

    def get_attachment(self, message_id, attachment_id):
        return self.attachments[(message_id, attachment_id)]
