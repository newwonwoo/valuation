from datetime import date
from io import BytesIO
from zipfile import ZipFile

from valuation_engine.dart_documents import (
    DartDocumentFetchPolicy,
    build_opendart_document_url,
    parse_opendart_original_document_archive,
)


RCEPT_NO = "20260824001234"


def _zip() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("report.xml", b"<DOCUMENT/>")
    return buffer.getvalue()


def test_direct_parser_retains_reproducible_raw_bytes_without_credentials():
    payload = _zip()
    source_ref = build_opendart_document_url(
        rcept_no=RCEPT_NO,
        api_key="SECRET_KEY",
    )
    result = parse_opendart_original_document_archive(
        payload,
        rcept_no=RCEPT_NO,
        checked_at=date(2026, 8, 24),
        source_ref=source_ref,
        policy=DartDocumentFetchPolicy(max_archive_bytes=len(payload)),
    )

    assert result.archive_bytes == payload
    assert result.archive_size_bytes == len(payload)
    assert "SECRET_KEY" not in result.source_ref
    assert result.rcept_no in result.source_ref
    result.validate()
