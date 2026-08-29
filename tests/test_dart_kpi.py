from datetime import date
from decimal import Decimal
from hashlib import sha256
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
    pattern: str = r"수주잔고\s+(?P<value>[0-9,]+)\s+(?P<unit>백만원)",
    unit: str = "KRW_million",
    effective_date: str = "2026-06-30",
    source_unit_map: tuple[tuple[str, str], ...] = (("백만원", "KRW_million"),),
) -> DartKPIExtractionSpec:
    return DartKPIExtractionSpec(
        metric=metric,
        segment=segment,
        member_path_pattern=path,
        value_pattern=pattern,
        canonical_unit=unit,
        effective_date=effective_date,
        locator_label="사업보고서 수주상황 표의 수주잔고",
        source_unit_map=source_unit_map,
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
    assert observation.source_unit_token == "백만원"
    assert observation.source_unit == "KRW_million"
    assert "수주잔고 12,345 백만원" in observation.matched_text
    assert len(observation.observation_hash) == 64


def test_canonical_unit_is_converted_from_matched_source_unit():
    document = filing(
        ("report.xml", "<p>수주잔고 12,345 백만원</p>"),
    )
    observation = extract_dart_kpi(
        document,
        spec(unit="KRW_billion"),
    )
    assert observation.source_unit == "KRW_million"
    assert observation.measure.unit == "KRW_billion"
    assert observation.measure.amount == Decimal("12.345")


def test_unmapped_source_unit_token_fails_closed():
    document = filing(
        ("report.xml", "<p>수주잔고 12,345 억원</p>"),
    )
    with pytest.raises(DartKPIExtractionError, match="source unit token is not mapped"):
        extract_dart_kpi(
            document,
            spec(
                pattern=r"수주잔고\s+(?P<value>[0-9,]+)\s+(?P<unit>억원)",
            ),
        )


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
    assert evidence.value == 12345
    assert evidence.unit == "KRW_million"
    assert evidence.effective_date == "2026-06-30"
    assert evidence.segment == "core"
    assert "member=report.xml" in evidence.source_ref
    assert "member_sha256=" in evidence.source_ref
    assert "normalization=DART_VISIBLE_TEXT_V1" in evidence.source_ref
    assert "normalized_sha256=" in evidence.source_ref
    assert "normalized_span=" in evidence.source_ref
    assert evidence.id.startswith(f"DARTKPI:{RCEPT_NO}:core:backlog:")
    assert evidence.critical


def test_normalized_span_is_bound_to_hashed_normalized_representation():
    document = filing(
        (
            "report.xml",
            "<root><header>앞 문구</header><table><tr><td>수주잔고</td>"
            "<td>12,345</td><td>백만원</td></tr></table></root>",
        )
    )
    observation = extract_dart_kpi(document, spec())
    normalized = "앞 문구 수주잔고 12,345 백만원"
    assert observation.normalization_version == "DART_VISIBLE_TEXT_V1"
    assert observation.normalized_text_hash == sha256(
        normalized.encode("utf-8")
    ).hexdigest()
    assert normalized[observation.text_start : observation.text_end] == observation.matched_text
    assert observation.member_content_hash != observation.normalized_text_hash


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


def test_value_pattern_must_explicitly_capture_value_unit_and_exact_effective_date():
    with pytest.raises(DartKPIExtractionError, match="named.*value.*unit"):
        spec(pattern=r"수주잔고\s+(?P<value>[0-9,]+)\s+백만원").validate()

    with pytest.raises(DartKPIExtractionError, match="effective_date"):
        spec(effective_date="2026-Q2").validate()

    with pytest.raises(DartKPIExtractionError, match="exact YYYY-MM-DD"):
        spec(effective_date="2026-06-30junk").validate()


def test_observed_date_requires_complete_iso_date():
    document = filing(("report.xml", "<p>수주잔고 12,345 백만원</p>"))
    observation = extract_dart_kpi(document, spec())
    with pytest.raises(DartKPIExtractionError, match="observed_date.*exact YYYY-MM-DD"):
        dart_kpi_observation_to_evidence(
            observation,
            target_id="KR:DART:00000000",
            observed_date="2026-08-24junk",
        )


def test_strict_decimal_parser_supports_parenthesized_negative_value():
    document = filing(
        ("report.xml", "<p>순현금조정 (1,250) 백만원</p>"),
    )
    observation = extract_dart_kpi(
        document,
        spec(
            metric="net_cash_adjustment",
            pattern=(
                r"순현금조정\s+(?P<value>\([0-9,]+\))\s+"
                r"(?P<unit>백만원)"
            ),
        ),
    )
    assert observation.measure.amount == Decimal("-1250")


def test_source_unit_mapping_must_use_registered_compatible_units():
    with pytest.raises(DartKPIExtractionError, match="unit mapping is invalid"):
        spec(
            metric="capacity",
            pattern=r"생산능력\s+(?P<value>[0-9,]+)\s+(?P<unit>톤)",
            unit="count",
            source_unit_map=(("톤", "tonnes_guessed"),),
        ).validate()
