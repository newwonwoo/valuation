from dataclasses import replace
from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption, CompiledAssumptionSet
from valuation_engine.control_plane import (
    DoctrineCoverageEntry,
    ExecutionMode,
    StageStatus,
    issue_freeze_token,
)
from valuation_engine.evaluator_registry import EvaluatorRegistry, NormalizedMultipleEvaluator
from valuation_engine.generic_reporting import render_generic_report
from valuation_engine.impact_adapter import build_generic_decision_outcome
from valuation_engine.method_capabilities import load_default_method_capability_registry
from valuation_engine.module_plan import ModuleRequirementPlan, SegmentModuleRequirementPlan
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.partial_valuation import promote_partial_valuation_plan
from valuation_engine.post_freeze import compare_generic_to_market
from valuation_engine.post_freeze_adapters import market_compare_adapter
from valuation_engine.records import AuditReport, CalibrationStatus, MarketObservation
from valuation_engine.scenario_binding import BoundScenario, BoundScenarioSet
from valuation_engine.valuation_adapter import deterministic_valuation_adapter
from valuation_engine.valuation_execution import (
    IntrinsicValuationScope,
    UnvaluedSegmentStatus,
    execute_company_valuation,
)
from valuation_engine.valuation_plan_compiler import (
    CompanyValuationPlanInputs,
    SegmentValueBinding,
    ValuationPlanStatus,
    compile_company_valuation_plan,
)


def _assumption(key: str, value: str, unit: str, *, scenario: str = "BASE") -> CompiledAssumption:
    return CompiledAssumption(
        key=key,
        scenario_id=scenario,
        measure=Measure(Decimal(value), unit, "2026-06-30"),
        bridge_id=f"B:{scenario}:{key}",
        evidence_ids=(f"E:{scenario}:{key}",),
        hypothesis_id=f"H:{scenario}:{key}",
        economic_path_id=f"PATH:{scenario}:{key}",
        transform_id="identity_observation",
        input_evidence_hash=f"HASH:{scenario}:{key}",
    )


def _scenario_set(*, include_shares: bool = True, include_unvalued_ownership: bool = False) -> BoundScenarioSet:
    assumptions = [
        _assumption("normalized_ebitda", "100", "KRW_billion"),
        _assumption("normalized_multiple", "8", "multiple"),
        _assumption("core_ownership", "1", "ratio"),
        _assumption("core_net_debt", "-100", "KRW_billion"),
    ]
    if include_shares:
        assumptions.append(_assumption("shares", "10", "shares"))
    if include_unvalued_ownership:
        assumptions.append(_assumption("unvalued_ownership", "1", "ratio"))
    return BoundScenarioSet(
        target_id="T",
        scenarios=(BoundScenario("BASE", tuple(assumptions)),),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        scenario_set_hash="SCENARIO-HASH",
    )


def _segment(
    segment_id: str,
    archetype: str,
    methods: tuple[str, ...],
) -> SegmentModuleRequirementPlan:
    result = SegmentModuleRequirementPlan(
        segment_id=segment_id,
        sector_adapter="test.adapter",
        archetypes=(archetype,),
        required_evidence=("revenue",),
        required_kpis=("revenue",),
        mandatory_scanners=("TEST_SCANNER",),
        kill_conditions=("test kill",),
        normalization_rules=("test normalization",),
        beta_peer_features=("risk",),
        per_peer_features=("quality",),
        scenario_variables=("revenue",),
        funding_scans=(),
        terminal_policies=("test terminal",),
        double_count_traps=(),
        forbidden_methods=(),
        allowed_valuation_methods=methods,
    )
    result.validate()
    return result


def _module_plan() -> ModuleRequirementPlan:
    valued = _segment(
        "core",
        "commodity_price_taker",
        ("normalized_multiple",),
    )
    unvalued = _segment(
        "future",
        "capacity_manufacturing",
        ("driver_dcf",),
    )
    plan = ModuleRequirementPlan(
        segments=(valued, unvalued),
        common_core_modules=("evidence_gate",),
        required_evidence=("revenue",),
        required_kpis=("revenue",),
        mandatory_scanners=("TEST_SCANNER",),
        kill_conditions=("test kill",),
        scenario_variables=("revenue",),
        double_count_traps=(),
        forbidden_methods=(),
    )
    plan.validate()
    return plan


def _inputs() -> CompanyValuationPlanInputs:
    return CompanyValuationPlanInputs(
        reporting_unit="KRW_billion",
        diluted_shares_key="shares",
        segment_bindings=(
            SegmentValueBinding(
                "core",
                "core-asset",
                "core_ownership",
                "core_net_debt",
            ),
            SegmentValueBinding(
                "future",
                "future-asset",
                "unvalued_ownership",
                "future_net_debt",
            ),
        ),
    )


def _registry() -> EvaluatorRegistry:
    registry = EvaluatorRegistry()
    registry.register(NormalizedMultipleEvaluator("commodity_price_taker"))
    return registry


def _partial_compilation(*, include_shares: bool = True):
    scenarios = _scenario_set(include_shares=include_shares)
    original = compile_company_valuation_plan(
        _module_plan(),
        scenarios,
        evaluator_registry=_registry(),
        capability_registry=load_default_method_capability_registry(),
        inputs=_inputs(),
    )
    assert original.status is ValuationPlanStatus.CAPABILITY_GAP
    promoted = promote_partial_valuation_plan(
        original,
        inputs=_inputs(),
        scenario_set=scenarios,
    )
    return scenarios, original, promoted


