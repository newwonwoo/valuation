from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.method_capabilities import (
    load_default_method_capability_registry,
)
from valuation_engine.module_plan import (
    ModuleRequirementPlan,
    SegmentModuleRequirementPlan,
)
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.valuation_method_intent import (
    resolve_valuation_method_intent,
    valuation_method_intent_adapter,
)
from valuation_engine.valuation_plan_compiler import (
    SegmentMethodChoice,
    ValuationPlanStatus,
    valuation_capability_registry_hash,
    valuation_method_choices_hash,
    valuation_module_plan_hash,
)


def segment(
    archetype: str,
    methods: tuple[str, ...],
    *,
    segment_id: str = "core",
) -> SegmentModuleRequirementPlan:
    value = SegmentModuleRequirementPlan(
        segment_id=segment_id,
        sector_adapter=f"test.{segment_id}",
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


def plan(*items: SegmentModuleRequirementPlan) -> ModuleRequirementPlan:
    result = ModuleRequirementPlan(
        segments=tuple(items),
        common_core_modules=("evidence_gate",),
        required_evidence=tuple(
            dict.fromkeys(
                metric
                for item in items
                for metric in item.required_evidence
            )
        ),
        required_kpis=tuple(
            dict.fromkeys(
                metric
                for item in items
                for metric in item.required_kpis
            )
        ),
        mandatory_scanners=tuple(
            dict.fromkeys(
                scanner
                for item in items
                for scanner in item.mandatory_scanners
            )
        ),
        kill_conditions=tuple(
            dict.fromkeys(
                condition
                for item in items
                for condition in item.kill_conditions
            )
        ),
        scenario_variables=tuple(
            dict.fromkeys(
                variable
                for item in items
                for variable in item.scenario_variables
            )
        ),
        double_count_traps=tuple(
            dict.fromkeys(
                trap
                for item in items
                for trap in item.double_count_traps
            )
        ),
        forbidden_methods=tuple(
            dict.fromkeys(
                method
                for item in items
                for method in item.forbidden_methods
            )
        ),
    )
    result.validate()
    return result


def test_unique_primary_method_resolves_before_risk_and_per_keeps_risk_chain_active():
    module_plan = plan(
        segment(
            "capacity_manufacturing",
            ("driver_dcf", "warranted_per"),
        )
    )
    capability_registry = load_default_method_capability_registry()
    result = resolve_valuation_method_intent(
        module_plan,
        capability_registry=capability_registry,
    )
    assert result.status is ValuationPlanStatus.READY
    assert result.ready
    assert result.segments[0].selected_method == "driver_dcf"
    assert result.warranted_per_segments == ("core",)
    assert result.requires_beta
    assert result.requires_wacc
    assert result.module_plan_hash == valuation_module_plan_hash(module_plan)
    assert result.capability_registry_hash == valuation_capability_registry_hash(
        capability_registry
    )
    assert result.method_choices() == (
        SegmentMethodChoice(
            "core",
            "capacity_manufacturing",
            "driver_dcf",
            None,
        ),
    )


def test_multiple_primary_economic_methods_stop_before_beta_wacc():
    module_plan = plan(
        segment(
            "commodity_price_taker",
            ("midcycle_price_volume_dcf", "normalized_multiple"),
        )
    )
    capability_registry = load_default_method_capability_registry()
    adapter = valuation_method_intent_adapter(
        capability_registry=capability_registry
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
    assert result.outputs["valuation_module_plan_hash"] == (
        valuation_module_plan_hash(module_plan)
    )
    assert result.outputs["valuation_capability_registry_hash"] == (
        valuation_capability_registry_hash(capability_registry)
    )
    assert "planned_method_choices" not in result.outputs
    assert "valuation_method_choices_hash" not in result.outputs
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
    adapter = valuation_method_intent_adapter(
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
    result = adapter(
        OrchestratorContext(
            "RUN",
            ExecutionMode.LIVE_PRIMARY,
            {"module_requirement_plan": module_plan},
        )
    )
    assert result.status is StageStatus.PASS
    intent = result.outputs["valuation_method_intent"]
    assert intent.ready
    assert not intent.requires_beta
    assert not intent.requires_wacc
    assert result.outputs["planned_method_choices"][0].version == "1"
    assert result.outputs["valuation_method_choices_hash"] == (
        valuation_method_choices_hash(
            result.outputs["planned_method_choices"]
        )
    )


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
    assert (
        result.outputs["valuation_method_intent"].status
        is ValuationPlanStatus.CAPABILITY_GAP
    )


def test_capability_gap_dominates_ambiguous_method_intent():
    module_plan = plan(
        segment(
            "commodity_price_taker",
            ("midcycle_price_volume_dcf", "normalized_multiple"),
            segment_id="commodity",
        ),
        segment(
            "financial_balance_sheet",
            ("ddm", "pb_roe", "residual_income"),
            segment_id="financial",
        ),
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
    intent = result.outputs["valuation_method_intent"]
    assert intent.segments[0].status is ValuationPlanStatus.METHOD_CHOICE_REQUIRED
    assert intent.segments[1].status is ValuationPlanStatus.CAPABILITY_GAP
    assert intent.status is ValuationPlanStatus.CAPABILITY_GAP
    assert result.status is StageStatus.NOT_IMPLEMENTED
    assert result.blocking
