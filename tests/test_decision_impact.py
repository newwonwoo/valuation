from valuation_engine.decision_impact import (
    DecisionOutcome,
    ImpactClassification,
    ImpactPolicy,
    ModuleHistoryEntry,
    ModuleImpactTrace,
    ResearchEffort,
    ResearchIntensity,
    compare_module_counterfactual,
    recommend_research_intensity,
    wasted_research_entries,
)


def test_value_material_module_is_detected():
    policy = ImpactPolicy(value_materiality_pct=0.05)
    baseline = DecisionOutcome("COMPLETED", intrinsic_value_per_share=120.0, assumption_hash="a2")
    counter = DecisionOutcome("COMPLETED", intrinsic_value_per_share=100.0, assumption_hash="a1")
    result = compare_module_counterfactual("capacity", baseline=baseline, counterfactual=counter, policy=policy)
    assert result.classification is ImpactClassification.VALUE_MATERIAL
    assert result.material
    assert round(result.value_delta_pct or 0, 4) == 0.2


def test_gate_with_zero_value_delta_can_be_guardrail_critical():
    baseline = DecisionOutcome("COMPLETED", intrinsic_value_per_share=100.0)
    counter = DecisionOutcome("COMPLETED", intrinsic_value_per_share=100.0)
    result = compare_module_counterfactual(
        "street_isolation",
        baseline=baseline,
        counterfactual=counter,
        guardrail_violation_detected=True,
    )
    assert result.classification is ImpactClassification.GUARDRAIL_CRITICAL
    assert result.material


def test_route_or_method_change_is_decision_material_even_without_value():
    baseline = DecisionOutcome("PARTIAL_INTRINSIC", route_hash="r2", selected_methods=("sotp",))
    counter = DecisionOutcome("VALUATION_BLOCKED", route_hash="r1", selected_methods=("generic_dcf",))
    result = compare_module_counterfactual("industry_dna", baseline=baseline, counterfactual=counter)
    assert result.classification is ImpactClassification.DECISION_MATERIAL
    assert result.material


def test_active_module_must_have_path_to_conclusion_or_be_guardrail():
    ModuleImpactTrace(
        module_id="backlog",
        evidence_ids=("E1",),
        affected_assumptions=("backlog_conversion",),
        final_output_refs=("intrinsic_value",),
    ).validate()

    import pytest
    with pytest.raises(ValueError, match="no path"):
        ModuleImpactTrace(module_id="decorative_research", evidence_ids=("E2",)).validate()

    ModuleImpactTrace(module_id="market_leakage_gate", guardrail_only=True).validate()


def test_repeated_costly_zero_impact_becomes_retire_candidate():
    low = compare_module_counterfactual(
        "scanner_x",
        baseline=DecisionOutcome("COMPLETED", 100.0),
        counterfactual=DecisionOutcome("COMPLETED", 100.0),
    )
    history = tuple(
        ModuleHistoryEntry(low, ResearchEffort(documents_reviewed=4), applicable=True, research_performed=True)
        for _ in range(6)
    )
    assert recommend_research_intensity(history) is ResearchIntensity.RETIRE_CANDIDATE


def test_mandatory_guardrail_is_never_retired_for_low_numeric_impact():
    low = compare_module_counterfactual(
        "audit",
        baseline=DecisionOutcome("COMPLETED", 100.0),
        counterfactual=DecisionOutcome("COMPLETED", 100.0),
    )
    history = (
        ModuleHistoryEntry(
            low,
            ResearchEffort(documents_reviewed=0),
            applicable=True,
            research_performed=False,
            mandatory_guardrail=True,
        ),
    )
    assert recommend_research_intensity(history) is ResearchIntensity.KEEP_GUARDRAIL


def test_non_applicable_research_is_direct_waste():
    low = compare_module_counterfactual(
        "clinical",
        baseline=DecisionOutcome("COMPLETED", 100.0),
        counterfactual=DecisionOutcome("COMPLETED", 100.0),
    )
    history = (
        ModuleHistoryEntry(low, ResearchEffort(documents_reviewed=3), applicable=False, research_performed=True),
        ModuleHistoryEntry(low, ResearchEffort(), applicable=False, research_performed=False),
    )
    assert len(wasted_research_entries(history)) == 1


def test_timing_impact_is_material():
    policy = ImpactPolicy(timing_materiality_days=14)
    baseline = DecisionOutcome("COMPLETED", 100.0, timing_days=90)
    counter = DecisionOutcome("COMPLETED", 100.0, timing_days=120)
    result = compare_module_counterfactual("permit", baseline=baseline, counterfactual=counter, policy=policy)
    assert result.classification is ImpactClassification.TIMING_MATERIAL


def test_three_point_numeric_sensitivity_tracks_value_spread_and_direction():
    from valuation_engine.decision_impact import assess_three_point_value_sensitivity

    result = assess_three_point_value_sensitivity(
        "capacity",
        variable="utilization",
        low_input=0.70,
        base_input=0.80,
        high_input=0.90,
        low_value=90.0,
        base_value=100.0,
        high_value=112.0,
        expected_direction="up",
    )
    assert result.monotonic
    assert round(result.downside_value_pct, 4) == -0.1
    assert round(result.upside_value_pct, 4) == 0.12
