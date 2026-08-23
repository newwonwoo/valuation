from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.dcf_evaluators import ExplicitFCFFDCFEvaluator
from valuation_engine.evaluator_registry import (
    EvaluatorRegistry,
    NormalizedMultipleEvaluator,
)
from valuation_engine.method_capabilities import (
    load_default_method_capability_registry,
)
from valuation_engine.module_plan import (
    ModuleRequirementPlan,
    SegmentModuleRequirementPlan,
)
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.records import CalibrationStatus
from valuation_engine.scenario_binding import BoundScenario, BoundScenarioSet
from valuation_engine.valuation_adapter import deterministic_valuation_adapter
from valuation_engine.valuation_execution import (
    CompanyValuationPlan,
    ParentAdjustmentPlan,
    SegmentValuationPlan,
    default_evaluator_registry,
    execute_company_valuation,
)
from valuation_engine.evaluator_registry import ModelKey
from valuation_engine.valuation_plan_compiler import (
    CompanyValuationPlanInputs,
    SegmentMethodChoice,
    SegmentValueBinding,
    ValuationPlanStatus,
    compile_company_valuation_plan,
)


def assumption(
    key: str,
    value: str,
    unit: str,
    path: str,
) -> CompiledAssumption:
    return CompiledAssumption(
        key=key,
        scenario_id="BASE",
        measure=Measure(Decimal(value), unit, "2026-06-30"),
        bridge_id=f"B-{key}",
        evidence_ids=(f"E-{key}",),
        hypothesis_id=f"H-{key}",
        economic_path_id=path,
        transform_id="identity_observation",
        input_evidence_hash=f"HASH-{key}",
    )


def scenarios(
    *items: CompiledAssumption,
    scenario_hash: str = "SCENARIO-CURRENT",
) -> BoundScenarioSet:
    return BoundScenarioSet(
        target_id="T",
        scenarios=(BoundScenario("BASE", items),),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        scenario_set_hash=scenario_hash,
    )


def segment(
    segment_id: str,
    archetypes: tuple[str, ...],
    methods: tuple[str, ...],
) -> SegmentModuleRequirementPlan:
    value = SegmentModuleRequirementPlan(
        segment_id=segment_id,
        sector_adapter=f"test.{segment_id}",
        archetypes=archetypes,
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
        double_count_traps=("test trap",),
        forbidden_methods=(),
        allowed_valuation_methods=methods,
    )
    value.validate()
    return value


def module_plan(
    *segments: SegmentModuleRequirementPlan,
) -> ModuleRequirementPlan:
    value = ModuleRequirementPlan(
        segments=tuple(segments),
        common_core_modules=("evidence_gate",),
        required_evidence=tuple(
            dict.fromkeys(
                metric
                for item in segments
                for metric in item.required_evidence
            )
        ),
        required_kpis=tuple(
            dict.fromkeys(
                metric
                for item in segments
                for metric in item.required_kpis
            )
        ),
        mandatory_scanners=tuple(
            dict.fromkeys(
                scanner
                for item in segments
                for scanner in item.mandatory_scanners
            )
        ),
        kill_conditions=tuple(
            dict.fromkeys(
                condition
                for item in segments
                for condition in item.kill_conditions
            )
        ),
        scenario_variables=tuple(
            dict.fromkeys(
                variable
                for item in segments
                for variable in item.scenario_variables
            )
        ),
        double_count_traps=tuple(
            dict.fromkeys(
                trap
                for item in segments
                for trap in item.double_count_traps
            )
        ),
        forbidden_methods=tuple(
            dict.fromkeys(
                method
                for item in segments
                for method in item.forbidden_methods
            )
        ),
    )
    value.validate()
    return value


def dcf_evaluator(
    *,
    version: str,
    forecast_years: int,
) -> ExplicitFCFFDCFEvaluator:
    return ExplicitFCFFDCFEvaluator(
        archetype="capacity_manufacturing",
        method="driver_dcf",
        version=version,
        forecast_years=forecast_years,
        discount_rate=Decimal("0.08"),
        discount_rate_path_id="wacc:fixture",
        beta_path_id="beta:fixture",
    )


def dcf_inputs() -> CompanyValuationPlanInputs:
    return CompanyValuationPlanInputs(
        reporting_unit="KRW_billion",
        diluted_shares_key="shares",
        segment_bindings=(
            SegmentValueBinding(
                "core",
                "core-asset",
                "ownership",
                "net_debt",
            ),
        ),
    )


