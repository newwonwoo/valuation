from __future__ import annotations

from types import SimpleNamespace

from valuation_engine.auto_method_routing import (
    AUTO_METHOD_ROUTING_FLAG,
    AUTO_METHOD_ROUTING_FORECAST_YEARS,
    auto_bridge_required_assumption_keys,
    enable_auto_method_routing,
)
from valuation_engine.cli_runtime import LiveAnalysisRequest
from valuation_engine.cold_start_probe import (
    PROBE_COMPANY_NAME,
    _staff_scripts,
    probe_network,
    probe_runtime_spec,
)
from valuation_engine.generic_live_providers import build_generic_kr_runtime_factory
from valuation_engine.llm_transport import ScriptedTransport
from valuation_engine.method_capabilities import load_default_method_capability_registry
from valuation_engine.module_plan import ModuleRequirementPlan, SegmentModuleRequirementPlan
from valuation_engine.records import CalibrationStatus
from valuation_engine.scenario_binding import BoundScenario, BoundScenarioSet
from valuation_engine.strict_live_runtime import run_prism
from valuation_engine.valuation_method_intent import valuation_method_intent_adapter


def _plan() -> ModuleRequirementPlan:
    segment = SegmentModuleRequirementPlan(
        segment_id="core",
        sector_adapter="materials",
        archetypes=("commodity_price_taker",),
        required_evidence=("benchmark_price",),
        required_kpis=("price",),
        mandatory_scanners=("commodity_cycle",),
        kill_conditions=("price thesis fails",),
        normalization_rules=("midcycle",),
        beta_peer_features=(),
        per_peer_features=(),
        scenario_variables=("price",),
        funding_scans=(),
        terminal_policies=("midcycle terminal",),
        double_count_traps=(),
        forbidden_methods=(),
        allowed_valuation_methods=(
            "midcycle_price_volume_dcf",
            "normalized_multiple",
        ),
    )
    plan = ModuleRequirementPlan(
        segments=(segment,),
        common_core_modules=("evidence_gate",),
        required_evidence=segment.required_evidence,
        required_kpis=segment.required_kpis,
        mandatory_scanners=segment.mandatory_scanners,
        kill_conditions=segment.kill_conditions,
        scenario_variables=segment.scenario_variables,
        double_count_traps=(),
        forbidden_methods=(),
    )
    plan.validate()
    return plan


def test_bridge_prepares_only_evidence_complete_candidate_assumptions():
    keys = auto_bridge_required_assumption_keys(
        _plan(),
        evidence_metrics=frozenset(
            {
                "normalized_ebitda",
                "normalized_multiple",
                "ownership",
                "ev_adjustment",
                "diluted_shares",
            }
        ),
        forecast_years=5,
    )
    assert "normalized_ebitda" in keys
    assert "normalized_multiple" in keys
    assert "ownership" in keys
    assert "ev_adjustment" in keys
    assert "diluted_shares" in keys
    assert "revenue_year_1" not in keys


def test_formal_method_intent_selects_the_only_compiled_feasible_method():
    scenario = BoundScenario(
        scenario_id="Base",
        assumptions=(
            SimpleNamespace(key="normalized_ebitda"),
            SimpleNamespace(key="normalized_multiple"),
            SimpleNamespace(key="ownership"),
            SimpleNamespace(key="ev_adjustment"),
            SimpleNamespace(key="diluted_shares"),
        ),
    )
    scenarios = BoundScenarioSet(
        target_id="KR:DART:TEST",
        scenarios=(scenario,),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        scenario_set_hash="scenario-hash",
    )
    context = SimpleNamespace(
        data={
            "module_requirement_plan": _plan(),
            "bound_scenario_set": scenarios,
            AUTO_METHOD_ROUTING_FLAG: True,
            AUTO_METHOD_ROUTING_FORECAST_YEARS: 5,
        }
    )
    result = valuation_method_intent_adapter(
        capability_registry=load_default_method_capability_registry(),
        method_choices=(),
    )(context)
    assert not result.blocking
    assert result.outputs["planned_method_choices"] == (
        result.outputs["planned_method_choices"][0],
    )
    choice = result.outputs["planned_method_choices"][0]
    assert choice.archetype == "commodity_price_taker"
    assert choice.method == "normalized_multiple"


def _request(state_root) -> LiveAnalysisRequest:
    return LiveAnalysisRequest(
        command=f"분석시작 {PROBE_COMPANY_NAME}",
        company_query=PROBE_COMPANY_NAME,
        state_root=state_root,
        run_id="AUTO-METHOD-COLD-START",
        jurisdiction="KR",
    )


def _auto_probe_factory():
    registry = load_default_method_capability_registry()
    base = build_generic_kr_runtime_factory(
        network=probe_network(),
        transport=ScriptedTransport(_staff_scripts()),
        spec=probe_runtime_spec(),
        capability_registry=registry,
    )
    return enable_auto_method_routing(
        base,
        forecast_years=probe_runtime_spec().forecast_years,
        scenario_ids=probe_runtime_spec().scenario_ids,
        capability_registry=registry,
    )


def test_auto_factory_removes_preselected_method_but_retains_declared_inputs(tmp_path):
    factory = _auto_probe_factory()
    assert factory.method_choices == ()
    assert factory.initial_data[AUTO_METHOD_ROUTING_FLAG] is True
    required = factory.additional_required_evidence[factory.filing.segment_id]
    assert "normalized_ebitda" in required
    assert "normalized_multiple" in required
    assert "ownership" in required
    assert "diluted_shares" in required

    config = factory(_request(tmp_path))
    assert config.method_choices == ()
    assert config.scenario_binding_spec.required_keys == (
        "ownership",
        "diluted_shares",
    )


def test_unseen_company_completes_without_predeclared_valuation_method(tmp_path):
    result = run_prism(_auto_probe_factory()(_request(tmp_path)))
    result.validate_canonical()
    assert result.result.completed
    intent = result.result.data["valuation_method_intent"]
    assert intent.ready
    assert intent.method_choices()[0].method == "normalized_multiple"
