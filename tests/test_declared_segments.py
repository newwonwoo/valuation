"""Multi-segment scope: evidence names the segments, declarations type them.

The snapshot loader's screen refuses a filing that looks multi-segment; the
declaration is how a run answers. The containment under test: the declared set
must match the filing's own IFRS 8 note bijectively (no invented segments, no
quietly dropped ones), each declared segment gets its own scope receipt bound
to the filing hash, the decomposer builds descriptors only from those receipts,
and each segment routes through its *declared* KSIC — because the issuer-level
code would hand a trucking segment a steel archetype. The single-segment path
must stay byte-identical throughout; the three committed live runs pin that.
"""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from valuation_engine.declared_segments import (
    DeclaredSegment,
    DeclaredSegments,
    DeclaredSegmentsError,
    SourceBoundSegmentEntry,
    SourceBoundSegmentExtraction,
    load_declared_segments,
)
from valuation_engine.generic_kr_industry import (
    GenericKRIndustryError,
    classified_industry_dna_router,
    classified_segment_decomposer,
    load_kr_industry_classification,
    opendart_filing_snapshot_loader,
)
from valuation_engine.industry_dna import EconomicArchetype
from valuation_engine.live_primary_adapters import ResolvedCompanyIdentity

FIXTURES = Path(__file__).resolve().parent / "fixtures"
AS_OF = "2026-08-27"
CORP = "00113225"
IDENTITY = ResolvedCompanyIdentity(
    target_id=f"KR:DART:{CORP}",
    legal_name="대한제강",
    ticker="084010",
    jurisdiction="KR",
    external_ids=(("opendart_corp_code", CORP), ("krx_stock_code", "084010")),
    source_refs=("https://opendart.fss.or.kr/api/corpCode.xml",),
)

#: A business-overview table that trips the multi-segment screen — the same
#: kind of 사업부문 table the real 대한제강 filing carries.
_SCREEN_TRIPPING_MEMBER = (
    "<BODY><TABLE><TR><TD>사업부문</TD><TD>매출액</TD></TR>"
    "<TR><TD>제강</TD><TD>100</TD></TR>"
    "<TR><TD>운송</TD><TD>10</TD></TR>"
    "<TR><TD>기타</TD><TD>5</TD></TR></TABLE></BODY>"
)

_SINGLE_SEGMENT_MEMBER = "<BODY><P>단일 제조 사업을 영위합니다.</P></BODY>"


def _fetch_text(url: str) -> str:
    if "list.json" in url:
        return json.dumps(
            {
                "status": "000",
                "list": [
                    {
                        "rcept_no": "20260814003201",
                        "report_nm": "반기보고서 (2026.06)",
                        "rcept_dt": "20260814",
                        "corp_code": CORP,
                        "stock_code": "084010",
                    }
                ],
            }
        )
    raise AssertionError(f"unexpected URL: {url}")


