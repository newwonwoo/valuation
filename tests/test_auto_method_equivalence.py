from __future__ import annotations

from types import SimpleNamespace

from valuation_engine.auto_method_routing import auto_feasible_method_choices
from valuation_engine.module_plan import ModuleRequirementPlan, SegmentModuleRequirementPlan
from valuation_engine.records import CalibrationStatus
from valuation_engine.scenario_binding import BoundScenario, BoundScenarioSet


def test_same_economic_method_across_two_archetypes_is_one_candidate():
    segment = SegmentModuleRequirementPlan(
        segment_id="core",
        sector_adapter="materials",
        archetypes=("commodity_price_taker", "process_spread"),
        required_evidence=("benchmark_price", "input_price", "output_price"),
        required_kpis=("price", "spread"),
        mandatory_scanners=("commodity_cycle",),
        kill_conditions=("midcycle thesis fails",),
        normalization_rules=("midcycle",),
        beta_peer_features=(),
        per_peer_features=(),
        scenario_variables=("price", "spread"),
        funding_scans=(),
        terminal_policies=("midcycle terminal",),
        double_count_traps=(),
        forbidden_methods=(),
        allowed_valuation_methods=(
            "midcycle_price_volume_dcf",
            "midcycle_spread_dcf",
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
    bound = BoundScenarioSet(
        target_id="KR:DART:TEST",
        scenarios=(scenario,),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        scenario_set_hash="hash",
    )

    choices = auto_feasible_method_choices(
        plan,
        bound,
        forecast_years=5,
    )

    assert len(choices) == 1
    assert choices[0].archetype == "commodity_price_taker"
    assert choices[0].method == "normalized_multiple"
