from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.method_capabilities import load_default_method_capability_registry
from valuation_engine.module_plan import ModuleRequirementPlan, SegmentModuleRequirementPlan
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.valuation_method_intent import (
    resolve_valuation_method_intent,
    valuation_method_intent_adapter,
)
from valuation_engine.valuation_plan_compiler import SegmentMethodChoice, ValuationPlanStatus


def segment(archetype: str, methods: tuple[str, ...]) -> SegmentModuleRequirementPlan:
    value = SegmentModuleRequirementPlan(
        segment_id="core",
        sector_adapter="test.adapter",
        archetypes=(archetype,),
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
        allowed_valuation_methods=methods,
    )
    value.validate()
    return value


def plan(item: SegmentModuleRequirementPlan) -> ModuleRequirementPlan:
    result = ModuleRequirementPlan(
        segments=(item,),
        common_core_modules=("evidence_gate",),
        required_evidence=item.required_evidence,
        required_kpis=item.required_kpis,
        mandatory_scanners=item.mandatory_scanners,
        kill_conditions=item.kill_conditions,
        scenario_variables=item.scenario_variables,
        double_count_traps=item.double_count_traps,
        forbidden_methods=item.forbidden_methods,
    )
    result.validate()
    return result


def test_unique_primary_method_resolves_before_risk_and_per_keeps_risk_chain_active():
    module_plan = plan(
        segment("capacity_manufacturing", ("driver_dcf", "warranted_per"))
    )
    result = resolve_valuation_method_intent(
        module_plan,
        capability_registry=load_default_method_capability_registry(),
    )
    assert result.status is ValuationPlanStatus.READY
    assert result.ready
    assert result.segments[0].selected_method == "driver_dcf"
    assert result.warranted_per_segments == ("core",)
    assert result.requires_beta
    assert result.requires_wacc
    assert result.method_choices() == (
        SegmentMethodChoice("core", "capacity_manufacturing", "driver_dcf", None),
    )


def test_multiple_primary_economic_methods_stop_before_beta_wacc():
    module_plan = plan(
        segment(
            "commodity_price_taker",
            ("midcycle_price_volume_dcf", "normalized_multiple"),
        )
    )
    adapter = valuation_method_intent_adapter(
        capability_registry=load_default_method_capability_registry()
    )
    result = adapter(
        OrchestratorContext(
            "RUN",
            ExecutionMode.LIVE_PRIMARY,
            {"module_requirement_plan": module_plan},
        )
    )
    assert result.status is StageStatus.AWAITING_USER_DECISION
    assert result.blocking
    intent = result.outputs["valuation_method_intent"]
    assert intent.status is ValuationPlanStatus.METHOD_CHOICE_REQUIRED
    assert set(intent.segments[0].candidate_bindings) == {
        "commodity_price_taker/midcycle_price_volume_dcf",
        "commodity_price_taker/normalized_multiple",
    }


def test_explicit_normalized_multiple_choice_can_skip_beta_and_wacc():
    module_plan = plan(
        segment(
            "commodity_price_taker",
            ("midcycle_price_volume_dcf", "normalized_multiple"),
        )
    )
    result = resolve_valuation_method_intent(
        module_plan,
        capability_registry=load_default_method_capability_registry(),
        method_choices=(
            SegmentMethodChoice(
                "core",
                "commodity_price_taker",
                "normalized_multiple",
                "1",
            ),
        ),
    )
    assert result.ready
    assert not result.requires_beta
    assert not result.requires_wacc
    assert result.method_choices()[0].version == "1"


def test_unimplemented_financial_methods_fail_before_risk_provider_loading():
    module_plan = plan(
        segment(
            "financial_balance_sheet",
            ("ddm", "pb_roe", "residual_income"),
        )
    )
    adapter = valuation_method_intent_adapter(
        capability_registry=load_default_method_capability_registry()
    )
    result = adapter(
        OrchestratorContext(
            "RUN",
            ExecutionMode.LIVE_PRIMARY,
            {"module_requirement_plan": module_plan},
        )
    )
    assert result.status is StageStatus.NOT_IMPLEMENTED
    assert result.blocking
    assert result.outputs["valuation_method_intent"].status is ValuationPlanStatus.CAPABILITY_GAP