def _archive(members: dict[str, str]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def _fetch_bytes_multi(url: str) -> bytes:
    assert "document.xml" in url
    return _archive(
        {
            "overview.xml": _SCREEN_TRIPPING_MEMBER,
            "segment_note.xml": (FIXTURES / "daehan_segment_note_h1_2026.xml").read_text(
                encoding="utf-8", errors="replace"
            ),
        }
    )


def _fetch_bytes_single(url: str) -> bytes:
    assert "document.xml" in url
    return _archive({"report.xml": _SINGLE_SEGMENT_MEMBER})


def _declaration(**overrides) -> DeclaredSegments:
    rows = overrides.pop(
        "segments",
        (
            DeclaredSegment(
                segment_id="steel",
                disclosed_name="제강부문",
                ksic_code="2411",
                rationale="EAF 빌릿·철근 제조와 압연 — 철강 원형으로 평가한다.",
            ),
            DeclaredSegment(
                segment_id="transport",
                disclosed_name="운송부문",
                ksic_code="4930",
                rationale="종속 물류사의 화물 운송 용역 — 차량 capacity 가동률 경제.",
            ),
            DeclaredSegment(
                segment_id="other",
                disclosed_name="기타부문",
                ksic_code="6811",
                rationale="임대업 중심의 기타 부문 — 자산 수익률 원형으로 평가한다.",
            ),
        ),
    )
    declared = DeclaredSegments(
        target_id=overrides.pop("target_id", IDENTITY.target_id),
        as_of=overrides.pop("as_of", AS_OF),
        source_ref=overrides.pop(
            "source_ref", "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814003201"
        ),
        segments=tuple(rows),
        source_bound_extraction=overrides.pop("source_bound_extraction", None),
    )
    declared.validate()
    return declared


def _load_snapshot(fetch_bytes, declared):
    loader = opendart_filing_snapshot_loader(
        fetch_text=_fetch_text,
        fetch_bytes=fetch_bytes,
        as_of=AS_OF,
        api_key="TEST",
        declared_segments=declared,
    )
    return loader(IDENTITY)


def _refusing_profile_fetcher(identity):
    raise AssertionError(
        "the multi-segment path must never consult the issuer-level profile"
    )


# ----------------------------------------------------------- snapshot loader


def test_a_multi_segment_filing_without_a_declaration_names_the_next_input():
    with pytest.raises(GenericKRIndustryError, match="declare the reportable"):
        _load_snapshot(_fetch_bytes_multi, None)


def test_a_matching_declaration_yields_one_scope_receipt_per_segment():
    snapshot = _load_snapshot(_fetch_bytes_multi, _declaration())
    receipts = tuple(
        item
        for item in snapshot.evidence_lineage
        if ":SEGMENT_SCOPE:" in item.evidence_id and not item.evidence_id.endswith(":SINGLE")
    )
    assert tuple(item.evidence_id.rsplit(":", 1)[1] for item in receipts) == (
        "steel",
        "transport",
        "other",
    )
    # Every receipt is bound to the same filing archive, and the whole-company
    # receipt is absent — the single-segment path cannot fire by accident.
    assert len({item.content_hash for item in receipts}) == 1
    assert not any(
        item.evidence_id.endswith(":SEGMENT_SCOPE:SINGLE")
        for item in snapshot.evidence_lineage
    )


def test_source_bound_llm_extraction_handles_irregular_filing_layout():
    """The LLM reads semantics; code verifies its exact source-table cells."""
    source = """단위:천원
<TABLE><THEAD><TR><TH>구분</TH><TH>제강부문</TH><TH>운송부문</TH><TH>기타부문</TH><TH>합계</TH></TR></THEAD>
<TBODY><TR><TD>매출액</TD><TD>100</TD><TD>20</TD><TD>5</TD><TD>125</TD></TR>
<TR><TD>영업이익</TD><TD>10</TD><TD>2</TD><TD>(1)</TD><TD>11</TD></TR></TBODY></TABLE>"""
    extraction = SourceBoundSegmentExtraction(
        extractor="llm_reviewed",
        document_id="DART_20260814003201",
        member_path="llm_source.xml",
        member_sha256=sha256(source.encode("utf-8")).hexdigest(),
        reporting_unit="천원",
        entries=(
            SourceBoundSegmentEntry(
                "제강부문", Decimal("100"), Decimal("10"),
                source.index("제강부문"), source.index(">100<") + 1,
                source.index(">10<") + 1,
            ),
            SourceBoundSegmentEntry(
                "운송부문", Decimal("20"), Decimal("2"),
                source.index("운송부문"), source.index(">20<") + 1,
                source.index(">2<") + 1,
            ),
            SourceBoundSegmentEntry(
                "기타부문", Decimal("5"), Decimal("-1"),
                source.index("기타부문"), source.index(">5<") + 1,
                source.index(">(1)<") + 1,
            ),
        ),
        filed_total_revenue=Decimal("125"),
        filed_total_operating_income=Decimal("11"),
        filed_total_revenue_offset=source.index(">125<") + 1,
        filed_total_operating_income_offset=source.index(">11<") + 1,
        revenue_row_label="매출액",
        revenue_row_label_offset=source.index("매출액"),
        operating_income_row_label="영업이익",
        operating_income_row_label_offset=source.index("영업이익"),
    )

    def fetch_bytes(url: str) -> bytes:
        assert "document.xml" in url
        return _archive(
            {"overview.xml": _SCREEN_TRIPPING_MEMBER, "llm_source.xml": source}
        )

    snapshot = _load_snapshot(
        fetch_bytes, _declaration(source_bound_extraction=extraction)
    )
    assert any(
        item.evidence_id.endswith(":SEGMENT_SCOPE:steel")
        for item in snapshot.evidence_lineage
    )


def test_source_bound_llm_extraction_refuses_a_changed_member():
    source = """단위:천원
<TABLE><TR><TH>구분</TH><TH>제강부문</TH><TH>운송부문</TH><TH>기타부문</TH><TH>합계</TH></TR>
<TR><TD>매출액</TD><TD>100</TD><TD>20</TD><TD>5</TD><TD>125</TD></TR>
<TR><TD>영업이익</TD><TD>10</TD><TD>2</TD><TD>(1)</TD><TD>11</TD></TR></TABLE>"""
    extraction = SourceBoundSegmentExtraction(
        extractor="llm_reviewed",
        document_id="DART_20260814003201",
        member_path="llm_source.xml",
        member_sha256="0" * 64,
        reporting_unit="천원",
        entries=(
            SourceBoundSegmentEntry(
                "제강부문", Decimal("100"), Decimal("10"), 1, 2, 3
            ),
            SourceBoundSegmentEntry(
                "운송부문", Decimal("20"), Decimal("2"), 4, 5, 6
            ),
            SourceBoundSegmentEntry(
                "기타부문", Decimal("5"), Decimal("-1"), 7, 8, 9
            ),
        ),
        filed_total_revenue=Decimal("125"),
        filed_total_operating_income=Decimal("11"),
        filed_total_revenue_offset=10,
        filed_total_operating_income_offset=11,
        revenue_row_label="매출액",
        revenue_row_label_offset=12,
        operating_income_row_label="영업이익",
        operating_income_row_label_offset=13,
    )

    def fetch_bytes(url: str) -> bytes:
        assert "document.xml" in url
        return _archive(
            {"overview.xml": _SCREEN_TRIPPING_MEMBER, "llm_source.xml": source}
        )

    with pytest.raises(DeclaredSegmentsError, match="path/hash"):
        _load_snapshot(fetch_bytes, _declaration(source_bound_extraction=extraction))


def test_source_bound_llm_extraction_refuses_swapped_segment_economics():
    source = """단위:천원
<TABLE><TR><TH>구분</TH><TH>제강부문</TH><TH>운송부문</TH><TH>기타부문</TH><TH>합계</TH></TR>
<TR><TD>매출액</TD><TD>100</TD><TD>20</TD><TD>5</TD><TD>125</TD></TR>
<TR><TD>영업이익</TD><TD>10</TD><TD>2</TD><TD>(1)</TD><TD>11</TD></TR></TABLE>"""
    extraction = SourceBoundSegmentExtraction(
        extractor="llm_reviewed",
        document_id="DART_20260814003201",
        member_path="llm_source.xml",
        member_sha256=sha256(source.encode("utf-8")).hexdigest(),
        reporting_unit="천원",
        entries=(
            SourceBoundSegmentEntry(
                "운송부문", Decimal("100"), Decimal("10"),
                source.index("운송부문"), source.index(">100<") + 1,
                source.index(">10<") + 1,
            ),
            SourceBoundSegmentEntry(
                "제강부문", Decimal("20"), Decimal("2"),
                source.index("제강부문"), source.index(">20<") + 1,
                source.index(">2<") + 1,
            ),
            SourceBoundSegmentEntry(
                "기타부문", Decimal("5"), Decimal("-1"),
                source.index("기타부문"), source.index(">5<") + 1,
                source.index(">(1)<") + 1,
            ),
        ),
        filed_total_revenue=Decimal("125"),
        filed_total_operating_income=Decimal("11"),
        filed_total_revenue_offset=source.index(">125<") + 1,
        filed_total_operating_income_offset=source.index(">11<") + 1,
        revenue_row_label="매출액",
        revenue_row_label_offset=source.index("매출액"),
        operating_income_row_label="영업이익",
        operating_income_row_label_offset=source.index("영업이익"),
    )

    with pytest.raises(DeclaredSegmentsError, match="source-table column"):
        extraction.bind_source_member(
            document_id="DART_20260814003201",
            member_path="llm_source.xml",
            member_sha256=sha256(source.encode("utf-8")).hexdigest(),
            text=source,
        )


def test_source_bound_extraction_refuses_cell_prefixes_as_exact_values():
    source = """단위:천원
<TABLE><TR><TH>구분</TH><TH>Alpha Segment</TH><TH>Beta Segment</TH><TH>합계</TH></TR>
<TR><TD>매출액</TD><TD>100</TD><TD>200</TD><TD>300</TD></TR>
<TR><TD>영업이익</TD><TD>10</TD><TD>20</TD><TD>30</TD></TR></TABLE>"""
    extraction = SourceBoundSegmentExtraction(
        extractor="llm_reviewed",
        document_id="DART_20260814003201",
        member_path="llm_source.xml",
        member_sha256=sha256(source.encode("utf-8")).hexdigest(),
        reporting_unit="천원",
        entries=(
            SourceBoundSegmentEntry(
                "Alpha", Decimal("10"), Decimal("1"),
                source.index("Alpha"), source.index(">100<") + 1,
                source.index(">10<") + 1,
            ),
            SourceBoundSegmentEntry(
                "Beta", Decimal("20"), Decimal("2"),
                source.index("Beta"), source.index(">200<") + 1,
                source.index(">20<") + 1,
            ),
        ),
        filed_total_revenue=Decimal("30"),
        filed_total_operating_income=Decimal("3"),
        filed_total_revenue_offset=source.index(">300<") + 1,
        filed_total_operating_income_offset=source.index(">30<") + 1,
        revenue_row_label="매출액",
        revenue_row_label_offset=source.index("매출액"),
        operating_income_row_label="영업이익",
        operating_income_row_label_offset=source.index("영업이익"),
    )

    with pytest.raises(DeclaredSegmentsError, match="full visible source-table cell"):
        extraction.bind_source_member(
            document_id="DART_20260814003201",
            member_path="llm_source.xml",
            member_sha256=sha256(source.encode("utf-8")).hexdigest(),
            text=source,
        )


def test_source_bound_extraction_requires_names_to_precede_metric_rows():
    source = """단위:천원
<TABLE><THEAD><TR><TH>구분</TH><TH>실제 A</TH><TH>실제 B</TH><TH>합계</TH></TR></THEAD>
<TBODY><TR><TD>매출액</TD><TD>100</TD><TD>200</TD><TD>300</TD></TR>
<TR><TD>영업이익</TD><TD>10</TD><TD>20</TD><TD>30</TD></TR>
<TR><TD>메모</TD><TD>발명 A</TD><TD>발명 B</TD><TD>해당 없음</TD></TR></TBODY></TABLE>"""
    extraction = SourceBoundSegmentExtraction(
        extractor="llm_reviewed",
        document_id="DART_20260814003201",
        member_path="llm_source.xml",
        member_sha256=sha256(source.encode("utf-8")).hexdigest(),
        reporting_unit="천원",
        entries=(
            SourceBoundSegmentEntry(
                "발명 A", Decimal("100"), Decimal("10"),
                source.index("발명 A"), source.index(">100<") + 1,
                source.index(">10<") + 1,
            ),
            SourceBoundSegmentEntry(
                "발명 B", Decimal("200"), Decimal("20"),
                source.index("발명 B"), source.index(">200<") + 1,
                source.index(">20<") + 1,
            ),
        ),
        filed_total_revenue=Decimal("300"),
        filed_total_operating_income=Decimal("30"),
        filed_total_revenue_offset=source.index(">300<") + 1,
        filed_total_operating_income_offset=source.index(">30<") + 1,
        revenue_row_label="매출액",
        revenue_row_label_offset=source.index("매출액"),
        operating_income_row_label="영업이익",
        operating_income_row_label_offset=source.index("영업이익"),
    )

    with pytest.raises(DeclaredSegmentsError, match="precede the metric rows"):
        extraction.bind_source_member(
            document_id="DART_20260814003201",
            member_path="llm_source.xml",
            member_sha256=sha256(source.encode("utf-8")).hexdigest(),
            text=source,
        )


def test_source_bound_extraction_rejects_pre_metric_memo_cells_as_names():
    source = """단위:천원
<TABLE><THEAD><TR><TH>구분</TH><TH>실제 A</TH><TH>실제 B</TH><TH>합계</TH></TR></THEAD>
<TBODY><TR><TD>메모</TD><TD>발명 A</TD><TD>발명 B</TD><TD>해당 없음</TD></TR>
<TR><TD>매출액</TD><TD>100</TD><TD>200</TD><TD>300</TD></TR>
<TR><TD>영업이익</TD><TD>10</TD><TD>20</TD><TD>30</TD></TR></TBODY></TABLE>"""
    extraction = SourceBoundSegmentExtraction(
        extractor="llm_reviewed",
        document_id="DART_20260814003201",
        member_path="llm_source.xml",
        member_sha256=sha256(source.encode("utf-8")).hexdigest(),
        reporting_unit="천원",
        entries=(
            SourceBoundSegmentEntry(
                "발명 A", Decimal("100"), Decimal("10"),
                source.index("발명 A"), source.index(">100<") + 1,
                source.index(">10<") + 1,
            ),
            SourceBoundSegmentEntry(
                "발명 B", Decimal("200"), Decimal("20"),
                source.index("발명 B"), source.index(">200<") + 1,
                source.index(">20<") + 1,
            ),
        ),
        filed_total_revenue=Decimal("300"),
        filed_total_operating_income=Decimal("30"),
        filed_total_revenue_offset=source.index(">300<") + 1,
        filed_total_operating_income_offset=source.index(">30<") + 1,
        revenue_row_label="매출액",
        revenue_row_label_offset=source.index("매출액"),
        operating_income_row_label="영업이익",
        operating_income_row_label_offset=source.index("영업이익"),
    )

    with pytest.raises(DeclaredSegmentsError, match="actual TH source-table header"):
        extraction.bind_source_member(
            document_id="DART_20260814003201",
            member_path="llm_source.xml",
            member_sha256=sha256(source.encode("utf-8")).hexdigest(),
            text=source,
        )


def test_a_declaration_on_a_whole_company_filing_is_refused():
    with pytest.raises(GenericKRIndustryError, match="remove the declaration"):
        _load_snapshot(_fetch_bytes_single, _declaration())


def test_a_declaration_that_drops_a_disclosed_segment_is_refused():
    partial = _declaration(
        segments=(
            DeclaredSegment(
                segment_id="steel",
                disclosed_name="제강부문",
                ksic_code="2411",
                rationale="EAF 빌릿·철근 제조와 압연 — 철강 원형으로 평가한다.",
            ),
            DeclaredSegment(
                segment_id="transport",
                disclosed_name="운송부문",
                ksic_code="4930",
                rationale="종속 물류사의 화물 운송 용역 — 차량 capacity 가동률 경제.",
            ),
        )
    )
    with pytest.raises(DeclaredSegmentsError, match="기타부문"):
        _load_snapshot(_fetch_bytes_multi, partial)


def test_a_declared_segment_the_filing_never_discloses_is_refused():
    invented = _declaration(
        segments=(
            *_declaration().segments[:2],
            DeclaredSegment(
                segment_id="bio",
                disclosed_name="바이오부문",
                ksic_code="21",
                rationale="존재하지 않는 부문을 선언해 보는 봉쇄 테스트용 문구다.",
            ),
        )
    )
    with pytest.raises(DeclaredSegmentsError, match="바이오부문"):
        _load_snapshot(_fetch_bytes_multi, invented)


# --------------------------------------------------- decomposer + DNA route


def test_each_declared_segment_becomes_its_own_typed_descriptor():
    declared = _declaration()
    snapshot = _load_snapshot(_fetch_bytes_multi, declared)
    classification = load_kr_industry_classification()
    decompose = classified_segment_decomposer(
        profile_fetcher=_refusing_profile_fetcher,
        classification=classification,
        declared_segments=declared,
    )
    segments = decompose(IDENTITY, snapshot)
    assert tuple(item.segment_id for item in segments) == (
        "steel",
        "transport",
        "other",
    )
    steel, transport, other = segments
    # Each descriptor is structured by its DECLARED classification, not the
    # issuer's: the trucking segment gets freight economics, not steel's.
    assert "철근" not in transport.name
    assert transport.price_formation.startswith("contracted freight")
    assert other.price_formation.startswith("market rent")
    # Each carries its own scope receipt first.
    assert steel.evidence_ids[0].endswith(":SEGMENT_SCOPE:steel")
    assert transport.evidence_ids[0].endswith(":SEGMENT_SCOPE:transport")

    route = classified_industry_dna_router(
        profile_fetcher=_refusing_profile_fetcher,
        classification=classification,
        declared_segments=declared,
    )
    profiles = route(IDENTITY, segments, snapshot)
    by_segment = {item.segment_id: item for item in profiles}
    assert EconomicArchetype.COMMODITY_PRICE_TAKER in by_segment["steel"].archetypes
    assert by_segment["transport"].archetypes == (
        EconomicArchetype.PROCESS_SPREAD,
    )
    assert by_segment["other"].archetypes == (EconomicArchetype.ASSET_YIELD_NAV,)


def test_the_decomposer_refuses_a_declared_segment_without_its_receipt():
    declared = _declaration()
    single_snapshot = _load_snapshot(_fetch_bytes_single, None)
    decompose = classified_segment_decomposer(
        profile_fetcher=_refusing_profile_fetcher,
        classification=load_kr_industry_classification(),
        declared_segments=declared,
    )
    with pytest.raises(GenericKRIndustryError, match="no unique scope receipt"):
        decompose(IDENTITY, single_snapshot)


# ------------------------------------------------------------- declarations


def test_the_declaration_file_loader_validates_like_the_other_front_doors(tmp_path):
    path = tmp_path / "segments.yaml"
    path.write_text(
        """
target_id: KR:DART:00113225
as_of: "2026-08-27"
source_ref: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814003201
segments:
  - segment_id: steel
    disclosed_name: 제강부문
    ksic_code: "2411"
    rationale: EAF 빌릿·철근 제조와 압연 — 철강 원형으로 평가한다.
  - segment_id: other
    disclosed_name: 기타부문
    ksic_code: "6811"
    rationale: 임대업 중심의 기타 부문 — 자산 수익률 원형으로 평가한다.
""",
        encoding="utf-8",
    )
    declared = load_declared_segments(path)
    assert tuple(item.segment_id for item in declared.segments) == ("steel", "other")

    for broken, match in (
        ("segments: []", "single-segment company needs no declaration"),
        (
            "segments:\n  - segment_id: steel\n    disclosed_name: 제강부문\n"
            "    ksic_code: '2411'\n    rationale: 짧다\n"
            "  - segment_id: other\n    disclosed_name: 기타부문\n"
            "    ksic_code: '6811'\n    rationale: 임대업 중심의 기타 부문 판단 근거.",
            "substantive rationale",
        ),
    ):
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "target_id: KR:DART:00113225\nas_of: '2026-08-27'\n"
            "source_ref: https://dart.fss.or.kr/x\n" + broken,
            encoding="utf-8",
        )
        with pytest.raises(DeclaredSegmentsError, match=match):
            load_declared_segments(bad)