def _partial_result():
    scenarios, _, promoted = _partial_compilation()
    assert promoted.plan is not None
    return execute_company_valuation(
        scenarios,
        plan=promoted.plan,
        registry=_registry(),
    )


def _freeze_token(run_id: str):
    coverage = (DoctrineCoverageEntry("PARTIAL", StageStatus.PASS, "ready"),)
    return issue_freeze_token(
        run_id=run_id,
        audit_passed=True,
        coverage_entries=coverage,
        expected_module_ids=("PARTIAL",),
        ledger_snapshot_hash="ledger",
        assumption_set_hash="assumptions",
        valuation_hash="valuation",
        audit_hash="audit",
        industry_snapshot_hash="industry",
        source_snapshot_hash="source",
    )


def test_segment_local_capability_gap_promotes_to_partial_without_zero_filling():
    _, original, promoted = _partial_compilation()
    assert original.plan is None
    assert promoted.status is ValuationPlanStatus.CAPABILITY_GAP
    assert promoted.plan is not None
    assert promoted.plan.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC
    assert tuple(item.segment_id for item in promoted.plan.segments) == ("core",)
    assert tuple(item.segment_id for item in promoted.plan.unvalued_segments) == ("future",)
    unvalued = promoted.plan.unvalued_segments[0]
    assert unvalued.status is UnvaluedSegmentStatus.UNVALUED_NOT_ZERO
    assert unvalued.resolution_status == "CAPABILITY_GAP"


def test_unvalued_segment_ownership_is_not_required_for_partial_subtotal():
    scenarios, _, promoted = _partial_compilation()
    assert promoted.plan is not None
    assert "BASE/unvalued_ownership" not in promoted.missing_assumptions
    result = execute_company_valuation(scenarios, plan=promoted.plan, registry=_registry())
    assert result.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC
    assert result.scenarios[0].equity_value_amount == Decimal("700")
    assert result.scenarios[0].value_per_share == Decimal("70")


def test_missing_company_common_diluted_shares_prevents_partial_promotion():
    _, _, promoted = _partial_compilation(include_shares=False)
    assert promoted.plan is None
    assert "BASE/shares" in promoted.missing_assumptions


def test_partial_valuation_hash_changes_when_unvalued_contract_changes():
    scenarios, _, promoted = _partial_compilation()
    assert promoted.plan is not None
    first = execute_company_valuation(scenarios, plan=promoted.plan, registry=_registry())
    changed_unvalued = replace(
        promoted.plan.unvalued_segments[0],
        rationale="different unresolved reason",
    )
    changed_plan = replace(promoted.plan, unvalued_segments=(changed_unvalued,))
    second = execute_company_valuation(scenarios, plan=changed_plan, registry=_registry())
    assert first.valuation_hash != second.valuation_hash


def test_deterministic_adapter_returns_warning_and_explicit_partial_scope():
    scenarios, _, promoted = _partial_compilation()
    assert promoted.plan is not None
    context = OrchestratorContext(
        "RUN",
        ExecutionMode.LIVE_PRIMARY,
        {"bound_scenario_set": scenarios},
    )
    result = deterministic_valuation_adapter(
        plan=promoted.plan,
        registry=_registry(),
    )(context)
    assert result.status is StageStatus.WARNING
    assert not result.blocking
    assert result.outputs["valuation_scope"] is IntrinsicValuationScope.PARTIAL_INTRINSIC
    assert result.outputs["unvalued_segments"][0].status is UnvaluedSegmentStatus.UNVALUED_NOT_ZERO


def test_partial_subtotal_cannot_be_compared_to_whole_company_market_price():
    valuation = _partial_result()
    observation = MarketObservation(60.0, "2026-08-25", "market")
    with pytest.raises(ValueError, match="whole-company"):
        compare_generic_to_market(valuation, observation, currency="KRW_billion")

    context = OrchestratorContext(
        "RUN",
        ExecutionMode.LIVE_PRIMARY,
        {
            "generic_valuation_result": valuation,
            "market_observation": observation,
            "market_currency": "KRW_billion",
        },
        freeze_token=_freeze_token("RUN"),
    )
    stage = market_compare_adapter()(context)
    assert stage.status is StageStatus.SKIPPED_NOT_APPLICABLE
    assert "withheld" in stage.rationale
    assert "market_comparison" not in stage.outputs


def test_partial_decision_impact_never_exposes_subtotal_as_full_intrinsic():
    valuation = replace(_partial_result(), expected_value_per_share=Decimal("72"))
    compiled = CompiledAssumptionSet("T", (), "ASSUMPTION-HASH")
    context = OrchestratorContext(
        "RUN",
        ExecutionMode.LIVE_PRIMARY,
        {
            "compiled_assumption_set": compiled,
            "generic_valuation_result": valuation,
            "selected_methods": ("commodity_price_taker/normalized_multiple/1",),
            "route_hash": "ROUTE",
        },
    )
    outcome = build_generic_decision_outcome(context)
    assert outcome.status == "PARTIAL_INTRINSIC"
    assert outcome.intrinsic_value_per_share is None
    assert "partial_intrinsic" in outcome.conclusion_tags


def test_partial_report_labels_subtotal_and_unvalued_not_zero():
    valuation = _partial_result()
    report = render_generic_report(
        {
            "company": "Example",
            "generic_valuation_result": valuation,
            "generic_audit_report": AuditReport(()),
            "doctrine_coverage": (),
        }
    )
    assert "부분 내재가치 — 평가 완료 사업부만 포함" in report
    assert "평가완료 소계" in report
    assert "미평가 사업부 — 0원으로 간주하지 않음" in report
    assert "미평가 사업부는 0원으로 합산하지 않았습니다" in report
