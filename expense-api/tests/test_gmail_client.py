import httpx
import pytest

from sync.gmail_client import GmailServiceClient, MailError


def client(handler, retries=3):
    return GmailServiceClient("http://gmail", retries=retries, backoff=0,
                              transport=httpx.MockTransport(handler))


def test_list_passes_params_and_returns_token():
    seen = {}

    def handler(req):
        seen.update(dict(req.url.params))
        return httpx.Response(200, json={"emails": [{"id": "a"}, {"id": "b"}], "next_page_token": "t2"})

    ids, tok = client(handler).list_message_ids("financial-kasikorn", "2026/07/01", "from:x", "t1")
    assert (ids, tok) == (["a", "b"], "t2")
    assert seen["labels"] == "financial-kasikorn" and seen["page_token"] == "t1"
    assert seen["date_after"] == "2026/07/01" and seen["search_text"] == "from:x"


def test_retries_5xx_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(500) if len(calls) < 3 else httpx.Response(200, json={"id": "m"})

    assert client(handler).get_message("m") == {"id": "m"}
    assert len(calls) == 3


def test_gives_up_after_retries():
    with pytest.raises(MailError):
        client(lambda r: httpx.Response(500), retries=2).get_message("m")


def test_4xx_is_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(404)

    with pytest.raises(MailError):
        client(handler).get_message("m")
    assert len(calls) == 1


def test_attachment_bytes():
    assert client(lambda r: httpx.Response(200, content=b"%PDF")).get_attachment("m", "a") == b"%PDF"
