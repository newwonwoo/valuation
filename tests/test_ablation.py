from valuation_engine.ablation import (
    AblationStatus,
    LoadoutAction,
    ModuleAblationSpec,
    retirement_proposals_allowed,
    run_joint_ablations,
    run_module_ablations,
)
from valuation_engine.decision_impact import (
    DecisionOutcome,
    ModuleHistoryEntry,
    ResearchEffort,
    compare_module_counterfactual,
)


def outcome(value: float, *, status: str = "complete", assumptions: str = "a") -> DecisionOutcome:
    return DecisionOutcome(
        status=status,
        intrinsic_value_per_share=value,
        assumption_hash=assumptions,
        route_hash="r",
        selected_methods=("dcf",),
        conclusion_tags=("base",),
        timing_days=100,
    )


def low_history(module_id: str, n: int = 6) -> tuple[ModuleHistoryEntry, ...]:
    baseline = outcome(100.0)
    assessment = compare_module_counterfactual(
        module_id,
        baseline=baseline,
        counterfactual=outcome(100.0),
    )
    return tuple(
        ModuleHistoryEntry(
            assessment=assessment,
            effort=ResearchEffort(documents_reviewed=4),
        )
        for _ in range(n)
    )


def test_value_material_ablation_is_measured():
    baseline = outcome(120.0)
    specs = (
        ModuleAblationSpec(
            "CAPACITY_SCANNER",
            expected_impact_paths=("capacity->quantity->revenue->value",),
        ),
    )

    result = run_module_ablations(
        baseline=baseline,
        specs=specs,
        run_without_module=lambda _: outcome(100.0),
    )

    observed = result.module_observations[0]
    assert observed.status is AblationStatus.MEASURED
    assert observed.assessment is not None
    assert observed.assessment.material is True
    assert result.measured_modules == ("CAPACITY_SCANNER",)


def test_non_applicable_module_never_runs_counterfactual():
    calls = []
    specs = (ModuleAblationSpec("CLINICAL", applicable=False),)

    result = run_module_ablations(
        baseline=outcome(100.0),
        specs=specs,
        run_without_module=lambda module_id: calls.append(module_id) or outcome(100.0),
    )

    assert calls == []
    assert result.module_observations[0].status is AblationStatus.NOT_APPLICABLE


def test_guardrail_kept_even_with_zero_value_delta():
    specs = (
        ModuleAblationSpec(
            "PRICE_ISOLATION",
            mandatory_guardrail=True,
            expected_impact_paths=("invalid-state-prevention",),
        ),
    )
    result = run_module_ablations(
        baseline=outcome(100.0),
        specs=specs,
        run_without_module=lambda _: outcome(100.0),
        guardrail_probe=lambda _: True,
    )

    rec = result.loadout_recommendations[0]
    assert rec.action is LoadoutAction.KEEP_GUARDRAIL
    assert result.module_observations[0].assessment.guardrail_violation_detected is True


def test_repeated_high_cost_zero_impact_becomes_downrank_proposal():
    module_id = "PATENT_SCANNER"
    specs = (
        ModuleAblationSpec(
            module_id,
            research_effort=ResearchEffort(documents_reviewed=4),
            expected_impact_paths=("technology-risk",),
        ),
    )
    result = run_module_ablations(
        baseline=outcome(100.0),
        specs=specs,
        run_without_module=lambda _: outcome(100.0),
        prior_history={module_id: low_history(module_id)},
    )

    assert result.loadout_recommendations[0].action is LoadoutAction.PROPOSE_DOWNRANK
    assert retirement_proposals_allowed(result) == (module_id,)


def test_joint_materiality_prevents_naive_retirement_of_correlated_modules():
    specs = (
        ModuleAblationSpec(
            "PERMIT_SCANNER",
            research_effort=ResearchEffort(documents_reviewed=4),
            expected_impact_paths=("project-timing",),
            correlation_group="project_realization",
        ),
        ModuleAblationSpec(
            "GRID_SCANNER",
            research_effort=ResearchEffort(documents_reviewed=4),
            expected_impact_paths=("project-timing",),
            correlation_group="project_realization",
        ),
    )
    history = {spec.module_id: low_history(spec.module_id) for spec in specs}
    batch = run_module_ablations(
        baseline=outcome(100.0),
        specs=specs,
        run_without_module=lambda _: outcome(100.0),
        prior_history=history,
    )
    assert {rec.action for rec in batch.loadout_recommendations} == {LoadoutAction.PROPOSE_DOWNRANK}

    batch = run_joint_ablations(
        batch,
        specs=specs,
        run_without_modules=lambda _: outcome(80.0),
    )

    assert batch.joint_observations[0].assessment.material is True
    assert retirement_proposals_allowed(batch) == ()


def test_unsupported_counterfactual_is_explicitly_not_measurable():
    specs = (
        ModuleAblationSpec(
            "NEW_SCANNER",
            counterfactual_supported=False,
            expected_impact_paths=("unknown-path",),
        ),
    )
    result = run_module_ablations(
        baseline=outcome(100.0),
        specs=specs,
        run_without_module=lambda _: outcome(90.0),
    )
    assert result.module_observations[0].status is AblationStatus.NOT_MEASURABLE
    assert result.loadout_recommendations == ()
