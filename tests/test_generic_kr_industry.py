"""The KR industry providers work for a company this repository has never seen.

Every fixture is a fictional listed company. If a provider needed a hand-written
module or a spec row, these tests could not pass.
"""

from __future__ import annotations

import json
from io import BytesIO
from zipfile import ZipFile

import pytest

from valuation_engine.generic_kr_industry import (
    CORE_SEGMENT_ID,
    CachedCompanyProfileFetcher,
    GenericKRIndustryError,
    classified_industry_dna_router,
    classified_segment_decomposer,
    filing_cadence_freshness_loader,
    load_kr_industry_classification,
    opendart_filing_snapshot_loader,
)
from valuation_engine.industry_dna import EconomicArchetype
from valuation_engine.live_primary_adapters import (
    IndustryKnowledgeSnapshot,
    ResolvedCompanyIdentity,
)
from valuation_engine.source_watch import WatchStatus


AS_OF = "2026-08-27"
CORP = "00999901"
IDENTITY = ResolvedCompanyIdentity(
    target_id=f"KR:DART:{CORP}",
    legal_name="한빛중전기",
    ticker="900990",
    jurisdiction="KR",
    external_ids=(("opendart_corp_code", CORP), ("krx_stock_code", "900990")),
    source_refs=("https://opendart.fss.or.kr/api/corpCode.xml",),
)

FILING_ROWS = [
    {"rcept_no": "20260515000101", "report_nm": "분기보고서 (2026.03)",
     "rcept_dt": "20260515", "corp_code": CORP, "stock_code": "900990"},
    {"rcept_no": "20260320000202", "report_nm": "사업보고서 (2025.12)",
     "rcept_dt": "20260320", "corp_code": CORP, "stock_code": "900990"},
    # Ad-hoc disclosure: real, but not part of the periodic snapshot.
    {"rcept_no": "20260701000303", "report_nm": "주요사항보고서(유상증자결정)",
     "rcept_dt": "20260701", "corp_code": CORP, "stock_code": "900990"},
    # Filed after the cutoff: must never enter the snapshot.
    {"rcept_no": "20260901000404", "report_nm": "반기보고서 (2026.06)",
     "rcept_dt": "20260901", "corp_code": CORP, "stock_code": "900990"},
]

COMPANY_PROFILE = {
    "status": "000", "corp_code": CORP, "corp_name": "한빛중전기",
    "stock_code": "900990", "induty_code": "28112",
}


def _fetch_text(url: str) -> str:
    if "list.json" in url:
        return json.dumps({"status": "000", "list": FILING_ROWS})
    if "company.json" in url:
        return json.dumps(COMPANY_PROFILE)
    raise AssertionError(f"unexpected URL: {url}")


def _fetch_bytes(url: str, body: str = "<BODY><P>단일 제조 사업을 영위합니다.</P></BODY>") -> bytes:
    assert "document.xml" in url
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("report.xml", body)
    return buffer.getvalue()


def _snapshot():
    loader = opendart_filing_snapshot_loader(
        fetch_text=_fetch_text,
        fetch_bytes=_fetch_bytes,
        as_of=AS_OF,
        api_key="TESTKEY",
    )
    return loader(IDENTITY)


def _fetcher():
    return CachedCompanyProfileFetcher(fetch_text=_fetch_text, api_key="TESTKEY")


# ------------------------------------------------------------------- snapshot


def test_snapshot_contains_only_periodic_filings_known_at_the_cutoff():
    snapshot = _snapshot()
    snapshot.validate()
    assert snapshot.document_ids == ("DART_20260515000101", "DART_20260320000202")
    # The post-cutoff half-year report and the ad-hoc disclosure are excluded.
    assert not any("20260901" in item for item in snapshot.document_ids)
    assert not any("20260701" in item for item in snapshot.document_ids)


