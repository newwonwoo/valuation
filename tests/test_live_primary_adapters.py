from io import BytesIO
from zipfile import ZipFile

import pytest

from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile
from valuation_engine.live_primary_adapters import (
    AuthoritativeEvidenceLineage,
    CompanyResolutionRequest,
    IndustryKnowledgeSnapshot,
    LiveFreshnessAssessment,
    OpenDartCorpRecord,
    SegmentDescriptor,
    live_company_resolution_adapter,
    live_industry_dna_route_adapter,
    live_industry_snapshot_adapter,
    live_opendart_company_resolver,
    live_segment_decomposition_adapter,
    live_source_freshness_adapter,
    parse_opendart_corp_code_archive,
    resolve_opendart_identity,
)
from valuation_engine.orchestrator import run_controlled_workflow
from valuation_engine.source_watch import WatchFinding, WatchStatus


def _corp_zip() -> bytes:
    xml = """<?xml version='1.0' encoding='UTF-8'?>
<result>
  <list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code><modify_date>20260101</modify_date></list>
  <list><corp_code>00164779</corp_code><corp_name>SK하이닉스</corp_name><stock_code>000660</stock_code><modify_date>20260101</modify_date></list>
</result>""".encode("utf-8")
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("CORPCODE.xml", xml)
    return buffer.getvalue()


def test_opendart_corp_archive_and_identity_resolution():
    records = parse_opendart_corp_code_archive(_corp_zip())
    assert len(records) == 2
    by_ticker = resolve_opendart_identity(
        records, CompanyResolutionRequest("005930", "KR")
    )
    by_name = resolve_opendart_identity(
        records, CompanyResolutionRequest("삼성전자")
    )
    assert by_ticker == by_name
    assert by_ticker.target_id == "KR:DART:00126380"
    assert by_ticker.ticker == "005930"


def test_live_opendart_resolver_fetches_official_archive_with_injected_transport():
    seen = []

    def fetch_bytes(url: str) -> bytes:
        seen.append(url)
        return _corp_zip()

    resolver = live_opendart_company_resolver(fetch_bytes, api_key="TEST_KEY")
    resolved = resolver(CompanyResolutionRequest("000660"))
    assert resolved.legal_name == "SK하이닉스"
    assert seen and "corpCode.xml" in seen[0] and "TEST_KEY" in seen[0]


def test_opendart_resolution_fails_closed_for_wrong_jurisdiction():
    records = (
        OpenDartCorpRecord("00126380", "삼성전자", "005930", "20260101"),
    )
    with pytest.raises(ValueError, match="Korean"):
        resolve_opendart_identity(
            records, CompanyResolutionRequest("005930", "US")
        )