def dcf_assumptions(
    *,
    include_terminal_roic: bool = True,
) -> tuple[CompiledAssumption, ...]:
    items = [
        assumption("fcff_year_1", "10", "KRW_billion", "fcff-1"),
        assumption("terminal_growth", "0.02", "ratio", "terminal-growth"),
        assumption("ownership", "1", "ratio", "ownership"),
        assumption("net_debt", "0", "KRW_billion", "net-debt"),
        assumption("shares", "10", "shares", "shares"),
    ]
    if include_terminal_roic:
        items.append(
            assumption("terminal_roic", "0.10", "ratio", "terminal-roic")
        )
    return tuple(items)


def test_compilation_diagnostics_include_evaluator_assumption_gaps():
    registry = EvaluatorRegistry()
    registry.register(dcf_evaluator(version="v1", forecast_years=1))
    result = compile_company_valuation_plan(
        module_plan(
            segment(
                "core",
                ("capacity_manufacturing",),
                ("driver_dcf",),
            )
        ),
        scenarios(*dcf_assumptions(include_terminal_roic=False)),
        evaluator_registry=registry,
        capability_registry=load_default_method_capability_registry(),
        inputs=dcf_inputs(),
    )
    assert result.status is ValuationPlanStatus.ASSUMPTION_GAP
    assert result.missing_assumptions == ("BASE/terminal_roic",)
    assert result.segment_resolutions[0].missing_assumptions == (
        "BASE/terminal_roic",
    )


def test_explicit_version_reports_only_that_versions_missing_inputs():
    registry = EvaluatorRegistry()
    registry.register(dcf_evaluator(version="v1", forecast_years=1))
    registry.register(dcf_evaluator(version="v2", forecast_years=2))
    result = compile_company_valuation_plan(
        module_plan(
            segment(
                "core",
                ("capacity_manufacturing",),
                ("driver_dcf",),
            )
        ),
        scenarios(*dcf_assumptions(include_terminal_roic=False)),
        evaluator_registry=registry,
        capability_registry=load_default_method_capability_registry(),
        inputs=dcf_inputs(),
        method_choices=(
            SegmentMethodChoice(
                "core",
                "capacity_manufacturing",
                "driver_dcf",
                "v1",
            ),
        ),
    )
    assert result.status is ValuationPlanStatus.ASSUMPTION_GAP
    assert result.missing_assumptions == ("BASE/terminal_roic",)
    assert "fcff_year_2" not in result.segment_resolutions[0].rationale


def test_capability_gap_dominates_recoverable_method_choice():
    commodity = segment(
        "commodity",
        ("commodity_price_taker",),
        ("midcycle_price_volume_dcf", "normalized_multiple"),
    )
    financial = segment(
        "financial",
        ("financial_balance_sheet",),
        ("ddm", "pb_roe", "residual_income"),
    )
    registry = EvaluatorRegistry()
    registry.register(
        ExplicitFCFFDCFEvaluator(
            archetype="commodity_price_taker",
            method="midcycle_price_volume_dcf",
            version="dcf-v1",
            forecast_years=1,
            discount_rate=Decimal("0.08"),
            discount_rate_path_id="wacc:fixture",
            beta_path_id="beta:fixture",
        )
    )
    registry.register(NormalizedMultipleEvaluator("commodity_price_taker"))
    inputs = CompanyValuationPlanInputs(
        reporting_unit="KRW_billion",
        diluted_shares_key="shares",
        segment_bindings=(
            SegmentValueBinding(
                "commodity",
                "commodity-asset",
                "ownership_commodity",
                "debt_commodity",
            ),
            SegmentValueBinding(
                "financial",
                "financial-asset",
                "ownership_financial",
                None,
            ),
        ),
    )
    result = compile_company_valuation_plan(
        module_plan(commodity, financial),
        scenarios(
            assumption("fcff_year_1", "10", "KRW_billion", "fcff-1"),
            assumption("terminal_growth", "0.02", "ratio", "terminal-growth"),
            assumption("terminal_roic", "0.10", "ratio", "terminal-roic"),
            assumption("normalized_ebitda", "12", "KRW_billion", "ebitda"),
            assumption("normalized_multiple", "7", "multiple", "multiple"),
            assumption("ownership_commodity", "1", "ratio", "own-c"),
            assumption("ownership_financial", "1", "ratio", "own-f"),
            assumption("debt_commodity", "0", "KRW_billion", "debt-c"),
            assumption("shares", "10", "shares", "shares"),
        ),
        evaluator_registry=registry,
        capability_registry=load_default_method_capability_registry(),
        inputs=inputs,
    )
    assert result.segment_resolutions[0].status is ValuationPlanStatus.METHOD_CHOICE_REQUIRED
    assert result.segment_resolutions[1].status is ValuationPlanStatus.CAPABILITY_GAP
    assert result.status is ValuationPlanStatus.CAPABILITY_GAP


