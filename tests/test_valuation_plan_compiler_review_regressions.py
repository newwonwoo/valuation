from decimal import Decimal

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.dcf_evaluators import ExplicitFCFFDCFEvaluator
from valuation_engine.evaluator_registry import EvaluatorRegistry
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
from valuation_engine.valuation_plan_compiler import (
    CompanyValuationPlanInputs,
    SegmentMethodChoice,
    SegmentValueBinding,
    ValuationPlanStatus,
    compile_company_valuation_plan,
    valuation_capability_registry_hash,
    valuation_module_plan_hash,
)


def _assumption(key: str, value: str, unit: str) -> CompiledAssumption:
    return CompiledAssumption(
        key=key,
        scenario_id="BASE",
        measure=Measure(Decimal(value), unit, "2026-06-30"),
        bridge_id=f"B-{key}",
        evidence_ids=(f"E-{key}",),
        hypothesis_id=f"H-{key}",
        economic_path_id=f"path:{key}",
        transform_id="identity_observation",
        input_evidence_hash=f"HASH-{key}",
    )


def _scenario_set() -> BoundScenarioSet:
    assumptions = (
        _assumption("fcff_year_1", "10", "KRW_billion"),
        _assumption("terminal_growth", "0.02", "ratio"),
        _assumption("terminal_roic", "0.10", "ratio"),
        _assumption("ownership", "1", "ratio"),
        _assumption("net_debt", "0", "KRW_billion"),
        _assumption("shares", "10", "shares"),
    )
    return BoundScenarioSet(
        target_id="T",
        scenarios=(BoundScenario("BASE", assumptions),),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        scenario_set_hash="HASH",
    )


def _module_plan() -> ModuleRequirementPlan:
    segment = SegmentModuleRequirementPlan(
        segment_id="core",
        sector_adapter="test.adapter",
        archetypes=("capacity_manufacturing",),
        required_evidence=("revenue",),
        required_kpis=("revenue",),
        mandatory_scanners=("TEST",),
        kill_conditions=("kill",),
        normalization_rules=("normalize",),
        beta_peer_features=("risk",),
        per_peer_features=("quality",),
        scenario_variables=("revenue",),
        funding_scans=(),
        terminal_policies=("terminal",),
        double_count_traps=("trap",),
        forbidden_methods=(),
        allowed_valuation_methods=("driver_dcf", "warranted_per"),
    )
    return ModuleRequirementPlan(
        segments=(segment,),
        common_core_modules=("evidence_gate",),
        required_evidence=segment.required_evidence,
        required_kpis=segment.required_kpis,
        mandatory_scanners=segment.mandatory_scanners,
        kill_conditions=segment.kill_conditions,
        scenario_variables=segment.scenario_variables,
        double_count_traps=segment.double_count_traps,
        forbidden_methods=(),
    )


def _inputs() -> CompanyValuationPlanInputs:
    return CompanyValuationPlanInputs(
        reporting_unit="KRW_billion",
        diluted_shares_key="shares",
        segment_bindings=(
            SegmentValueBinding(
                "core",
                "asset",
                "ownership",
                "net_debt",
            ),
        ),
    )


def test_explicit_unregistered_version_is_capability_gap():
    registry = EvaluatorRegistry()
    registry.register(
        ExplicitFCFFDCFEvaluator(
            "capacity_manufacturing",
            "driver_dcf",
            "v1",
            1,
            Decimal("0.08"),
            "wacc:fixture",
            beta_path_id="beta:fixture",
        )
    )
    result = compile_company_valuation_plan(
        _module_plan(),
        _scenario_set(),
        evaluator_registry=registry,
        capability_registry=load_default_method_capability_registry(),
        inputs=_inputs(),
        method_choices=(
            SegmentMethodChoice(
                "core",
                "capacity_manufacturing",
                "driver_dcf",
                "v9",
            ),
        ),
    )
    assert result.status is ValuationPlanStatus.CAPABILITY_GAP
    assert "v9" in result.segment_resolutions[0].rationale
    assert "not registered" in result.segment_resolutions[0].rationale


def test_plan_loader_keyerror_is_recovery_not_evaluator_not_implemented():
    module = _module_plan()
    capability_registry = load_default_method_capability_registry()
    adapter = deterministic_valuation_adapter(
        registry=EvaluatorRegistry(),
        plan_loader=lambda context, registry: context.data[
            "missing_upstream_plan_input"
        ],
    )
    result = adapter(
        OrchestratorContext(
            "RUN",
            ExecutionMode.LIVE_PRIMARY,
            {
                "bound_scenario_set": _scenario_set(),
                "module_requirement_plan": module,
                "valuation_module_plan_hash": valuation_module_plan_hash(
                    module
                ),
                "valuation_capability_registry_hash": (
                    valuation_capability_registry_hash(capability_registry)
                ),
            },
        )
    )
    assert result.status is StageStatus.RECOVERY_REQUIRED
    assert result.blocking
    assert "plan loader" in result.rationale
    assert "missing upstream context" in result.rationale