def test_industry_snapshot_hash_is_self_verifying():
    snapshot = IndustryKnowledgeSnapshot.build(
        as_of="2026-08-23",
        source_ids=("KR_OPENDART", "KR_KIET_PSI"),
        document_ids=("D1",),
        evidence_ids=("E_INDUSTRY",),
        content_hashes=("abc", "def"),
    )
    snapshot.validate()
    broken = IndustryKnowledgeSnapshot(
        snapshot.as_of,
        snapshot.source_ids,
        snapshot.document_ids,
        snapshot.evidence_ids,
        snapshot.content_hashes,
        "bad",
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        broken.validate()


def _identity_resolver(_):
    records = parse_opendart_corp_code_archive(_corp_zip())
    return resolve_opendart_identity(
        records, CompanyResolutionRequest("005930")
    )


def _snapshot_loader(_):
    return IndustryKnowledgeSnapshot.build(
        as_of="2026-08-23",
        source_ids=("KR_OPENDART",),
        document_ids=("D1",),
        evidence_ids=("E_INDUSTRY", "E_SEGMENT"),
        content_hashes=("facts-v1", "segment-v1"),
        evidence_lineage=(
            AuthoritativeEvidenceLineage(
                "E_INDUSTRY",
                "KR:DART:00126380",
                "KR_OPENDART",
                "2026-08-20",
                "facts-v1",
            ),
            AuthoritativeEvidenceLineage(
                "E_SEGMENT",
                "KR:DART:00126380",
                "KR_OPENDART",
                "2026-08-20",
                "segment-v1",
            ),
        ),
    )


def _clean_freshness(_, snapshot):
    return LiveFreshnessAssessment(
        checked_at="2026-08-23",
        findings=(
            WatchFinding(
                WatchStatus.CLEAN,
                "KR_OPENDART",
                "current snapshot reviewed",
                (),
                False,
            ),
        ),
        source_snapshot_hash=snapshot.snapshot_hash,
    )


def _segments(_, __):
    return (
        SegmentDescriptor(
            segment_id="semiconductor",
            name="Memory semiconductor",
            revenue_recognition="shipment",
            price_formation="negotiated_and_market",
            asset_ownership="manufacturer",
            capital_intensity="high",
            regulation_intensity="medium",
            customer_structure="global_oem_and_cloud",
            reinvestment_model="fab_and_node_transition",
            cashflow_duration="cyclical_multi_year",
            evidence_ids=("E_SEGMENT",),
        ),
    )


def _dna(_, segments, __):
    segment = segments[0]
    return (
        IndustryDNAProfile(
            segment_id=segment.segment_id,
            sector_adapter="semiconductor.memory",
            archetypes=(
                EconomicArchetype.CAPACITY_MANUFACTURING,
                EconomicArchetype.COMMODITY_PRICE_TAKER,
            ),
            revenue_recognition=segment.revenue_recognition,
            price_formation=segment.price_formation,
            asset_ownership=segment.asset_ownership,
            capital_intensity=segment.capital_intensity,
            regulation_intensity=segment.regulation_intensity,
            customer_structure=segment.customer_structure,
            reinvestment_model=segment.reinvestment_model,
            cashflow_duration=segment.cashflow_duration,
            evidence_keys=("E_SEGMENT", "E_INDUSTRY"),
        ),
    )


def test_live_primary_front_half_runs_with_typed_live_contracts():
    sequence = (
        "COMPANY_RESOLUTION",
        "LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT",
        "SOURCE_FRESHNESS_PRECHECK",
        "SEGMENT_DECOMPOSITION",
        "INDUSTRY_DNA_ROUTE",
    )
    adapters = {
        "COMPANY_RESOLUTION": live_company_resolution_adapter(
            resolver=_identity_resolver,
            request=CompanyResolutionRequest("005930", "KR"),
        ),
        "LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT": live_industry_snapshot_adapter(
            loader=_snapshot_loader
        ),
        "SOURCE_FRESHNESS_PRECHECK": live_source_freshness_adapter(
            loader=_clean_freshness
        ),
        "SEGMENT_DECOMPOSITION": live_segment_decomposition_adapter(
            decomposer=_segments
        ),
        "INDUSTRY_DNA_ROUTE": live_industry_dna_route_adapter(router=_dna),
    }
    result = run_controlled_workflow(
        run_id="LIVE_FRONT_1",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_sequence=sequence,
        adapters=adapters,
        required_stages=sequence,
    )
    assert result.blocked_reasons == ()
    assert all(
        trace.status is StageStatus.PASS for trace in result.stage_traces
    )
    assert result.data["ticker"] == "005930"
    assert result.data["segment_evidence_lineage_hash"]
    assert (
        result.data["industry_dna_profiles"][0].sector_adapter
        == "semiconductor.memory"
    )


def test_freshness_revalidation_blocks_live_run_before_downstream_analysis():
    def stale(_, snapshot):
        return LiveFreshnessAssessment(
            checked_at="2026-08-23",
            findings=(
                WatchFinding(
                    WatchStatus.NEW_RELEASE,
                    "KR_OPENDART",
                    "new quarterly filing detected",
                    ("company.financials",),
                    False,
                ),
            ),
            source_snapshot_hash=snapshot.snapshot_hash,
        )

    sequence = (
        "COMPANY_RESOLUTION",
        "LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT",
        "SOURCE_FRESHNESS_PRECHECK",
    )
    result = run_controlled_workflow(
        run_id="LIVE_FRONT_2",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_sequence=sequence,
        adapters={
            "COMPANY_RESOLUTION": live_company_resolution_adapter(
                resolver=_identity_resolver,
                request=CompanyResolutionRequest("005930"),
            ),
            "LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT": live_industry_snapshot_adapter(
                loader=_snapshot_loader
            ),
            "SOURCE_FRESHNESS_PRECHECK": live_source_freshness_adapter(
                loader=stale
            ),
        },
        required_stages=sequence,
    )
    assert result.blocked_reasons
    assert result.stage_traces[-1].status is StageStatus.RECOVERY_REQUIRED


def test_industry_dna_router_cannot_invent_evidence_ids():
    def bad_dna(identity, segments, snapshot):
        good = _dna(identity, segments, snapshot)[0]
        return (
            IndustryDNAProfile(
                **{
                    **good.__dict__,
                    "evidence_keys": ("FAKE_EVIDENCE",),
                }
            ),
        )

    sequence = (
        "COMPANY_RESOLUTION",
        "LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT",
        "SEGMENT_DECOMPOSITION",
        "INDUSTRY_DNA_ROUTE",
    )
    result = run_controlled_workflow(
        run_id="LIVE_FRONT_3",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_sequence=sequence,
        adapters={
            "COMPANY_RESOLUTION": live_company_resolution_adapter(
                resolver=_identity_resolver,
                request=CompanyResolutionRequest("005930"),
            ),
            "LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT": live_industry_snapshot_adapter(
                loader=_snapshot_loader
            ),
            "SEGMENT_DECOMPOSITION": live_segment_decomposition_adapter(
                decomposer=_segments
            ),
            "INDUSTRY_DNA_ROUTE": live_industry_dna_route_adapter(
                router=bad_dna
            ),
        },
        required_stages=sequence,
    )
    assert result.blocked_reasons
    assert "unknown evidence IDs" in result.stage_traces[-1].rationale
