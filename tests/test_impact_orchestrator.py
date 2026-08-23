from __future__ import annotations

import pytest

from valuation_engine.decision_impact import (
    DecisionOutcome,
    ImpactClassification,
    ModuleHistoryEntry,
    ModuleImpactTrace,
    ResearchEffort,
    ResearchIntensity,
)
from valuation_engine.impact_orchestrator import (
    AdaptiveLoadoutPlan,
    ExperimentArtifact,
    ExperimentKind,
    ExperimentRequest,
    LoadoutDisposition,
    ModuleExperimentSpec,
    ModuleHistory,
    build_adaptive_loadout,
    run_automatic_ablation,
    summarize_module_history,
)


def _trace(module_id: str) -> ModuleImpactTrace:
    return ModuleImpactTrace(
        module_id=module_id,
        affected_assumptions=(f"assumption:{module_id}",),
        economic_path_ids=(f"path:{module_id}",),
        final_output_refs=("intrinsic_value",),
    )


def _linear_runner(request: ExperimentRequest) -> ExperimentArtifact:
    active = set(request.active_modules)
    value = 100.0
    if "alpha" in active:
        value += 20.0
    if "beta" in active:
        value += 0.2
    traces = tuple(_trace(module_id) for module_id in request.active_modules)
    return ExperimentArtifact(
        DecisionOutcome(
            status="complete",
            intrinsic_value_per_share=value,
            assumption_hash="|".join(sorted(active)),
            selected_methods=("dcf",),
            conclusion_tags=("base",),
        ),
        traces,
    )


def test_automatic_ablation_separates_material_and_low_impact_modules():
    report = run_automatic_ablation(
        (
            ModuleExperimentSpec("alpha"),
            ModuleExperimentSpec("beta"),
        ),
        _linear_runner,
        measure_pair_interactions=False,
    )

    assert report.result_for("alpha").assessment.classification is ImpactClassification.VALUE_MATERIAL
    assert report.result_for("alpha").assessment.material
    assert report.result_for("beta").assessment.classification is ImpactClassification.ASSUMPTION_ONLY
    assert not report.result_for("beta").assessment.material
    assert report.missing_baseline_traces == ()


def test_guardrail_ablation_is_classified_as_guardrail_critical():
    def runner(request: ExperimentRequest) -> ExperimentArtifact:
        violations = ("audit_gate",) if "audit_gate" in request.removed_modules else ()
        return ExperimentArtifact(
            DecisionOutcome(status="complete", intrinsic_value_per_share=100.0),
            tuple(_trace(module_id) for module_id in request.active_modules),
            violations,
        )

    report = run_automatic_ablation(
        (ModuleExperimentSpec("audit_gate", mandatory_guardrail=True),),
        runner,
        measure_pair_interactions=False,
    )
    row = report.result_for("audit_gate")
    assert row.assessment.classification is ImpactClassification.GUARDRAIL_CRITICAL
    assert row.assessment.material


def test_pair_ablation_measures_interaction_residual():
    def runner(request: ExperimentRequest) -> ExperimentArtifact:
        active = set(request.active_modules)
        value = 100.0
        if "a" in active:
            value += 10.0
        if "b" in active:
            value += 10.0
        if {"a", "b"}.issubset(active):
            value += 20.0
        return ExperimentArtifact(
            DecisionOutcome(status="complete", intrinsic_value_per_share=value),
            tuple(_trace(module_id) for module_id in request.active_modules),
        )

    report = run_automatic_ablation(
        (
            ModuleExperimentSpec("a", interaction_group="demand"),
            ModuleExperimentSpec("b", interaction_group="demand"),
        ),
        runner,
    )
    assert len(report.pair_interactions) == 1
    pair = report.pair_interactions[0]
    assert pair.joint_delta == pytest.approx(40.0)
    assert pair.individual_delta_a == pytest.approx(30.0)
    assert pair.individual_delta_b == pytest.approx(30.0)
    assert pair.interaction_residual == pytest.approx(-20.0)
    assert pair.material


def test_non_applicable_module_is_skipped_without_research_run():
    calls: list[ExperimentRequest] = []

    def runner(request: ExperimentRequest) -> ExperimentArtifact:
        calls.append(request)
        return ExperimentArtifact(
            DecisionOutcome(status="complete", intrinsic_value_per_share=100.0),
            tuple(_trace(module_id) for module_id in request.active_modules),
        )

    report = run_automatic_ablation(
        (
            ModuleExperimentSpec("active"),
            ModuleExperimentSpec("not_for_this_company", applicable=False, research_performed=False),
        ),
        runner,
        measure_pair_interactions=False,
    )
    assert report.skipped_not_applicable == ("not_for_this_company",)
    assert all("not_for_this_company" not in request.removed_modules for request in calls)


