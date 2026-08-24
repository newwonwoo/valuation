from __future__ import annotations

from email.message import Message

import pytest

import valuation_engine.live_indexers as live_indexers
from valuation_engine.live_indexers import HttpTransport, SourceFetchError


class FakeResponse:
    def __init__(
        self,
        content: bytes,
        *,
        status: int = 200,
        content_type: str = "application/octet-stream",
    ):
        self._content = content
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, limit: int) -> bytes:
        return self._content[:limit]


def test_http_transport_fetches_bounded_binary_and_text(monkeypatch):
    responses = [
        FakeResponse(b"PK\x03\x04binary"),
        FakeResponse(
            "한글".encode("utf-8"),
            content_type="text/plain; charset=utf-8",
        ),
    ]

    def fake_urlopen(request, timeout):
        assert timeout == 3
        assert request.headers["User-agent"].startswith("RocketSLA")
        return responses.pop(0)

    monkeypatch.setattr(live_indexers, "urlopen", fake_urlopen)
    transport = HttpTransport(timeout_seconds=3, max_bytes=1024, retries=0)
    binary = transport.get_bytes("https://example.test/archive?api_key=SECRET")
    text = transport.get_text("https://example.test/text")
    assert binary.content == b"PK\x03\x04binary"
    assert text.text == "한글"


def test_http_transport_rejects_oversized_response(monkeypatch):
    monkeypatch.setattr(
        live_indexers,
        "urlopen",
        lambda request, timeout: FakeResponse(b"12345"),
    )
    transport = HttpTransport(max_bytes=4, retries=0)
    with pytest.raises(SourceFetchError, match="SourceFetchError"):
        transport.get_bytes("https://example.test/large")


def test_http_transport_error_does_not_expose_query_credentials(monkeypatch):
    def fail(request, timeout):
        raise TimeoutError("Authorization: Bearer TOP-SECRET")

    monkeypatch.setattr(live_indexers, "urlopen", fail)
    transport = HttpTransport(retries=0)
    with pytest.raises(SourceFetchError) as caught:
        transport.get_text(
            "https://example.test/data?api_key=TOP-SECRET&token=OTHER"
        )
    message = str(caught.value)
    assert message == "fetch failed for https://example.test/data (TimeoutError)"
    assert "TOP-SECRET" not in message
    assert "token=" not in message


def test_http_transport_validates_bounds():
    with pytest.raises(ValueError, match="timeout_seconds"):
        HttpTransport(timeout_seconds=0)
    with pytest.raises(ValueError, match="max_bytes"):
        HttpTransport(max_bytes=0)
    with pytest.raises(ValueError, match="retries"):
        HttpTransport(retries=-1)
