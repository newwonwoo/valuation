from decimal import Decimal

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.dcf_evaluators import ExplicitFCFFDCFEvaluator
from valuation_engine.evaluator_registry import EvaluatorRegistry, ModelKey, NormalizedMultipleEvaluator
from valuation_engine.method_capabilities import load_default_method_capability_registry
from valuation_engine.module_plan import ModuleRequirementPlan, SegmentModuleRequirementPlan
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.records import CalibrationStatus
from valuation_engine.scenario_binding import BoundScenario, BoundScenarioSet
from valuation_engine.valuation_adapter import deterministic_valuation_adapter
from valuation_engine.valuation_plan_compiler import (
    CompanyValuationPlanInputs,
    SegmentMethodChoice,
    SegmentValueBinding,
    ValuationPlanStatus,
    compile_company_valuation_plan,
)


def assumption(key: str, value: str, unit: str, path: str) -> CompiledAssumption:
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


def scenario_set(*items: CompiledAssumption) -> BoundScenarioSet:
    return BoundScenarioSet(
        target_id="T",
        scenarios=(BoundScenario("BASE", items),),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        scenario_set_hash="SCENARIO-HASH",
    )


def common_assumptions():
    return (
        assumption("fcff_year_1", "10", "KRW_billion", "fcff1"),
        assumption("terminal_growth", "0.02", "ratio", "terminal-growth"),
        assumption("terminal_roic", "0.10", "ratio", "terminal-roic"),
        assumption("ownership", "1", "ratio", "ownership"),
        assumption("net_debt", "0", "KRW_billion", "net-debt"),
        assumption("shares", "10", "shares", "shares"),
    )