def test_snapshot_lineage_is_target_bound_and_knowledge_timed():
    snapshot = _snapshot()
    for item in snapshot.evidence_lineage:
        assert item.target_id == IDENTITY.target_id
        assert item.first_seen_at == item.published_at
        assert item.first_seen_at[:10] <= AS_OF


def test_a_company_with_no_periodic_filing_fails_closed():
    def empty(url: str) -> str:
        # status 000 with only ad-hoc rows: fetch succeeds, no periodic filing.
        return json.dumps({"status": "000", "list": [
            {"rcept_no": "20260701000303", "report_nm": "주요사항보고서",
             "rcept_dt": "20260701", "corp_code": CORP, "stock_code": "900990"},
        ]})

    loader = opendart_filing_snapshot_loader(
        fetch_text=empty,
        fetch_bytes=_fetch_bytes,
        as_of=AS_OF,
        api_key="TESTKEY",
    )
    with pytest.raises(GenericKRIndustryError, match="no periodic DART filing"):
        loader(IDENTITY)


# ------------------------------------------------------------------ freshness


def test_recent_filing_is_clean():
    snapshot = _snapshot()
    assessment = filing_cadence_freshness_loader(as_of=AS_OF)(IDENTITY, snapshot)
    assert len(assessment.findings) == 1
    assert assessment.findings[0].status is WatchStatus.CLEAN
    assert not assessment.blocking_findings
    assert assessment.source_snapshot_hash == snapshot.snapshot_hash


def test_stale_filing_warns_without_blocking():
    snapshot = _snapshot()
    tight = filing_cadence_freshness_loader(as_of=AS_OF, max_age_days=30)
    assessment = tight(IDENTITY, snapshot)
    assert assessment.findings[0].status is WatchStatus.EXPECTED_RELEASE_MISSED
    assert not assessment.blocking_findings
    assert assessment.warning_findings


# ------------------------------------------------------- segments + DNA route


def test_decomposer_routes_the_unseen_company_from_its_ksic_code():
    snapshot = _snapshot()
    decomposer = classified_segment_decomposer(
        profile_fetcher=_fetcher(),
        classification=load_kr_industry_classification(),
    )
    segments = decomposer(IDENTITY, snapshot)
    assert len(segments) == 1
    segment = segments[0]
    assert segment.segment_id == CORE_SEGMENT_ID
    assert "한빛중전기" in segment.name
    # KSIC 28112 -> 전기장비 제조업 entry.
    assert segment.revenue_recognition == "delivery"
    assert set(segment.evidence_ids) == {
        item.evidence_id
        for item in snapshot.evidence_lineage
        if "SEGMENT_SCOPE:SINGLE" not in item.evidence_id
    }


def test_multi_segment_filing_fails_closed_instead_of_flattening():
    loader = opendart_filing_snapshot_loader(
        fetch_text=_fetch_text,
        fetch_bytes=lambda url: _fetch_bytes(
            url,
            "<BODY><P>연결회사의 보고부문은 철강부문과 건설부문입니다.</P></BODY>",
        ),
        as_of=AS_OF,
        api_key="TESTKEY",
    )
    with pytest.raises(GenericKRIndustryError, match="multiple operating segments"):
        loader(IDENTITY)


def test_multi_segment_business_table_fails_closed():
    loader = opendart_filing_snapshot_loader(
        fetch_text=_fetch_text,
        fetch_bytes=lambda url: _fetch_bytes(
            url,
            """
            <BODY><TABLE>
              <TR><TH>사업부문</TH><TH>매출</TH></TR>
              <TR><TD>철강</TD><TD>100</TD></TR>
              <TR><TD>건설</TD><TD>80</TD></TR>
              <TR><TD>합계</TD><TD>180</TD></TR>
            </TABLE></BODY>
            """,
        ),
        as_of=AS_OF,
        api_key="TESTKEY",
    )
    with pytest.raises(GenericKRIndustryError, match="multiple operating segments"):
        loader(IDENTITY)


