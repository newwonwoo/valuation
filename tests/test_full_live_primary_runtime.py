from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from valuation_engine.collection_plan import CollectorCapability
from valuation_engine.context_strength_linkage import ContextStrengthLinkageDecision
from valuation_engine.control_plane import StageStatus
from valuation_engine.evidence_collection import (
    EvidenceCollectionBatch,
    EvidenceCollectionRequest,
)
from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile
from valuation_engine.live_primary_adapters import (
    AuthoritativeEvidenceLineage,
    CompanyResolutionRequest,
    IndustryKnowledgeSnapshot,
    LiveFreshnessAssessment,
    ResolvedCompanyIdentity,
    SegmentDescriptor,
)
from valuation_engine.live_runtime import (
    LiveCollectorProvider,
    LivePrimaryProviders,
    LivePrimaryRuntimeConfig,
    run_prism,
)
from valuation_engine.llm_staff import (
    BridgeDraft,
    BridgeProposalBundle,
    IntelligenceProposal,
    RedTeamProposal,
)
from valuation_engine.records import (
    AffectedVariable,
    BridgeRecord,
    Direction,
    EvidenceRecord,
    EvidenceSourceLayer,
    HypothesisRecord,
    MarketObservation,
)
from valuation_engine.scanner_runtime import ScannerFinding, ScannerFindingStatus
from valuation_engine.scenario_binding import ScenarioBindingSpec
from valuation_engine.source_watch import WatchFinding, WatchStatus
from valuation_engine.street import StreetResearchReport
from valuation_engine.valuation_execution import default_evaluator_registry
from valuation_engine.valuation_plan_compiler import (
    CompanyValuationPlanInputs,
    SegmentMethodChoice,
    SegmentValueBinding,
)


TARGET_ID = "KR:DART:00000000"
SEGMENT_ID = "core"
COLLECTOR_ID = "dart-fixture"
SOURCE_ID = "KR_OPENDART"
AS_OF = "2026-08-23"
FIXTURE_SOURCE_URL = (
    "https://github.com/newwonwoo/valuation/blob/main/"
    "tests/test_full_live_primary_runtime.py"
)

COLLECTOR_METRICS = (
    "realized_price",
    "benchmark_price",
    "production",
    "inventory",
    "cash_cost",
    "capacity",
    "utilization",
    "cost_curve_position",
)

EVIDENCE_VALUES = {
    "realized_price": (100, "KRW_billion"),
    "benchmark_price": (8, "multiple"),
    "production": (10_000_000, "shares"),
    "inventory": (-100, "KRW_billion"),
    "cash_cost": (1, "KRW_billion"),
    "capacity": (1, "count"),
    "utilization": (1, "ratio"),
    "cost_curve_position": (1, "count"),
}

ASSUMPTION_SOURCES = {
    "normalized_ebitda": (
        "realized_price",
        AffectedVariable.MARGIN,
    ),
    "normalized_multiple": (
        "benchmark_price",
        AffectedVariable.MULTIPLE,
    ),
    "ownership": (
        "utilization",
        AffectedVariable.SEGMENT_VALUE,
    ),
    "ev_adjustment": (
        "inventory",
        AffectedVariable.NET_DEBT,
    ),
    "diluted_shares": (
        "production",
        AffectedVariable.SHARE_COUNT,
    ),
}

MANDATORY_SCANNERS = (
    "CYCLE_NORMALIZATION",
    "COST_CURVE",
    "INVENTORY",
    "TRADE_FLOW",
)


def identity() -> ResolvedCompanyIdentity:
    return ResolvedCompanyIdentity(
        target_id=TARGET_ID,
        legal_name="Frozen Commodity Co",
        ticker="000000",
        jurisdiction="KR",
        external_ids=(
            ("opendart_corp_code", "00000000"),
            ("krx_stock_code", "000000"),
        ),
        source_refs=(FIXTURE_SOURCE_URL,),
    )


def company_resolver(_: CompanyResolutionRequest) -> ResolvedCompanyIdentity:
    return identity()


def _lineage(evidence_id: str, content_hash: str) -> AuthoritativeEvidenceLineage:
    return AuthoritativeEvidenceLineage(
        evidence_id=evidence_id,
        target_id=TARGET_ID,
        source_id=SOURCE_ID,
        observed_date=AS_OF,
        content_hash=content_hash,
        event_date="2026-06-30",
        effective_date="2026-06-30",
        published_at="2026-08-23T08:30:00+09:00",
        first_seen_at="2026-08-23T08:35:00+09:00",
        revision_id="original",
        revision_at="2026-08-23T08:30:00+09:00",
    )


