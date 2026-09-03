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


# ------------------------------------- the parts, the whole, and the residual


def test_the_elimination_between_parts_and_whole_is_carried_not_dropped():
    """대한제강 FY2025 sums its three segments to 1,322,796,260,309 KRW of
    revenue while the consolidated income statement reports
    1,247,058,624,052 — a 75.7bn inter-segment elimination. A sum-of-the-parts
    that ignored it would inflate the company by that amount with nothing in
    the output to show for it, so the reconciliation computes and keeps it."""
    from decimal import Decimal as D

    from valuation_engine.segment_note import reconcile_segments

    disclosure = parse_operating_segment_note(
        _fixture("daehan_segment_note_fy2025.xml")
    )
    reconciliation = reconcile_segments(
        disclosure,
        # Both figures are the filed consolidated statement's own
        # (fnltt_2025_CFS: 매출액 / 영업이익, dart CIS rows).
        consolidated_revenue=D("1247058624052"),
        consolidated_operating_income=D("-2872504765"),
    )
    assert reconciliation.revenue_elimination == D("-75737636257")
    assert reconciliation.operating_income_elimination == D("430262387")
    # The identity that makes the parts complete: parts + elimination = whole.
    assert (
        disclosure.total_revenue + reconciliation.revenue_elimination
        == reconciliation.consolidated_revenue
    )
    assert (
        disclosure.total_operating_income
        + reconciliation.operating_income_elimination
        == reconciliation.consolidated_operating_income
    )


def test_reconciliation_refuses_a_whole_it_cannot_anchor_to():
    from decimal import Decimal as D

    from valuation_engine.segment_note import reconcile_segments

    disclosure = parse_operating_segment_note(
        _fixture("daehan_segment_note_fy2025.xml")
    )
    with pytest.raises(SegmentNoteError, match="non-positive whole"):
        reconcile_segments(
            disclosure,
            consolidated_revenue=D("0"),
            consolidated_operating_income=D("-2872504765"),
        )


def test_two_tier_reportable_segment_header_is_reconciled():
    text = """
    <table>
      <tr><td></td><td>부문</td><td>부문</td><td>부문</td><td>부문 합계</td></tr>
      <tr><td></td><td>보고부문</td><td>보고부문</td><td>기타부문</td><td>부문 합계</td></tr>
      <tr><td></td><td>비철금속 제조 및 판매</td><td>비철금속 수출입</td><td>기타부문</td><td>부문 합계</td></tr>
      <tr><td>매출액</td><td>9693315659</td><td>3182281396</td><td>413925299</td><td>13289522354</td></tr>
      <tr><td>영업이익</td><td>1241489905</td><td>54979012</td><td>(13599031)</td><td>1282869886</td></tr>
    </table>
    """
    disclosure = parse_operating_segment_note(text)
    assert disclosure.segment_names == (
        "비철금속 제조 및 판매",
        "비철금속 수출입",
        "기타부문",
    )
    assert disclosure.total_revenue == Decimal("13289522354")
    assert disclosure.total_operating_income == Decimal("1282869886")
    assert disclosure.entries[2].operating_income == Decimal("-13599031")


def test_generic_two_tier_segment_header_without_reportable_group_label():
    text = """
    <table>
      <tr><td></td><td>부문</td><td>부문</td><td>부문</td><td>부문 합계</td></tr>
      <tr><td></td><td>제조 및 판매</td><td>상품 수출입</td><td>폐기물처리 및 기타사업</td><td>부문 합계</td></tr>
      <tr><td>매출액</td><td>12504635145</td><td>4396467004</td><td>633818286</td><td>17534920435</td></tr>
      <tr><td>영업이익(손실)</td><td>1209732583</td><td>71756426</td><td>(31778827)</td><td>1249710182</td></tr>
    </table>
    """
    disclosure = parse_operating_segment_note(text)
    assert disclosure.segment_names == (
        "제조 및 판매",
        "상품 수출입",
        "폐기물처리 및 기타사업",
    )
    assert disclosure.total_revenue == Decimal("17534920435")
    assert disclosure.total_operating_income == Decimal("1249710182")


def test_one_disclosed_unit_rounding_residual_is_tolerated_but_larger_is_not():
    rounded = """
    <table>
      <tr><td></td><td>보고부문</td><td>보고부문</td><td>기타부문</td><td>부문 합계</td></tr>
      <tr><td></td><td>제조 및 판매</td><td>상품 수출입</td><td>기타부문</td><td>부문 합계</td></tr>
      <tr><td>매출액</td><td>9693315659</td><td>3182281396</td><td>413925299</td><td>13289522354</td></tr>
      <tr><td>영업이익</td><td>1241489905</td><td>54979012</td><td>(13599032)</td><td>1282869886</td></tr>
    </table>
    """
    disclosure = parse_operating_segment_note(rounded)
    assert sum(x.operating_income for x in disclosure.entries) == Decimal("1282869885")
    assert disclosure.total_operating_income == Decimal("1282869886")

    bad = rounded.replace("(13599032)", "(13599034)")
    with pytest.raises(SegmentNoteError, match="do not reconcile"):
        parse_operating_segment_note(bad)
