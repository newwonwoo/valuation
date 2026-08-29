"""The KR industry providers work for a company this repository has never seen.

Every fixture is a fictional listed company. If a provider needed a hand-written
module or a spec row, these tests could not pass.
"""

from __future__ import annotations

import json

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
from valuation_engine.live_primary_adapters import ResolvedCompanyIdentity
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


def _snapshot():
    loader = opendart_filing_snapshot_loader(
        fetch_text=_fetch_text, as_of=AS_OF, api_key="TESTKEY"
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
        fetch_text=empty, as_of=AS_OF, api_key="TESTKEY"
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
    assert set(segment.evidence_ids) == set(snapshot.evidence_ids)


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