def industry_snapshot_loader(_: ResolvedCompanyIdentity) -> IndustryKnowledgeSnapshot:
    return IndustryKnowledgeSnapshot.build(
        as_of=AS_OF,
        source_ids=(SOURCE_ID,),
        document_ids=("DOC:INDUSTRY",),
        evidence_ids=("E:INDUSTRY", "E:SEGMENT"),
        content_hashes=("FROZEN-INDUSTRY-CONTENT", "FROZEN-SEGMENT-CONTENT"),
        evidence_lineage=(
            _lineage("E:INDUSTRY", "FROZEN-INDUSTRY-CONTENT"),
            _lineage("E:SEGMENT", "FROZEN-SEGMENT-CONTENT"),
        ),
    )


def freshness_loader(
    _: ResolvedCompanyIdentity,
    snapshot: IndustryKnowledgeSnapshot,
) -> LiveFreshnessAssessment:
    return LiveFreshnessAssessment(
        checked_at=AS_OF,
        findings=(
            WatchFinding(
                WatchStatus.CLEAN,
                SOURCE_ID,
                "frozen source snapshot reviewed",
                (),
                False,
            ),
        ),
        source_snapshot_hash=snapshot.snapshot_hash,
    )


def segment_decomposer(
    _: ResolvedCompanyIdentity,
    __: IndustryKnowledgeSnapshot,
) -> tuple[SegmentDescriptor, ...]:
    return (
        SegmentDescriptor(
            segment_id=SEGMENT_ID,
            name="Commodity operations",
            revenue_recognition="delivery",
            price_formation="benchmark-linked",
            asset_ownership="producer",
            capital_intensity="high",
            regulation_intensity="medium",
            customer_structure="industrial buyers",
            reinvestment_model="maintenance and cycle capacity",
            cashflow_duration="cyclical",
            evidence_ids=("E:SEGMENT",),
        ),
    )


def industry_dna_router(
    _: ResolvedCompanyIdentity,
    segments: tuple[SegmentDescriptor, ...],
    __: IndustryKnowledgeSnapshot,
) -> tuple[IndustryDNAProfile, ...]:
    segment = segments[0]
    return (
        IndustryDNAProfile(
            segment_id=segment.segment_id,
            sector_adapter="materials.commodity",
            archetypes=(EconomicArchetype.COMMODITY_PRICE_TAKER,),
            revenue_recognition=segment.revenue_recognition,
            price_formation=segment.price_formation,
            asset_ownership=segment.asset_ownership,
            capital_intensity=segment.capital_intensity,
            regulation_intensity=segment.regulation_intensity,
            customer_structure=segment.customer_structure,
            reinvestment_model=segment.reinvestment_model,
            cashflow_duration=segment.cashflow_duration,
            evidence_keys=("E:SEGMENT", "E:INDUSTRY"),
        ),
    )


def _evidence(metric: str) -> EvidenceRecord:
    value, unit = EVIDENCE_VALUES.get(metric, (1, "count"))
    return EvidenceRecord(
        id=f"E:{SEGMENT_ID}:{metric}",
        target=TARGET_ID,
        metric=metric,
        value=value,
        unit=unit,
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-06-30",
        observed_date=AS_OF,
        source_name="frozen filing fixture",
        source_ref=FIXTURE_SOURCE_URL,
        source_grade="A",
        confidence=1.0,
        segment=SEGMENT_ID,
    )


