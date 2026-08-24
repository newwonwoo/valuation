from dataclasses import replace
from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO
from urllib.parse import parse_qs, urlparse
from zipfile import ZipFile

import pytest

from valuation_engine.dart_documents import (
    DartDocumentError,
    DartDocumentFetchError,
    DartDocumentFetchPolicy,
    build_opendart_document_url,
    fetch_indexed_opendart_original_document,
    fetch_opendart_original_document,
    opendart_document_source_ref,
    parse_opendart_original_document_archive,
)
from valuation_engine.source_index import DocumentIndexRecord


RCEPT_NO = "20260824001234"
CHECKED_AT = date(2026, 8, 24)


def zip_payload(entries: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for path, payload in entries.items():
            archive.writestr(path, payload)
    return buffer.getvalue()


def filing_record(
    *,
    document_id: str | None = None,
    locator: str | None = RCEPT_NO,
    published_at: date | None = CHECKED_AT,
    content_fingerprint: str | None = "INDEX-HASH",
):
    return DocumentIndexRecord(
        source_id="KR_OPENDART",
        document_id=document_id or f"DART_{RCEPT_NO}",
        title="사업보고서 (2026.12)",
        published_at=published_at,
        url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={RCEPT_NO}",
        document_class="regulatory_filing",
        period="사업보고서 (2026.12)",
        locator=locator,
        content_fingerprint=content_fingerprint,
    )


def test_document_url_uses_official_binary_endpoint_and_source_ref_hides_api_key():
    url = build_opendart_document_url(
        rcept_no=RCEPT_NO,
        api_key="SECRET_API_KEY",
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "opendart.fss.or.kr"
    assert parsed.path == "/api/document.xml"
    assert query["rcept_no"] == [RCEPT_NO]
    assert query["crtfc_key"] == ["SECRET_API_KEY"]

    source_ref = opendart_document_source_ref(RCEPT_NO)
    assert "SECRET_API_KEY" not in source_ref
    assert f"rcept_no={RCEPT_NO}" in source_ref


def test_original_document_zip_builds_hash_bound_text_manifest_and_retains_raw_archive():
    payload = zip_payload(
        {
            "report.xml": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<DOCUMENT><TITLE>사업보고서</TITLE></DOCUMENT>"
            ).encode("utf-8"),
            "notes/note.html": "<html><body>수주상황</body></html>".encode("utf-8"),
            "images/logo.png": b"PNG-BYTES",
        }
    )
    result = parse_opendart_original_document_archive(
        payload,
        rcept_no=RCEPT_NO,
        checked_at=CHECKED_AT,
    )

    assert result.rcept_no == RCEPT_NO
    assert len(result.members) == 3
    assert tuple(member.path for member in result.text_members) == (
        "notes/note.html",
        "report.xml",
    )
    assert "수주상황" in result.text_members[0].text
    assert result.archive_bytes == payload
    assert result.archive_size_bytes == len(payload)
    assert result.archive_hash == sha256(payload).hexdigest()
    assert len(result.manifest_hash) == 64
    assert result.source_ref == opendart_document_source_ref(RCEPT_NO)


def test_caller_supplied_source_ref_is_credential_redacted_and_receipt_bound():
    payload = zip_payload({"report.xml": b"<DOCUMENT/>"})
    credential_url = build_opendart_document_url(
        rcept_no=RCEPT_NO,
        api_key="SECRET_API_KEY",
    )
    result = parse_opendart_original_document_archive(
        payload,
        rcept_no=RCEPT_NO,
        checked_at=CHECKED_AT,
        source_ref=credential_url + "&token=ANOTHER_SECRET",
    )
    assert "SECRET_API_KEY" not in result.source_ref
    assert "ANOTHER_SECRET" not in result.source_ref
    assert f"rcept_no={RCEPT_NO}" in result.source_ref

    mismatched = credential_url.replace(RCEPT_NO, "20260824009999")
    with pytest.raises(DartDocumentError, match="receipt number"):
        parse_opendart_original_document_archive(
            payload,
            rcept_no=RCEPT_NO,
            checked_at=CHECKED_AT,
            source_ref=mismatched,
        )


def test_original_document_supports_explicit_korean_xml_encoding():
    text = (
        '<?xml version="1.0" encoding="EUC-KR"?>'
        "<DOCUMENT><TITLE>생산설비</TITLE></DOCUMENT>"
    )
    payload = zip_payload({"report.xml": text.encode("euc-kr")})
    result = parse_opendart_original_document_archive(
        payload,
        rcept_no=RCEPT_NO,
        checked_at=CHECKED_AT,
    )
    member = result.text_members[0]
    assert member.text_encoding == "euc-kr"
    assert "생산설비" in member.text


def test_original_document_rejects_unknown_declared_codec_as_fetch_failure():
    payload = zip_payload(
        {
            "report.xml": (
                '<?xml version="1.0" encoding="made-up"?>'
                "<DOCUMENT/>"
            ).encode("ascii")
        }
    )
    with pytest.raises(DartDocumentFetchError, match="unsupported encoding"):
        parse_opendart_original_document_archive(
            payload,
            rcept_no=RCEPT_NO,
            checked_at=CHECKED_AT,
        )


def test_original_document_rejects_path_traversal_and_duplicate_paths():
    traversal = zip_payload({"../escape.xml": b"<root/>"})
    with pytest.raises(DartDocumentFetchError, match="unsafe"):
        parse_opendart_original_document_archive(
            traversal,
            rcept_no=RCEPT_NO,
            checked_at=CHECKED_AT,
        )

    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("same.xml", b"<a/>")
        archive.writestr("same.xml", b"<b/>")
    with pytest.raises(DartDocumentFetchError, match="duplicate member path"):
        parse_opendart_original_document_archive(
            buffer.getvalue(),
            rcept_no=RCEPT_NO,
            checked_at=CHECKED_AT,
        )


def test_original_document_rejects_archive_and_zip_bomb_style_limits():
    payload = zip_payload({"report.xml": b"x" * 1024})
    with pytest.raises(DartDocumentFetchError, match="max_archive_bytes"):
        parse_opendart_original_document_archive(
            payload,
            rcept_no=RCEPT_NO,
            checked_at=CHECKED_AT,
            policy=DartDocumentFetchPolicy(max_archive_bytes=len(payload) - 1),
        )

    with pytest.raises(DartDocumentFetchError, match="max_member_bytes"):
        parse_opendart_original_document_archive(
            payload,
            rcept_no=RCEPT_NO,
            checked_at=CHECKED_AT,
            policy=DartDocumentFetchPolicy(
                max_files=10,
                max_archive_bytes=10_000,
                max_member_bytes=100,
                max_total_uncompressed_bytes=1000,
                max_compression_ratio=500,
            ),
        )


def test_original_document_surfaces_opendart_error_xml_instead_of_bad_zip():
    payload = (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<result><status>014</status><message>파일이 존재하지 않습니다.</message></result>"
    ).encode("utf-8")
    with pytest.raises(DartDocumentFetchError, match="status=014"):
        parse_opendart_original_document_archive(
            payload,
            rcept_no=RCEPT_NO,
            checked_at=CHECKED_AT,
        )


def test_fetcher_requires_binary_transport_and_does_not_persist_credential():
    seen = []

    def fetch_bytes(url: str) -> bytes:
        seen.append(url)
        return zip_payload({"report.xml": b"<DOCUMENT/>"})

    result = fetch_opendart_original_document(
        fetch_bytes,
        rcept_no=RCEPT_NO,
        checked_at=CHECKED_AT,
        api_key="SECRET_API_KEY",
    )
    assert seen and "SECRET_API_KEY" in seen[0]
    assert "SECRET_API_KEY" not in result.source_ref

    with pytest.raises(DartDocumentFetchError, match="must return bytes"):
        fetch_opendart_original_document(
            lambda _: "not-bytes",  # type: ignore[return-value]
            rcept_no=RCEPT_NO,
            checked_at=CHECKED_AT,
            api_key="SECRET_API_KEY",
        )


def test_indexed_document_fetch_is_bound_to_exact_receipt_source_dates_and_index_snapshot():
    payload = zip_payload({"report.xml": b"<DOCUMENT/>"})
    record = filing_record()
    result = fetch_indexed_opendart_original_document(
        lambda _: payload,
        record,
        checked_at=CHECKED_AT,
        api_key="SECRET_API_KEY",
    )
    assert result.rcept_no == RCEPT_NO
    assert result.published_at == CHECKED_AT
    assert result.checked_at == CHECKED_AT
    assert result.index_record_hash is not None
    manifest_hash = result.manifest_hash

    changed_record = replace(record, content_fingerprint="INDEX-HASH-CHANGED")
    changed = fetch_indexed_opendart_original_document(
        lambda _: payload,
        changed_record,
        checked_at=CHECKED_AT,
        api_key="SECRET_API_KEY",
    )
    assert changed.archive_hash == result.archive_hash
    assert changed.index_record_hash != result.index_record_hash
    assert changed.manifest_hash != manifest_hash

    future_record = filing_record(published_at=CHECKED_AT + timedelta(days=1))
    with pytest.raises(DartDocumentError, match="publication date"):
        fetch_indexed_opendart_original_document(
            lambda _: payload,
            future_record,
            checked_at=CHECKED_AT,
            api_key="SECRET_API_KEY",
        )

    wrong_id = filing_record(document_id="DART_20260824009999")
    with pytest.raises(DartDocumentError, match="document_id does not match"):
        fetch_indexed_opendart_original_document(
            lambda _: payload,
            wrong_id,
            checked_at=CHECKED_AT,
            api_key="SECRET_API_KEY",
        )

    wrong_source = DocumentIndexRecord(
        source_id="US_SEC",
        document_id=f"DART_{RCEPT_NO}",
        title="filing",
        published_at=CHECKED_AT,
        url="https://example.invalid",
        document_class="regulatory_filing",
        locator=RCEPT_NO,
    )
    with pytest.raises(DartDocumentError, match="KR_OPENDART"):
        fetch_indexed_opendart_original_document(
            lambda _: payload,
            wrong_source,
            checked_at=CHECKED_AT,
            api_key="SECRET_API_KEY",
        )


def test_retained_archive_validation_detects_tampering():
    payload = zip_payload({"report.xml": b"<DOCUMENT/>"})
    result = parse_opendart_original_document_archive(
        payload,
        rcept_no=RCEPT_NO,
        checked_at=CHECKED_AT,
    )
    with pytest.raises(DartDocumentError, match="retained archive hash"):
        replace(result, archive_bytes=payload + b"tamper", archive_size_bytes=len(payload) + 6).validate()


def test_receipt_number_must_be_exactly_fourteen_digits():
    with pytest.raises(DartDocumentError, match="14 digits"):
        build_opendart_document_url(
            rcept_no="1234",
            api_key="SECRET_API_KEY",
        )
