from datetime import date
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile

import pytest

from valuation_engine.dart_documents import parse_opendart_original_document_archive
from valuation_engine.dart_kpi import (
    DartKPIExtractionError,
    DartKPIExtractionSpec,
    dart_kpi_observation_to_evidence,
    extract_dart_kpi,
)
from valuation_engine.records import EvidenceSourceLayer


RCEPT_NO = "20260824001234"


def filing(*texts: tuple[str, str]):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for path, text in texts:
            archive.writestr(path, text.encode("utf-8"))
    return parse_opendart_original_document_archive(
        buffer.getvalue(),
        rcept_no=RCEPT_NO,
        checked_at=date(2026, 8, 24),
    )


def spec(
    *,
    metric: str = "backlog",
    segment: str = "core",
    path: str = r"report\.xml",
    pattern: str = r"수주잔고\s+(?P<value>[0-9,]+)\s+백만원",
    unit: str = "KRW_million",
    effective_date: str = "2026-06-30",
) -> DartKPIExtractionSpec:
    return DartKPIExtractionSpec(
        metric=metric,
        segment=segment,
        member_path_pattern=path,
        value_pattern=pattern,
        canonical_unit=unit,
        effective_date=effective_date,
        locator_label="사업보고서 수주상황 표의 수주잔고",
        critical=True,
    )


def test_exact_kpi_extraction_from_normalized_filing_text():
    document = filing(
        (
            "report.xml",
            """<DOCUMENT><SECTION>수주상황</SECTION>
            <TABLE><TR><TD>수주잔고</TD><TD>12,345</TD><TD>백만원</TD></TR></TABLE>
            </DOCUMENT>""",
        )
    )
    observation = extract_dart_kpi(document, spec())
    assert observation.metric == "backlog"
    assert observation.segment == "core"
    assert observation.measure.amount == Decimal("12345")
    assert observation.measure.unit == "KRW_million"
    assert observation.measure.as_of == "2026-06-30"
    assert observation.member_path == "report.xml"
    assert observation.rcept_no == RCEPT_NO
    assert "수주잔고 12,345 백만원" in observation.matched_text
    assert len(observation.observation_hash) == 64


def test_kpi_observation_becomes_filing_evidence_not_assumption():
    document = filing(
        ("report.xml", "<p>수주잔고 12,345 백만원</p>"),
    )
    observation = extract_dart_kpi(document, spec())
    evidence = dart_kpi_observation_to_evidence(
        observation,
        target_id="KR:DART:00000000",
        observed_date="2026-08-24",
    )
    assert evidence.source_layer is EvidenceSourceLayer.REALIZED_OR_FILING
    assert evidence.value == Decimal("12345")
    assert evidence.unit == "KRW_million"
    assert evidence.effective_date == "2026-06-30"
    assert evidence.segment == "core"
    assert "member=report.xml" in evidence.source_ref
    assert "member_sha256=" in evidence.source_ref
    assert evidence.id.startswith(f"DARTKPI:{RCEPT_NO}:core:backlog:")
    assert evidence.critical


def test_extraction_fails_closed_on_zero_or_multiple_matches():
    no_match = filing(("report.xml", "<p>수주총액 12,345 백만원</p>"))
    with pytest.raises(DartKPIExtractionError, match="matched no filing location"):
        extract_dart_kpi(no_match, spec())

    ambiguous = filing(
        (
            "report.xml",
            "<p>수주잔고 12,345 백만원</p><p>수주잔고 13,000 백만원</p>",
        )
    )
    with pytest.raises(DartKPIExtractionError, match="ambiguous"):
        extract_dart_kpi(ambiguous, spec())


def test_extraction_does_not_cross_unplanned_member_paths():
    document = filing(
        ("report.xml", "<p>수주잔고 12,345 백만원</p>"),
        ("attachment.xml", "<p>수주잔고 99,999 백만원</p>"),
    )
    observation = extract_dart_kpi(document, spec(path=r"report\.xml"))
    assert observation.measure.amount == Decimal("12345")

    with pytest.raises(DartKPIExtractionError, match="ambiguous"):
        extract_dart_kpi(document, spec(path=r".*\.xml"))


def test_value_pattern_must_explicitly_capture_value_and_effective_date():
    with pytest.raises(DartKPIExtractionError, match="named.*value"):
        spec(pattern=r"수주잔고\s+[0-9,]+\s+백만원").validate()

    with pytest.raises(DartKPIExtractionError, match="effective_date"):
        spec(effective_date="2026-Q2").validate()


def test_strict_decimal_parser_supports_parenthesized_negative_value():
    document = filing(
        ("report.xml", "<p>순현금조정 (1,250) 백만원</p>"),
    )
    observation = extract_dart_kpi(
        document,
        spec(
            metric="net_cash_adjustment",
            pattern=r"순현금조정\s+(?P<value>\([0-9,]+\))\s+백만원",
        ),
    )
    assert observation.measure.amount == Decimal("-1250")


def test_pattern_cannot_convert_unknown_unit_by_convenience():
    document = filing(
        ("report.xml", "<p>생산능력 1,200 톤</p>"),
    )
    with pytest.raises(ValueError, match="unsupported unit"):
        extract_dart_kpi(
            document,
            spec(
                metric="capacity",
                pattern=r"생산능력\s+(?P<value>[0-9,]+)\s+톤",
                unit="tonnes_guessed",
            ),
        )
