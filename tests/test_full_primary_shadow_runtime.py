from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from valuation_engine.control_plane import StageStatus
from valuation_engine.evidence_collection import static_evidence_collector
from valuation_engine.evaluator_registry import ModelKey
from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile
from valuation_engine.llm_staff import (
    BridgeDraft,
    BridgeProposalBundle,
    IntelligenceProposal,
    RedTeamProposal,
)
from valuation_engine.primary_shadow_runtime import PrimaryShadowRuntimeConfig, run_primary_shadow
from valuation_engine.records import (
    AffectedVariable,
    BridgeRecord,
    Direction,
    EvidenceRecord,
    EvidenceSourceLayer,
    HypothesisRecord,
    MarketObservation,
)
from valuation_engine.scenario_binding import ScenarioBindingSpec
from valuation_engine.street import StreetResearchReport
from valuation_engine.valuation_execution import (
    CompanyValuationPlan,
    SegmentValuationPlan,
    default_evaluator_registry,
)


SCENARIO_INPUTS = {
    "Bear": {
        "normalized_ebitda": (80, "KRW_billion", AffectedVariable.MARGIN),
        "normalized_multiple": (7, "multiple", AffectedVariable.MULTIPLE),
        "ownership": (1, "ratio", AffectedVariable.SEGMENT_VALUE),
        "ev_adjustment": (-100, "KRW_billion", AffectedVariable.NET_DEBT),
        "diluted_shares": (10_000_000, "shares", AffectedVariable.SHARE_COUNT),
    },
    "Base": {
        "normalized_ebitda": (100, "KRW_billion", AffectedVariable.MARGIN),
        "normalized_multiple": (8, "multiple", AffectedVariable.MULTIPLE),
        "ownership": (1, "ratio", AffectedVariable.SEGMENT_VALUE),
        "ev_adjustment": (-100, "KRW_billion", AffectedVariable.NET_DEBT),
        "diluted_shares": (10_000_000, "shares", AffectedVariable.SHARE_COUNT),
    },
    "Bull": {
        "normalized_ebitda": (120, "KRW_billion", AffectedVariable.MARGIN),
        "normalized_multiple": (9, "multiple", AffectedVariable.MULTIPLE),
        "ownership": (1, "ratio", AffectedVariable.SEGMENT_VALUE),
        "ev_adjustment": (-100, "KRW_billion", AffectedVariable.NET_DEBT),
        "diluted_shares": (10_000_000, "shares", AffectedVariable.SHARE_COUNT),
    },
}

REQUIRED_OPERATING = (
    "realized_price",
    "benchmark_price",
    "production",
    "inventory",
    "cash_cost",
    "capacity",
    "utilization",
)

PROJECT_REQUIRED = (
    "contracted_cashflow",
    "capex",
    "financing_close",
    "dscr",
    "tenor",
    "cod",
)


def evidence_records():
    records = []
    metrics = tuple(dict.fromkeys((*REQUIRED_OPERATING, *PROJECT_REQUIRED)))
    for index, metric in enumerate(metrics, start=1):
        records.append(
            EvidenceRecord(
                id=f"E:OPERATING:{metric}",
                target="T",
                metric=metric,
                value=index,
                unit="count",
                source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
                effective_date="2026-06-30",
                observed_date="2026-07-01",
                source_name="filing",
                source_ref=f"filing#operating/{metric}",
                source_grade="A",
                confidence=1.0,
                segment="core",
            )
        )
    for scenario, assumptions in SCENARIO_INPUTS.items():
        for key, (value, unit, _) in assumptions.items():
            records.append(
                EvidenceRecord(
                    id=f"E:{scenario}:{key}",
                    target="T",
                    metric=f"{scenario}:{key}",
                    value=value,
                    unit=unit,
                    source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
                    effective_date="2026-06-30",
                    observed_date="2026-07-01",
                    source_name="filing",
                    source_ref=f"filing#{scenario}/{key}",
                    source_grade="A",
                    confidence=1.0,
                    segment="core",
                )
            )
    return tuple(records)


