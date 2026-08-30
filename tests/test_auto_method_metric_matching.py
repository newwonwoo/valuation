from valuation_engine.auto_method_routing import auto_bridge_required_assumption_keys
from valuation_engine.module_plan import ModuleRequirementPlan, SegmentModuleRequirementPlan


def test_scenario_qualified_metrics_satisfy_compiler_key_semantics():
    segment = SegmentModuleRequirementPlan(
        segment_id="core",
        sector_adapter="materials",
        archetypes=("commodity_price_taker",),
        required_evidence=("benchmark_price",),
        required_kpis=("price",),
        mandatory_scanners=("commodity_cycle",),
        kill_conditions=("cycle thesis fails",),
        normalization_rules=("midcycle",),
        beta_peer_features=(),
        per_peer_features=(),
        scenario_variables=("price",),
        funding_scans=(),
        terminal_policies=("midcycle terminal",),
        double_count_traps=(),
        forbidden_methods=(),
        allowed_valuation_methods=("normalized_multiple",),
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

    keys = auto_bridge_required_assumption_keys(
        plan,
        evidence_metrics=frozenset(
            {
                "bear_normalized_ebitda",
                "bull_normalized_multiple",
                "base_ownership",
                "base_ev_adjustment",
                "base_diluted_shares",
            }
        ),
        forecast_years=5,
    )

    assert "normalized_ebitda" in keys
    assert "normalized_multiple" in keys
