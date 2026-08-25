from decimal import Decimal

from valuation_engine.assumption_compiler import AssumptionSpec
from valuation_engine.audit_adapter import generic_audit_adapter
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.decision_impact import DecisionOutcome, ImpactClassification
from valuation_engine.evaluator_registry import ModelKey
from valuation_engine.evidence_adapter import evidence_ledger_adapter
from valuation_engine.impact_adapter import GenericDecisionImpactConfig
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.orchestrator import run_controlled_workflow
from valuation_engine.records import (
    AffectedVariable,
    BridgeRecord,
    Direction,
    EvidenceRecord,
    EvidenceSourceLayer,
    HypothesisRecord,
)
from valuation_engine.scenario_binding import ScenarioBindingSpec
from valuation_engine.shadow_adapters import scenario_build_adapter
from valuation_engine.valuation_adapter import deterministic_valuation_adapter
from valuation_engine.valuation_execution import (
    CompanyValuationPlan,
    SegmentValuationPlan,
    default_evaluator_registry,
)


SCENARIOS = {
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


def build_inputs():
    evidences = []
    hypotheses = []
    bridges = []
    specs = []
    for scenario, assumptions in SCENARIOS.items():
        for key, (value, unit, affected) in assumptions.items():
            evidence_id = f"E:{scenario}:{key}"
            hypothesis_id = f"H:{scenario}:{key}"
            bridge_id = f"B:{scenario}:{key}"
            evidences.append(
                EvidenceRecord(
                    id=evidence_id,
                    target="T",
                    metric=key,
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
            hypotheses.append(
                HypothesisRecord(
                    id=hypothesis_id,
                    statement=f"{key} is usable for {scenario}",
                    causal_chain=("evidence", key, "intrinsic_value"),
                    supporting_evidence_ids=(evidence_id,),
                    kill_conditions=(f"{key} invalidated",),
                )
            )
            bridges.append(
                BridgeRecord(
                    id=bridge_id,
                    evidence_ids=(evidence_id,),
                    hypothesis_id=hypothesis_id,
                    affected_variable=affected,
                    direction=Direction.UNCHANGED,
                    old_value=float(value),
                    new_value=float(value),
                    unit=unit,
                    rationale="identity observation for end-to-end contract test",
                    confidence=1.0,
                    kill_condition=f"{key} invalidated",
                    verification_event="next filing",
                    economic_path_id=f"PATH:{scenario}:{key}",
                )
            )
            specs.append(
                AssumptionSpec(
                    key=key,
                    scenario_id=scenario,
                    bridge_id=bridge_id,
                    canonical_unit=unit,
                    transform_id="identity_observation",
                )
            )
    return EvidenceLedger(tuple(evidences)), tuple(hypotheses), tuple(bridges), tuple(specs)


def valuation_plan():
    return CompanyValuationPlan(
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


def impact_config():
    def without_deterministic_valuation(context):
        compiled = context.data["compiled_assumption_set"]
        return DecisionOutcome(
            status="VALUATION_BLOCKED",
            assumption_hash=compiled.assumption_set_hash,
            route_hash=context.data["route_hash"],
            selected_methods=context.data["selected_methods"],
            blocked_reasons=("deterministic valuation removed",),
        )

    return GenericDecisionImpactConfig(
        counterfactual_runners={
            "DETERMINISTIC_VALUATION": without_deterministic_valuation,
        },
        include_unit_ids=("DETERMINISTIC_VALUATION",),
    )


def run_path(*, leak_market_price: bool = False, mutate_ledger_after_compile: bool = False):
    ledger, hypotheses, bridges, specs = build_inputs()
    initial_data = {
        "target_id": "T",
        "evidence_ledger": ledger,
        "hypotheses": hypotheses,
        "bridges": bridges,
        "assumption_specs": specs,
        "bridge_input_map": {},
        "scenario_binding_spec": ScenarioBindingSpec(
            ("Bear", "Base", "Bull"),
            ("normalized_ebitda", "normalized_multiple", "ownership", "ev_adjustment", "diluted_shares"),
        ),
        "industry_snapshot_hash": "INDUSTRY_HASH",
        "source_snapshot_hash": "SOURCE_HASH",
    }
    if leak_market_price:
        initial_data["current_market_price"] = 12345

    valuation = deterministic_valuation_adapter(
        registry=default_evaluator_registry(),
        plan=valuation_plan(),
    )

    def valuation_with_optional_ledger_mutation(context):
        if mutate_ledger_after_compile:
            context.data["evidence_ledger"].append(
                EvidenceRecord(
                    id="E:LATE",
                    target="T",
                    metric="late_discovery",
                    value=1,
                    unit="dimensionless",
                    source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
                    effective_date="2026-06-30",
                    observed_date="2026-08-25",
                    source_name="late filing",
                    source_ref="filing#late",
                    source_grade="A",
                    confidence=1.0,
                    segment="core",
                )
            )
        return valuation(context)

    sequence = (
        "EVIDENCE_LEDGER",
        "SCENARIO_BUILD",
        "DETERMINISTIC_VALUATION",
        "AUDIT_GATE",
        "INTRINSIC_VALUE_FREEZE",
    )
    return run_controlled_workflow(
        run_id="GENERIC-E2E-LEAK" if leak_market_price else "GENERIC-E2E",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=sequence,
        adapters={
            "EVIDENCE_LEDGER": evidence_ledger_adapter(),
            "SCENARIO_BUILD": scenario_build_adapter(),
            "DETERMINISTIC_VALUATION": valuation_with_optional_ledger_mutation,
            "AUDIT_GATE": generic_audit_adapter(impact_config=impact_config()),
        },
        required_stages=sequence,
        initial_data=initial_data,
    )


def test_evidence_to_freeze_token_generic_path_passes_without_market_data():
    result = run_path()
    assert result.blocked_reasons == ()
    assert result.freeze_token is not None
    assert [trace.status for trace in result.stage_traces] == [
        StageStatus.PASS,
        StageStatus.PASS,
        StageStatus.PASS,
        StageStatus.PASS,
        StageStatus.PASS,
    ]
    assert result.data["expected_value_per_share"] is None
    base = next(item for item in result.data["intrinsic_scenario_values"] if item.scenario_id == "Base")
    assert base.value_per_share == Decimal("70000")
    assert result.freeze_token.ledger_snapshot_hash == result.data["ledger_snapshot_hash"]
    assert result.freeze_token.assumption_set_hash == result.data["assumption_set_hash"]
    assert result.freeze_token.valuation_hash == result.data["valuation_hash"]
    assert result.freeze_token.audit_hash == result.data["audit_hash"]
    observation = next(
        item
        for item in result.data["decision_impact_batch"].module_observations
        if item.module_id == "DETERMINISTIC_VALUATION"
    )
    assert observation.assessment is not None
    assert observation.assessment.classification is ImpactClassification.DECISION_MATERIAL
    covered = {item.module_id for item in result.data["runtime_doctrine_coverage"]}
    assert {"EVIDENCE_LEDGER", "ASSUMPTION_COMPILER", "SCENARIO_ENGINE", "DETERMINISTIC_VALUATION", "SOTP_AGGREGATOR", "DECISION_IMPACT", "AUDIT_GATE", "INTRINSIC_FREEZE"}.issubset(covered)


def test_evidence_ledger_drift_after_compilation_blocks_before_freeze():
    result = run_path(mutate_ledger_after_compile=True)
    assert result.freeze_token is None
    assert result.stage_traces[-1].stage == "AUDIT_GATE"
    assert result.stage_traces[-1].status is StageStatus.BLOCKED
    assert "EvidenceLedger content no longer matches ledger_snapshot_hash" in result.stage_traces[-1].rationale


def test_market_price_leak_blocks_before_intrinsic_freeze():
    result = run_path(leak_market_price=True)
    assert result.freeze_token is None
    assert result.blocked_reasons
    assert result.stage_traces[-1].stage == "AUDIT_GATE"
    assert result.stage_traces[-1].status is StageStatus.BLOCKED
    assert "leaked keys" in result.stage_traces[-1].rationale