def test_plan_inputs_reject_reused_adjustment_assumption_keys():
    value = CompanyValuationPlanInputs(
        reporting_unit="KRW_billion",
        diluted_shares_key="shares",
        segment_bindings=(
            SegmentValueBinding("A", "asset-a", "ownership_a", "shared_debt"),
            SegmentValueBinding("B", "asset-b", "ownership_b", "shared_debt"),
        ),
    )
    with pytest.raises(ValueError, match="reuse EV-to-equity"):
        value.validate(expected_segment_ids=("A", "B"))


def test_plan_inputs_reject_segment_parent_adjustment_key_overlap():
    value = CompanyValuationPlanInputs(
        reporting_unit="KRW_billion",
        diluted_shares_key="shares",
        segment_bindings=(
            SegmentValueBinding("A", "asset-a", "ownership_a", "shared_debt"),
        ),
        parent_adjustments=(
            ParentAdjustmentPlan("parent-adjustment", "shared_debt"),
        ),
    )
    with pytest.raises(ValueError, match="reuse EV-to-equity"):
        value.validate(expected_segment_ids=("A",))


def test_loaded_compilation_must_match_current_scenario_set_hash():
    module = module_plan(
        segment(
            "core",
            ("capacity_manufacturing",),
            ("driver_dcf",),
        )
    )
    registry = EvaluatorRegistry()
    registry.register(dcf_evaluator(version="v1", forecast_years=1))
    cached = compile_company_valuation_plan(
        module,
        scenarios(*dcf_assumptions(), scenario_hash="SCENARIO-OLD"),
        evaluator_registry=registry,
        capability_registry=load_default_method_capability_registry(),
        inputs=dcf_inputs(),
    )
    current = scenarios(
        *dcf_assumptions(),
        scenario_hash="SCENARIO-CURRENT",
    )
    adapter = deterministic_valuation_adapter(
        plan_loader=lambda context, effective_registry: cached,
        registry=registry,
    )
    result = adapter(
        OrchestratorContext(
            "RUN",
            ExecutionMode.LIVE_PRIMARY,
            {"bound_scenario_set": current},
        )
    )
    assert result.status is StageStatus.BLOCKED
    assert result.blocking
    assert "scenario-set hash" in result.rationale
    assert result.outputs["current_scenario_set_hash"] == "SCENARIO-CURRENT"


def test_execution_rejects_distinct_adjustment_keys_with_same_economic_path():
    plan = CompanyValuationPlan(
        segments=(
            SegmentValuationPlan(
                asset_id="core",
                segment_id="core",
                model_key=ModelKey(
                    "commodity_price_taker",
                    "normalized_multiple",
                    "1",
                ),
                ownership_key="ownership",
                ev_to_equity_adjustment_key="segment_debt",
            ),
        ),
        reporting_unit="KRW_billion",
        diluted_shares_key="shares",
        parent_adjustments=(
            ParentAdjustmentPlan("parent-adjustment", "parent_debt"),
        ),
    )
    current = scenarios(
        assumption("normalized_ebitda", "100", "KRW_billion", "ebitda"),
        assumption("normalized_multiple", "8", "multiple", "multiple"),
        assumption("ownership", "1", "ratio", "ownership"),
        assumption("segment_debt", "-50", "KRW_billion", "shared-adjustment"),
        assumption("parent_debt", "-20", "KRW_billion", "shared-adjustment"),
        assumption("shares", "10", "shares", "shares"),
    )
    with pytest.raises(ValueError, match="reuses valuation adjustment economic paths"):
        execute_company_valuation(
            current,
            plan=plan,
            registry=default_evaluator_registry(),
        )
