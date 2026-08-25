from dataclasses import replace
from datetime import date
from io import BytesIO
from zipfile import ZipFile

import pytest

from valuation_engine.dart_documents import (
    DartDocumentError,
    fetch_indexed_opendart_original_document,
    parse_opendart_original_document_archive,
)
from valuation_engine.source_index import DocumentIndexRecord


RCEPT_NO = "20260824001234"
CHECKED_AT = date(2026, 8, 24)


def _payload(text: str = "<DOCUMENT><TITLE>사업보고서</TITLE></DOCUMENT>") -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("report.xml", text.encode("utf-8"))
    return buffer.getvalue()


def test_indexed_original_document_requires_published_at_before_fetch():
    record = DocumentIndexRecord(
        source_id="KR_OPENDART",
        document_id=f"DART_{RCEPT_NO}",
        title="사업보고서",
        published_at=None,
        url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={RCEPT_NO}",
        document_class="regulatory_filing",
        locator=RCEPT_NO,
    )
    called = []

    with pytest.raises(DartDocumentError, match="requires published_at"):
        fetch_indexed_opendart_original_document(
            lambda url: called.append(url) or _payload(),
            record,
            checked_at=CHECKED_AT,
            api_key="TEST_KEY",
        )
    assert called == []


def test_document_validation_reproduces_decoded_members_from_retained_zip():
    result = parse_opendart_original_document_archive(
        _payload(),
        rcept_no=RCEPT_NO,
        checked_at=CHECKED_AT,
    )
    original = result.members[0]

    with pytest.raises(DartDocumentError, match="decoded text mismatch"):
        replace(
            result,
            members=(replace(original, text="<DOCUMENT>TAMPERED</DOCUMENT>"),),
        ).validate()

    with pytest.raises(DartDocumentError, match="content hash mismatch"):
        replace(
            result,
            members=(replace(original, content_hash="0" * 64),),
        ).validate()