def segment(
    archetypes: tuple[str, ...],
    methods: tuple[str, ...],
    *,
    segment_id: str = "core",
) -> SegmentModuleRequirementPlan:
    value = SegmentModuleRequirementPlan(
        segment_id=segment_id,
        sector_adapter="test.adapter",
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


def plan_for(segment_plan: SegmentModuleRequirementPlan) -> ModuleRequirementPlan:
    result = ModuleRequirementPlan(
        segments=(segment_plan,),
        common_core_modules=("evidence_gate",),
        required_evidence=segment_plan.required_evidence,
        required_kpis=segment_plan.required_kpis,
        mandatory_scanners=segment_plan.mandatory_scanners,
        kill_conditions=segment_plan.kill_conditions,
        scenario_variables=segment_plan.scenario_variables,
        double_count_traps=segment_plan.double_count_traps,
        forbidden_methods=segment_plan.forbidden_methods,
    )
    result.validate()
    return result


def inputs() -> CompanyValuationPlanInputs:
    return CompanyValuationPlanInputs(
        reporting_unit="KRW_billion",
        diluted_shares_key="shares",
        segment_bindings=(
            SegmentValueBinding("core", "core-asset", "ownership", "net_debt"),
        ),
    )


def dcf_registry(archetype: str, method: str, *, version: str = "v1") -> EvaluatorRegistry:
    registry = EvaluatorRegistry()
    registry.register(
        ExplicitFCFFDCFEvaluator(
            archetype=archetype,
            method=method,
            version=version,
            forecast_years=1,
            discount_rate=Decimal("0.08"),
            discount_rate_path_id="wacc:fixture",
            beta_path_id="beta:fixture",
        )
    )
    return registry


def test_single_executable_segment_evaluator_is_selected_and_per_stays_cross_method():
    module = plan_for(
        segment(
            ("capacity_manufacturing",),
            ("driver_dcf", "warranted_per"),
        )
    )
    result = compile_company_valuation_plan(
        module,
        scenario_set(*common_assumptions()),
        evaluator_registry=dcf_registry("capacity_manufacturing", "driver_dcf"),
        capability_registry=load_default_method_capability_registry(),
        inputs=inputs(),
    )
    assert result.status is ValuationPlanStatus.READY
    assert result.plan is not None
    assert result.plan.segments[0].model_key == ModelKey("capacity_manufacturing", "driver_dcf", "v1")
    assert result.warranted_per_segments == ("core",)
    assert all(candidate.method != "warranted_per" for candidate in result.segment_resolutions[0].candidates)


def test_multiple_executable_methods_require_explicit_economic_choice():
    module = plan_for(
        segment(
            ("commodity_price_taker",),
            ("midcycle_price_volume_dcf", "normalized_multiple"),
        )
    )
    registry = dcf_registry("commodity_price_taker", "midcycle_price_volume_dcf", version="dcf-v1")
    registry.register(NormalizedMultipleEvaluator("commodity_price_taker"))
    scenarios = scenario_set(
        *common_assumptions(),
        assumption("normalized_ebitda", "12", "KRW_billion", "normalized-ebitda"),
        assumption("normalized_multiple", "7", "multiple", "normalized-multiple"),
    )
    unresolved = compile_company_valuation_plan(
        module,
        scenarios,
        evaluator_registry=registry,
        capability_registry=load_default_method_capability_registry(),
        inputs=inputs(),
    )
    assert unresolved.status is ValuationPlanStatus.METHOD_CHOICE_REQUIRED
    assert unresolved.plan is None
    assert len(unresolved.segment_resolutions[0].candidates) == 2

    selected = compile_company_valuation_plan(
        module,
        scenarios,
        evaluator_registry=registry,
        capability_registry=load_default_method_capability_registry(),
        inputs=inputs(),
        method_choices=(
            SegmentMethodChoice("core", "commodity_price_taker", "normalized_multiple", "1"),
        ),
    )
    assert selected.status is ValuationPlanStatus.READY
    assert selected.plan is not None
    assert selected.plan.segments[0].model_key == ModelKey("commodity_price_taker", "normalized_multiple", "1")


def test_registered_evaluator_with_missing_compiled_inputs_is_assumption_gap():
    module = plan_for(segment(("capacity_manufacturing",), ("driver_dcf", "warranted_per")))
    incomplete = scenario_set(
        assumption("fcff_year_1", "10", "KRW_billion", "fcff1"),
        assumption("terminal_growth", "0.02", "ratio", "terminal-growth"),
        assumption("ownership", "1", "ratio", "ownership"),
        assumption("net_debt", "0", "KRW_billion", "net-debt"),
        assumption("shares", "10", "shares", "shares"),
    )
    result = compile_company_valuation_plan(
        module,
        incomplete,
        evaluator_registry=dcf_registry("capacity_manufacturing", "driver_dcf"),
        capability_registry=load_default_method_capability_registry(),
        inputs=inputs(),
    )
    assert result.status is ValuationPlanStatus.ASSUMPTION_GAP
    assert "BASE/terminal_roic" in result.segment_resolutions[0].rationale


def test_unimplemented_financial_methods_are_capability_gap_not_generic_dcf():
    module = plan_for(
        segment(
            ("financial_balance_sheet",),
            ("ddm", "pb_roe", "residual_income"),
        )
    )
    result = compile_company_valuation_plan(
        module,
        scenario_set(*common_assumptions()),
        evaluator_registry=EvaluatorRegistry(),
        capability_registry=load_default_method_capability_registry(),
        inputs=inputs(),
    )
    assert result.status is ValuationPlanStatus.CAPABILITY_GAP
    assert result.plan is None


def test_sotp_aggregator_is_visible_but_never_compiled_as_segment_model_key():
    module = plan_for(segment(("project_finance",), ("project_npv", "sotp")))
    registry = EvaluatorRegistry()
    from valuation_engine.finite_life_evaluators import FiniteLifeNPVEvaluator

    registry.register(
        FiniteLifeNPVEvaluator(
            "project_finance",
            "project_npv",
            "project-v1",
            1,
            Decimal("0.08"),
            "wacc:fixture",
            "beta:fixture",
        )
    )
    scenarios = scenario_set(
        assumption("cashflow_year_0", "-10", "KRW_billion", "capex"),
        assumption("cashflow_year_1", "20", "KRW_billion", "cashflow1"),
        assumption("ownership", "1", "ratio", "ownership"),
        assumption("net_debt", "0", "KRW_billion", "net-debt"),
        assumption("shares", "10", "shares", "shares"),
    )
    result = compile_company_valuation_plan(
        module,
        scenarios,
        evaluator_registry=registry,
        capability_registry=load_default_method_capability_registry(),
        inputs=inputs(),
    )
    assert result.status is ValuationPlanStatus.READY
    assert result.plan is not None
    assert result.plan.segments[0].model_key.method == "project_npv"
    assert result.aggregator_bindings == ("core:project_finance/sotp",)


def test_deterministic_stage_can_compile_plan_after_registry_load():
    module = plan_for(segment(("capacity_manufacturing",), ("driver_dcf", "warranted_per")))
    scenarios = scenario_set(*common_assumptions())
    registry = dcf_registry("capacity_manufacturing", "driver_dcf")

    def loader(context, effective_registry):
        assert context.data["module_requirement_plan"] is module
        return compile_company_valuation_plan(
            module,
            context.data["bound_scenario_set"],
            evaluator_registry=effective_registry,
            capability_registry=load_default_method_capability_registry(),
            inputs=inputs(),
        )

    adapter = deterministic_valuation_adapter(plan_loader=loader, registry=registry)
    result = adapter(
        OrchestratorContext(
            "RUN",
            ExecutionMode.LIVE_PRIMARY,
            {"bound_scenario_set": scenarios, "module_requirement_plan": module},
        )
    )
    assert result.status is StageStatus.PASS
    assert result.outputs["valuation_plan_compilation"].status is ValuationPlanStatus.READY
    assert result.outputs["warranted_per_segments"] == ("core",)


def test_deterministic_stage_returns_recovery_for_ambiguous_method_selection():
    module = plan_for(
        segment(
            ("commodity_price_taker",),
            ("midcycle_price_volume_dcf", "normalized_multiple"),
        )
    )
    registry = dcf_registry("commodity_price_taker", "midcycle_price_volume_dcf", version="dcf-v1")
    registry.register(NormalizedMultipleEvaluator("commodity_price_taker"))
    scenarios = scenario_set(
        *common_assumptions(),
        assumption("normalized_ebitda", "12", "KRW_billion", "normalized-ebitda"),
        assumption("normalized_multiple", "7", "multiple", "normalized-multiple"),
    )

    adapter = deterministic_valuation_adapter(
        registry=registry,
        plan_loader=lambda context, effective_registry: compile_company_valuation_plan(
            module,
            context.data["bound_scenario_set"],
            evaluator_registry=effective_registry,
            capability_registry=load_default_method_capability_registry(),
            inputs=inputs(),
        ),
    )
    result = adapter(
        OrchestratorContext("RUN", ExecutionMode.LIVE_PRIMARY, {"bound_scenario_set": scenarios})
    )
    assert result.status is StageStatus.RECOVERY_REQUIRED
    assert result.blocking
    assert result.outputs["valuation_plan_compilation"].status is ValuationPlanStatus.METHOD_CHOICE_REQUIRED