def collector(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
    return EvidenceCollectionBatch(
        source_id=SOURCE_ID,
        checked_at=AS_OF,
        records=tuple(_evidence(metric) for metric in request.required_metrics),
        source_fingerprint="FROZEN-COMPANY-SOURCE",
        document_ids=("DOC:COMPANY",),
    )


def scanner_runner(context) -> ScannerFinding:
    return ScannerFinding(
        scanner_id=context.scanner_id,
        status=ScannerFindingStatus.PASS,
        summary=f"{context.scanner_id} checked against frozen primary evidence",
        evidence_ids=(f"E:{SEGMENT_ID}:realized_price",),
        context_only=True,
    )


def intelligence_officer(context) -> IntelligenceProposal:
    hypotheses = []
    for assumption_key, (metric, _) in ASSUMPTION_SOURCES.items():
        hypotheses.append(
            HypothesisRecord(
                id=f"H:{assumption_key}",
                statement=f"Base {assumption_key} is supported by frozen filing evidence",
                causal_chain=(
                    f"observed {metric}",
                    assumption_key,
                    "intrinsic value",
                ),
                supporting_evidence_ids=(f"E:{SEGMENT_ID}:{metric}",),
                kill_conditions=(f"{metric} is superseded or redefined",),
            )
        )
    return IntelligenceProposal(
        hypotheses=tuple(hypotheses),
        rationale="frozen primary evidence supports one unweighted Base scenario",
        context_strength_linkage_decision=ContextStrengthLinkageDecision(
            not_applicable_reason=(
                "This frozen acceptance fixture validates deterministic runtime "
                "integrity and does not assert an external-change investment thesis."
            ),
        ),
    )


def red_team_officer(context, hypotheses) -> RedTeamProposal:
    return RedTeamProposal(
        issues=(),
        counter_thesis=(
            "cycle normalization could differ, but the frozen acceptance fixture tests "
            "runtime integrity rather than an investment conclusion"
        ),
    )


def bridge_analyst(context, hypotheses, red_team) -> BridgeProposalBundle:
    drafts = []
    for assumption_key, (metric, affected_variable) in ASSUMPTION_SOURCES.items():
        value, unit = EVIDENCE_VALUES[metric]
        evidence_id = f"E:{SEGMENT_ID}:{metric}"
        drafts.append(
            BridgeDraft(
                assumption_key=assumption_key,
                scenario_id="Base",
                bridge=BridgeRecord(
                    id=f"B:{assumption_key}",
                    evidence_ids=(evidence_id,),
                    hypothesis_id=f"H:{assumption_key}",
                    affected_variable=affected_variable,
                    direction=Direction.UNCHANGED,
                    old_value=float(value),
                    new_value=float(value),
                    unit=unit,
                    rationale="identity transform from frozen primary evidence",
                    confidence=1.0,
                    kill_condition=f"{metric} is superseded or redefined",
                    verification_event="next filing",
                    economic_path_id=f"PATH:{assumption_key}",
                ),
                canonical_unit=unit,
                transform_id="identity_observation",
                input_evidence_ids=(evidence_id,),
                min_value=(
                    "0"
                    if assumption_key
                    in {"normalized_ebitda", "normalized_multiple", "ownership", "diluted_shares"}
                    else None
                ),
                max_value="1" if assumption_key == "ownership" else None,
            )
        )
    return BridgeProposalBundle(
        drafts=tuple(drafts),
        rationale=(
            "validated proposals remain compiler inputs and are deterministically "
            "reproduced from frozen Evidence"
        ),
    )


def valuation_plan_inputs_loader(context) -> CompanyValuationPlanInputs:
    return CompanyValuationPlanInputs(
        reporting_unit="KRW",
        diluted_shares_key="diluted_shares",
        segment_bindings=(
            SegmentValueBinding(
                segment_id=SEGMENT_ID,
                asset_id=SEGMENT_ID,
                ownership_key="ownership",
                ev_to_equity_adjustment_key="ev_adjustment",
            ),
        ),
    )


def street_reports() -> tuple[StreetResearchReport, ...]:
    return (
        StreetResearchReport(
            broker="BrokerA",
            analyst="AnalystA",
            published_date="2026-08-01",
            target_price=65_000,
            target_price_currency="KRW",
            valuation_method="DCF",
            base_year="2027",
            estimates=(),
            source_ref=FIXTURE_SOURCE_URL,
        ),
        StreetResearchReport(
            broker="BrokerB",
            analyst="AnalystB",
            published_date="2026-08-05",
            target_price=75_000,
            target_price_currency="KRW",
            valuation_method="PER",
            base_year="2027",
            estimates=(),
            source_ref=FIXTURE_SOURCE_URL,
        ),
    )


def runtime_config(tmp_path: Path) -> LivePrimaryRuntimeConfig:
    collector_provider = LiveCollectorProvider(
        CollectorCapability(
            collector_id=COLLECTOR_ID,
            source_id=SOURCE_ID,
            supported_metrics=COLLECTOR_METRICS,
            jurisdictions=("KR",),
            implementation_ref="tests.test_full_live_primary_runtime.collector",
        ),
        collector,
    )
    providers = LivePrimaryProviders(
        company_resolver=company_resolver,
        industry_snapshot_loader=industry_snapshot_loader,
        freshness_loader=freshness_loader,
        segment_decomposer=segment_decomposer,
        industry_dna_router=industry_dna_router,
        collectors=(collector_provider,),
        scanner_runners={scanner_id: scanner_runner for scanner_id in MANDATORY_SCANNERS},
        intelligence_officer=intelligence_officer,
        red_team_officer=red_team_officer,
        bridge_analyst=bridge_analyst,
        evaluator_registry_loader=lambda _: default_evaluator_registry(),
        valuation_plan_inputs_loader=valuation_plan_inputs_loader,
        street_loader=street_reports,
        market_loader=lambda: MarketObservation(
            65_000,
            AS_OF,
            FIXTURE_SOURCE_URL,
        ),
    )
    return LivePrimaryRuntimeConfig(
        run_id="FULL-LIVE-1",
        state_root=tmp_path,
        company_request=CompanyResolutionRequest("000000", "KR"),
        scenario_binding_spec=ScenarioBindingSpec(
            scenario_ids=("Base",),
            required_keys=tuple(ASSUMPTION_SOURCES),
        ),
        providers=providers,
        method_choices=(
            SegmentMethodChoice(
                segment_id=SEGMENT_ID,
                archetype="commodity_price_taker",
                method="normalized_multiple",
                version="1",
            ),
        ),
        market_currency="KRW",
    )


def test_frozen_provider_live_primary_run_reaches_final_report(tmp_path):
    result = run_prism(runtime_config(tmp_path))

    assert result.blocked_reasons == ()
    assert result.completed
    assert result.freeze_token is not None
    assert len(result.stage_traces) == 33
    assert result.stage_traces[0].stage == "COMPANY_RESOLUTION"
    assert result.stage_traces[-1].stage == "FINAL_REPORT"
    assert all(
        trace.status
        not in {
            StageStatus.NOT_IMPLEMENTED,
            StageStatus.BLOCKED,
            StageStatus.RECOVERY_REQUIRED,
            StageStatus.AWAITING_USER_DECISION,
        }
        for trace in result.stage_traces
    )
    intent_trace = next(
        trace
        for trace in result.stage_traces
        if trace.stage == "VALUATION_METHOD_INTENT"
    )
    assert intent_trace.status is StageStatus.PASS
    assert result.data["valuation_plan_method_choices_hash"] == (
        result.data["valuation_method_choices_hash"]
    )

    valuation = result.data["generic_valuation_result"]
    assert valuation.expected_value_per_share is None
    assert valuation.scenarios[0].scenario_id == "Base"
    assert valuation.scenarios[0].value_per_share == Decimal("70000")
    assert result.data["market_comparison"].envelope.get("Base").gap_per_share == Decimal("5000")
    assert result.data["decision_impact_completed"]
    assert "LLM Insight Layer — Environment × Corporate Strength" in result.data[
        "final_report"
    ]
    assert "Status: NOT_APPLICABLE" in result.data["final_report"]
    assert "Expected Value: 미산출" in result.data["final_report"]
    assert "## Sources — Direct Verification" in result.data["final_report"]
    assert FIXTURE_SOURCE_URL in result.data["final_report"]

    state_root = Path(tmp_path)
    assert (state_root / "state" / "000000" / "current_state.json").exists()
    assert (state_root / "runs" / "000000" / "FULL-LIVE-1" / "final_report.md").exists()
    assert (
        state_root
        / "runs"
        / "000000"
        / "FULL-LIVE-1"
        / "context_strength_linkages.json"
    ).exists()
    assert (state_root / "learning" / "000000" / "module-impact" / "FULL-LIVE-1.json").exists()


def test_post_freeze_provider_gap_redacts_intrinsic_outputs(tmp_path):
    config = runtime_config(tmp_path)
    config = replace(
        config,
        providers=replace(config.providers, street_loader=None),
    )

    result = run_prism(config)

    assert result.blocked_reasons
    assert result.stage_traces[-1].stage == "STREET_REFERENCE_LOAD"
    assert result.stage_traces[-1].status is StageStatus.NOT_IMPLEMENTED
    assert result.freeze_token is None
    for key in (
        "generic_valuation_result",
        "intrinsic_scenario_values",
        "expected_value_per_share",
        "valuation_hash",
        "intrinsic_freeze_token",
    ):
        assert key not in result.data


def test_live_final_report_blocks_when_evidence_source_is_not_clickable(tmp_path):
    config = runtime_config(tmp_path)
    original_provider = config.providers.collectors[0]

    def non_http_collector(request):
        batch = collector(request)
        return replace(
            batch,
            records=tuple(
                replace(record, source_ref="fixture://non-verifiable")
                for record in batch.records
            ),
        )

    config = replace(
        config,
        providers=replace(
            config.providers,
            collectors=(
                LiveCollectorProvider(
                    original_provider.capability,
                    non_http_collector,
                ),
            ),
        ),
    )

    result = run_prism(config)

    assert result.blocked_reasons
    assert result.stage_traces[-1].stage == "SAVE_STATE"
    assert result.stage_traces[-1].status is StageStatus.BLOCKED
    assert "final_report" not in result.data


def test_reserved_save_state_output_blocks_before_persistence(tmp_path):
    config = replace(
        runtime_config(tmp_path),
        initial_data={"saved_report_markdown": "forged report"},
    )

    result = run_prism(config)

    assert result.blocked_reasons
    assert result.stage_traces[-1].stage == "SAVE_STATE"
    assert result.stage_traces[-1].status is StageStatus.BLOCKED
    assert "reserved output keys" in result.stage_traces[-1].rationale
    assert not (Path(tmp_path) / "state" / "000000" / "current_state.json").exists()
    assert not (Path(tmp_path) / "runs" / "000000" / "FULL-LIVE-1").exists()
    assert not (
        Path(tmp_path)
        / "learning"
        / "000000"
        / "module-impact"
        / "FULL-LIVE-1.json"
    ).exists()