# ------------------------------------------------- the assumption namespace


def test_multi_segment_runs_namespace_every_method_key_by_segment():
    """Two segments running DCF families must not share fcff_year_1: the key
    map prefixes method keys and the per-segment bindings, keeps the
    company-level diluted-shares key once, and leaves single-segment runs
    byte-identical to the historical names."""
    from valuation_engine.generic_live_providers import (
        required_assumption_keys_by_segment,
    )
    from valuation_engine.valuation_plan_compiler import SegmentMethodChoice

    multi = required_assumption_keys_by_segment(
        method_choices=(
            SegmentMethodChoice(
                "steel", "commodity_price_taker", "midcycle_price_volume_dcf", None
            ),
            SegmentMethodChoice(
                "transport", "capacity_manufacturing", "driver_dcf", None
            ),
            SegmentMethodChoice("other", "asset_yield_nav", "nav", None),
        ),
        forecast_years=5,
    )
    assert "steel_fcff_year_1" in multi["steel"]
    assert "transport_fcff_year_1" in multi["transport"]
    assert "other_gross_asset_value" in multi["other"]
    assert "steel_ownership" in multi["steel"]
    assert "steel_ev_adjustment" in multi["steel"]
    # nav is equity-output: no EV bridge key for that segment.
    assert "other_ev_adjustment" not in multi["other"]
    # diluted shares is company-level: once, unprefixed, on the first segment.
    assert "diluted_shares" in multi["steel"]
    assert "diluted_shares" not in multi["transport"]
    assert not any(key.startswith("fcff_year") for key in multi["transport"])

    single = required_assumption_keys_by_segment(
        method_choices=(
            SegmentMethodChoice("core", "asset_yield_nav", "nav", None),
        ),
        forecast_years=5,
    )
    assert single == {
        "core": (
            "gross_asset_value",
            "liabilities",
            "ownership",
            "diluted_shares",
        )
    }