def intelligence_officer(context):
    hypotheses = []
    for scenario, assumptions in SCENARIO_INPUTS.items():
        for key in assumptions:
            evidence_id = f"E:{scenario}:{key}"
            hypotheses.append(
                HypothesisRecord(
                    id=f"H:{scenario}:{key}",
                    statement=f"{scenario} {key} is a supported shadow assumption candidate",
                    causal_chain=("filing evidence", key, "intrinsic value"),
                    supporting_evidence_ids=(evidence_id,),
                    kill_conditions=(f"{key} is superseded or redefined",),
                )
            )
    return IntelligenceProposal(
        hypotheses=tuple(hypotheses),
        rationale="Primary filings support a normalized commodity-cycle scenario range; no probability weighting is claimed.",
    )


def red_team_officer(context, hypotheses):
    return RedTeamProposal(
        issues=(),
        counter_thesis="The cycle may normalize faster, but the scenario range preserves that uncertainty without market anchoring.",
    )


def bridge_analyst(context, hypotheses, red_team):
    drafts = []
    for scenario, assumptions in SCENARIO_INPUTS.items():
        for key, (value, unit, affected) in assumptions.items():
            evidence_id = f"E:{scenario}:{key}"
            bridge = BridgeRecord(
                id=f"B:{scenario}:{key}",
                evidence_ids=(evidence_id,),
                hypothesis_id=f"H:{scenario}:{key}",
                affected_variable=affected,
                direction=Direction.UNCHANGED,
                old_value=float(value),
                new_value=float(value),
                unit=unit,
                rationale="identity transform from the validated filing fixture",
                confidence=1.0,
                kill_condition=f"{key} is superseded or redefined",
                verification_event="next filing",
                economic_path_id=f"PATH:{scenario}:{key}",
            )
            drafts.append(
                BridgeDraft(
                    assumption_key=key,
                    scenario_id=scenario,
                    bridge=bridge,
                    canonical_unit=unit,
                    transform_id="identity_observation",
                    input_evidence_ids=(evidence_id,),
                    min_value="0" if key in {"ownership", "diluted_shares", "normalized_multiple"} else None,
                    max_value="1" if key == "ownership" else None,
                )
            )
    return BridgeProposalBundle(
        drafts=tuple(drafts),
        rationale="All numeric proposals remain compiler inputs; the deterministic Compiler must reproduce them from Evidence.",
    )


def reports():
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
            source_ref="report-a",
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
            source_ref="report-b",
        ),
    )


def runtime_config(tmp_path):
    profile = IndustryDNAProfile(
        segment_id="core",
        sector_adapter="materials.commodity",
        archetypes=(EconomicArchetype.COMMODITY_PRICE_TAKER,),
        revenue_recognition="delivery",
        price_formation="benchmark-linked",
        asset_ownership="producer",
        capital_intensity="high",
        regulation_intensity="medium",
        customer_structure="industrial buyers",
        reinvestment_model="maintenance and cycle capacity",
        cashflow_duration="cyclical",
        evidence_keys=("ROUTE:EVIDENCE:1",),
    )
    collector = static_evidence_collector(
        source_id="STATIC_PRIMARY",
        checked_at="2026-08-23",
        records=evidence_records(),
        source_fingerprint="STATIC_SOURCE_HASH",
        document_ids=("DOC-1",),
    )
    valuation_plan = CompanyValuationPlan(
        segments=(
            SegmentValuationPlan(
                asset_id="core",
                segment_id="core",
                model_key=ModelKey("commodity_price_taker", "normalized_multiple", "1"),
                ownership_key="ownership",
                ev_to_equity_adjustment_key="ev_adjustment",
            ),
        ),
        reporting_unit="KRW",
        diluted_shares_key="diluted_shares",
    )
    return PrimaryShadowRuntimeConfig(
        run_id="FULL-SHADOW-1",
        company="Example Commodity",
        ticker="EXM",
        target_id="T",
        state_root=tmp_path,
        profiles=(profile,),
        collectors=(collector,),
        intelligence_officer=intelligence_officer,
        red_team_officer=red_team_officer,
        bridge_analyst=bridge_analyst,
        scenario_binding_spec=ScenarioBindingSpec(
            ("Bear", "Base", "Bull"),
            ("normalized_ebitda", "normalized_multiple", "ownership", "ev_adjustment", "diluted_shares"),
        ),
        valuation_plan=valuation_plan,
        evaluator_registry=default_evaluator_registry(),
        selected_methods=("normalized_multiple",),
        industry_snapshot_hash="INDUSTRY_SNAPSHOT_HASH",
        street_loader=reports,
        market_loader=lambda: MarketObservation(65_000, "2026-08-23", "market-source"),
        market_currency="KRW",
        optional_research_units=("PATENT_SIGNAL",),
    )