def test_missing_baseline_trace_is_reported_for_maintenance_audit():
    def runner(request: ExperimentRequest) -> ExperimentArtifact:
        return ExperimentArtifact(
            DecisionOutcome(status="complete", intrinsic_value_per_share=100.0),
            (),
        )

    report = run_automatic_ablation(
        (ModuleExperimentSpec("orphan"),),
        runner,
        measure_pair_interactions=False,
    )
    assert report.missing_baseline_traces == ("orphan",)


def test_high_effort_repeated_zero_impact_becomes_retire_review_not_auto_delete():
    report = run_automatic_ablation(
        (ModuleExperimentSpec("beta"),),
        _linear_runner,
        measure_pair_interactions=False,
    )
    assessment = report.result_for("beta").assessment
    entries = tuple(
        ModuleHistoryEntry(
            assessment=assessment,
            effort=ResearchEffort(documents_reviewed=3, elapsed_seconds=60),
        )
        for _ in range(6)
    )
    history = ModuleHistory("beta", entries)
    plan = build_adaptive_loadout(
        (ModuleExperimentSpec("beta"),),
        (history,),
    )
    row = plan.recommendations[0]
    assert row.intensity is ResearchIntensity.RETIRE_CANDIDATE
    assert row.disposition is LoadoutDisposition.RETIRE_REVIEW
    assert not row.deploy_by_default
    assert row.requires_user_approval_to_retire


def test_mandatory_guardrail_is_always_kept_even_without_value_delta():
    plan = build_adaptive_loadout(
        (ModuleExperimentSpec("audit_gate", mandatory_guardrail=True),),
        (),
    )
    row = plan.recommendations[0]
    assert row.intensity is ResearchIntensity.KEEP_GUARDRAIL
    assert row.disposition is LoadoutDisposition.KEEP_GUARDRAIL
    assert row.deploy_by_default


def test_sample_schedule_and_non_applicable_state_are_explicit():
    plan: AdaptiveLoadoutPlan = build_adaptive_loadout(
        (
            ModuleExperimentSpec("sample_later", sample_due=False),
            ModuleExperimentSpec("not_applicable", applicable=False, research_performed=False),
        ),
        (),
    )
    sample, skipped = plan.recommendations
    assert sample.disposition is LoadoutDisposition.SAMPLE
    assert not sample.deploy_by_default
    assert skipped.disposition is LoadoutDisposition.SKIP_NOT_APPLICABLE
    assert not skipped.deploy_by_default


def test_efficiency_summary_preserves_cost_and_material_rate():
    report = run_automatic_ablation(
        (ModuleExperimentSpec("alpha"),),
        _linear_runner,
        measure_pair_interactions=False,
    )
    assessment = report.result_for("alpha").assessment
    history = ModuleHistory(
        "alpha",
        (
            ModuleHistoryEntry(
                assessment,
                ResearchEffort(source_queries=2, documents_reviewed=4, llm_calls=1, elapsed_seconds=30),
            ),
            ModuleHistoryEntry(
                assessment,
                ResearchEffort(source_queries=1, documents_reviewed=2, llm_calls=2, elapsed_seconds=10),
            ),
        ),
    )
    summary = summarize_module_history(history)
    assert summary.observations == 2
    assert summary.material_rate == 1.0
    assert summary.mean_documents_reviewed == 3.0
    assert summary.mean_elapsed_seconds == 20.0
    assert summary.total_source_queries == 3
    assert summary.total_llm_calls == 3
    assert summary.recommended_intensity is ResearchIntensity.ALWAYS


def test_duplicate_module_specs_fail_closed():
    with pytest.raises(ValueError, match="unique module_id"):
        run_automatic_ablation(
            (ModuleExperimentSpec("x"), ModuleExperimentSpec("x")),
            _linear_runner,
        )


def test_experiment_request_prevents_invalid_ablation_shape():
    with pytest.raises(ValueError, match="exactly one"):
        ExperimentRequest(ExperimentKind.SINGLE_ABLATION, (), ("a", "b"))
