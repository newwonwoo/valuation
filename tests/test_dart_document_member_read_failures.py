from datetime import date

import pytest

from valuation_engine.dart_documents import (
    DartDocumentFetchError,
    parse_opendart_original_document_archive,
)


class _Info:
    filename = "report.xml"
    flag_bits = 0
    file_size = 10
    compress_size = 10

    def is_dir(self):
        return False


class _UnreadableArchive:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def infolist(self):
        return [_Info()]

    def read(self, info):
        raise NotImplementedError("unsupported compression method")


def test_member_read_failure_is_normalized_to_document_fetch_error(monkeypatch):
    monkeypatch.setattr(
        "valuation_engine.dart_documents.ZipFile",
        lambda stream: _UnreadableArchive(),
    )

    with pytest.raises(DartDocumentFetchError, match="member cannot be read: report.xml"):
        parse_opendart_original_document_archive(
            b"not-used-by-fake-zip",
            rcept_no="20260824001234",
            checked_at=date(2026, 8, 24),
        )