def test_full_canonical_primary_shadow_sequence_reaches_final_report(tmp_path):
    result = run_primary_shadow(runtime_config(tmp_path))

    assert result.blocked_reasons == ()
    assert result.completed
    assert result.freeze_token is not None
    assert len(result.stage_traces) == 32
    assert result.stage_traces[0].stage == "COMPANY_RESOLUTION"
    assert result.stage_traces[-1].stage == "FINAL_REPORT"
    assert all(
        trace.status not in {
            StageStatus.NOT_IMPLEMENTED,
            StageStatus.BLOCKED,
            StageStatus.RECOVERY_REQUIRED,
        }
        for trace in result.stage_traces
    )

    valuation = result.data["generic_valuation_result"]
    assert valuation.expected_value_per_share is None
    assert valuation.scenarios[0].value_per_share == Decimal("46000")
    assert valuation.scenarios[1].value_per_share == Decimal("70000")
    assert valuation.scenarios[2].value_per_share == Decimal("98000")

    assert result.data["decision_impact_completed"]
    assert result.data["decision_impact_result"].not_measurable_modules
    assert result.data["market_comparison"].envelope.get("Base").gap_per_share == Decimal("5000")
    assert "Expected Value: 미산출" in result.data["final_report"]
    assert "Decision Impact & Research Efficiency" in result.data["final_report"]
    assert result.data["research_learning_record_hash"]
    assert (Path(tmp_path) / "state" / "EXM" / "current_state.json").exists()
    assert (Path(tmp_path) / "runs" / "EXM" / "FULL-SHADOW-1" / "final_report.md").exists()
    assert (Path(tmp_path) / "learning" / "EXM" / "module-impact" / "FULL-SHADOW-1.json").exists()


def test_second_run_loads_prior_impact_record_without_rewriting_first_run(tmp_path):
    first = run_primary_shadow(runtime_config(tmp_path))
    assert first.blocked_reasons == ()

    second_config = replace(runtime_config(tmp_path), run_id="FULL-SHADOW-2")
    second = run_primary_shadow(second_config)
    assert second.blocked_reasons == ()
    assert second.data["research_learning_record_count"] == 1
    assert second.data["saved_current_state"]["last_completed_run"] == "FULL-SHADOW-2"
    assert (Path(tmp_path) / "runs" / "EXM" / "FULL-SHADOW-1").exists()
    assert (Path(tmp_path) / "runs" / "EXM" / "FULL-SHADOW-2").exists()


def test_required_funding_scan_fails_closed_without_adapter(tmp_path):
    config = runtime_config(tmp_path)
    profile = IndustryDNAProfile(
        segment_id="project",
        sector_adapter="power.project_developer",
        archetypes=(EconomicArchetype.PROJECT_FINANCE,),
        revenue_recognition="contract cash flow",
        price_formation="offtake contract",
        asset_ownership="project SPV",
        capital_intensity="very high",
        regulation_intensity="high",
        customer_structure="offtaker",
        reinvestment_model="project capex",
        cashflow_duration="long",
        evidence_keys=("ROUTE:EVIDENCE:2",),
    )
    result = run_primary_shadow(replace(config, profiles=(profile,), run_id="FUNDING-BLOCK"))
    assert result.blocked_reasons
    funding_trace = next(trace for trace in result.stage_traces if trace.stage == "UPSTREAM_FUNDING_SCAN")
    assert funding_trace.status is StageStatus.NOT_IMPLEMENTED
