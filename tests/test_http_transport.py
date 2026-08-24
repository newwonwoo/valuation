from email.message import Message

import pytest

from valuation_engine.live_indexers import HttpTransport, SourceFetchError


class FakeResponse:
    def __init__(self, payload: bytes, *, content_type: str):
        self._payload = payload
        self.status = 200
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def read(self, size: int) -> bytes:
        return self._payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_http_transport_get_bytes_preserves_binary_payload(monkeypatch):
    payload = b"PK\x03\x04binary-zip"
    monkeypatch.setattr(
        "valuation_engine.live_indexers.urlopen",
        lambda request, timeout: FakeResponse(
            payload,
            content_type="application/zip",
        ),
    )
    transport = HttpTransport(timeout_seconds=1, max_bytes=1024, retries=0)
    response = transport.get_bytes("https://example.test/archive.zip")
    assert response.body == payload
    assert response.status == 200
    assert response.content_type == "application/zip"


def test_http_transport_get_text_uses_declared_charset(monkeypatch):
    text = "생산설비"
    monkeypatch.setattr(
        "valuation_engine.live_indexers.urlopen",
        lambda request, timeout: FakeResponse(
            text.encode("euc-kr"),
            content_type="text/plain; charset=EUC-KR",
        ),
    )
    transport = HttpTransport(timeout_seconds=1, max_bytes=1024, retries=0)
    response = transport.get_text("https://example.test/report.txt")
    assert response.text == text


def test_http_transport_blocks_responses_above_byte_limit(monkeypatch):
    payload = b"x" * 11
    monkeypatch.setattr(
        "valuation_engine.live_indexers.urlopen",
        lambda request, timeout: FakeResponse(
            payload,
            content_type="application/octet-stream",
        ),
    )
    transport = HttpTransport(timeout_seconds=1, max_bytes=10, retries=0)
    with pytest.raises(SourceFetchError, match="max_bytes=10"):
        transport.get_bytes("https://example.test/too-large")


def test_http_transport_rejects_invalid_limits():
    with pytest.raises(ValueError, match="HTTP transport limits"):
        HttpTransport(timeout_seconds=0)
