"""The operating-segment note, read from two real Daehan filings.

The fixtures are the verbatim DART viewer sections for 대한제강's FY2025 annual
report (rcept 20260318000780, 35. 영업부문 정보) and its H1-2026 half-year
report (rcept 20260814003201, 21. 영업부문 정보). Both are the layout the
engine will actually meet, including the empty-cell shift that moves the
영업이익 row's later columns — the reason every metric row is reconciled
against its own segment total rather than read positionally.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from valuation_engine.segment_note import (
    OperatingSegmentDisclosure,
    SegmentNoteEntry,
    SegmentNoteError,
    parse_operating_segment_note,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


def test_the_fy2025_annual_note_yields_three_reconciled_segments():
    disclosure = parse_operating_segment_note(
        _fixture("daehan_segment_note_fy2025.xml")
    )
    assert disclosure.segment_names == ("제강부문", "운송부문", "기타부문")
    assert disclosure.entries[0].revenue == Decimal("1298094658912")
    assert disclosure.entries[1].revenue == Decimal("10548621593")
    assert disclosure.entries[2].revenue == Decimal("14152979804")
    assert disclosure.total_revenue == Decimal("1322796260309")

    # The 영업이익 row is where a positional read would go wrong: the filing
    # drops two empty cells, so the adjustment and consolidated figures sit two
    # columns early. Reading the named segment columns and reconciling to the
    # row's own 부문 합계 keeps the loss figures exact.
    assert disclosure.entries[0].operating_income == Decimal("-2344801515")
    assert disclosure.entries[1].operating_income == Decimal("-412094312")
    assert disclosure.entries[2].operating_income == Decimal("-545871325")
    assert disclosure.total_operating_income == Decimal("-3302767152")


def test_the_h1_2026_half_year_note_reads_the_same_segments():
    """The half-year note labels its rows 수익 / 영업이익(손실) rather than
    수익(매출액) / 영업이익, and heads its group 부문의 합계 rather than
    기업 전체 총계 — the same three segments must still come back."""
    disclosure = parse_operating_segment_note(
        _fixture("daehan_segment_note_h1_2026.xml")
    )
    assert disclosure.segment_names == ("제강부문", "운송부문", "기타부문")
    assert disclosure.entries[0].revenue == Decimal("717620206316")
    assert disclosure.entries[0].operating_income == Decimal("18678018398")
    assert disclosure.entries[2].operating_income == Decimal("-914683852")
    assert disclosure.total_revenue == Decimal("749035966727")
    assert disclosure.total_operating_income == Decimal("17789381504")


def test_the_current_period_is_read_not_the_comparative():
    """Both notes carry 당기 then 전기 with identical shape; the parser must
    return the current period. FY2025's comparative revenue for 제강부문 is
    1,411,391,455,844 — reading it would be a year-old number silently."""
    disclosure = parse_operating_segment_note(
        _fixture("daehan_segment_note_fy2025.xml")
    )
    assert disclosure.entries[0].revenue != Decimal("1411391455844")


def test_a_note_without_a_segment_table_is_refused():
    with pytest.raises(SegmentNoteError, match="no reconciled operating-segment"):
        parse_operating_segment_note("<p>주석 21. 우발부채</p>")


def test_a_row_that_does_not_reconcile_is_refused_not_repaired():
    """The reconciliation is the whole safety argument: if the segment values
    do not sum to the disclosed segment total, the columns were misread and the
    disclosure must fail closed rather than hand back a plausible number."""
    disclosure = OperatingSegmentDisclosure(
        entries=(
            SegmentNoteEntry("제강부문", Decimal("100"), Decimal("10")),
            SegmentNoteEntry("기타부문", Decimal("20"), Decimal("2")),
        ),
        total_revenue=Decimal("130"),
        total_operating_income=Decimal("12"),
    )
    with pytest.raises(SegmentNoteError, match="do not sum"):
        disclosure.validate()


def test_a_single_segment_disclosure_is_not_a_multi_segment_case():
    disclosure = OperatingSegmentDisclosure(
        entries=(SegmentNoteEntry("제강부문", Decimal("100"), Decimal("10")),),
        total_revenue=Decimal("100"),
        total_operating_income=Decimal("10"),
    )
    with pytest.raises(SegmentNoteError, match="at least two"):
        disclosure.validate()
