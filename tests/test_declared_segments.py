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

from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from valuation_engine.declared_segments import (
    DeclaredSegment,
    DeclaredSegments,
    DeclaredSegmentsError,
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
        EconomicArchetype.CAPACITY_MANUFACTURING,
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