def test_explicit_single_segment_declaration_outranks_process_rows():
    loader = opendart_filing_snapshot_loader(
        fetch_text=_fetch_text,
        fetch_bytes=lambda url: _fetch_bytes(
            url,
            """
            <BODY>
              <P>당사는 공시대상 사업부문을 철강으로 단일화하여 표시합니다.</P>
              <TABLE>
                <TR><TH>사업부문</TH><TH>생산량</TH></TR>
                <TR><TD>제강</TD><TD>100</TD></TR>
                <TR><TD>압연</TD><TD>90</TD></TR>
              </TABLE>
            </BODY>
            """,
        ),
        as_of=AS_OF,
        api_key="TESTKEY",
    )
    snapshot = loader(IDENTITY)
    assert any("SEGMENT_SCOPE:SINGLE" in item for item in snapshot.evidence_ids)


def test_decomposer_refuses_a_snapshot_without_the_scope_receipt():
    screened = _snapshot()
    filing_lineage = tuple(
        item
        for item in screened.evidence_lineage
        if "SEGMENT_SCOPE:SINGLE" not in item.evidence_id
    )
    unscreened = IndustryKnowledgeSnapshot.build(
        as_of=screened.as_of,
        source_ids=screened.source_ids,
        document_ids=screened.document_ids,
        evidence_ids=tuple(item.evidence_id for item in filing_lineage),
        content_hashes=tuple(item.content_hash for item in filing_lineage),
        evidence_lineage=filing_lineage,
    )
    decomposer = classified_segment_decomposer(
        profile_fetcher=_fetcher(),
        classification=load_kr_industry_classification(),
    )
    with pytest.raises(GenericKRIndustryError, match="unscreened company"):
        decomposer(IDENTITY, unscreened)


def test_router_attaches_the_mapped_archetypes():
    snapshot = _snapshot()
    fetcher = _fetcher()
    classification = load_kr_industry_classification()
    segments = classified_segment_decomposer(
        profile_fetcher=fetcher, classification=classification
    )(IDENTITY, snapshot)
    profiles = classified_industry_dna_router(
        profile_fetcher=fetcher, classification=classification
    )(IDENTITY, segments, snapshot)
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.sector_adapter == "power.electrical_equipment"
    assert profile.archetypes == (
        EconomicArchetype.CONTRACTED_BACKLOG,
        EconomicArchetype.CAPACITY_MANUFACTURING,
    )
    assert profile.evidence_keys == segments[0].evidence_ids


def test_an_unmapped_ksic_code_fails_closed_instead_of_guessing():
    def other(url: str) -> str:
        if "company.json" in url:
            return json.dumps({**COMPANY_PROFILE, "induty_code": "97001"})
        return _fetch_text(url)

    decomposer = classified_segment_decomposer(
        profile_fetcher=CachedCompanyProfileFetcher(fetch_text=other, api_key="K"),
        classification=load_kr_industry_classification(),
    )
    with pytest.raises(GenericKRIndustryError, match="97001 is not covered"):
        decomposer(IDENTITY, _snapshot())


def test_profile_fetch_is_cached_across_decomposer_and_router():
    calls = {"company": 0}

    def counting(url: str) -> str:
        if "company.json" in url:
            calls["company"] += 1
        return _fetch_text(url)

    fetcher = CachedCompanyProfileFetcher(fetch_text=counting, api_key="K")
    classification = load_kr_industry_classification()
    snapshot = _snapshot()
    segments = classified_segment_decomposer(
        profile_fetcher=fetcher, classification=classification
    )(IDENTITY, snapshot)
    classified_industry_dna_router(
        profile_fetcher=fetcher, classification=classification
    )(IDENTITY, segments, snapshot)
    assert calls["company"] == 1


def test_longest_ksic_prefix_wins():
    classification = load_kr_industry_classification()
    assert classification.lookup("58211").sector_adapter == "content.games"
    assert classification.lookup("58229").sector_adapter == "software.application"
