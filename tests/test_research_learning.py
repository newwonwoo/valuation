import pytest

from valuation_engine.ablation import (
    AblationStatus,
    ModuleAblationSpec,
    run_module_ablations,
)
from valuation_engine.control_plane import (
    DoctrineCoverageEntry,
    ExecutionMode,
    StageStatus,
    issue_freeze_token,
)
from valuation_engine.decision_impact import DecisionOutcome, ResearchEffort
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.research_learning import ResearchLearningStore
from valuation_engine.state_learning_adapter import (
    load_research_learning_adapter,
    save_research_learning_adapter,
)


def measured_batch(value: float = 100.0):
    baseline = DecisionOutcome(status="COMPLETED", intrinsic_value_per_share=value)
    return run_module_ablations(
        baseline=baseline,
        specs=(
            ModuleAblationSpec(
                "CAPACITY_RAMP",
                research_effort=ResearchEffort(documents_reviewed=3, llm_calls=1),
                expected_impact_paths=("capacity->revenue->intrinsic",),
            ),
        ),
        run_without_module=lambda _: DecisionOutcome(
            status="COMPLETED",
            intrinsic_value_per_share=value * 0.8,
        ),
    )


def not_measurable_batch():
    baseline = DecisionOutcome(status="COMPLETED", intrinsic_value_per_share=100.0)
    return run_module_ablations(
        baseline=baseline,
        specs=(
            ModuleAblationSpec(
                "PATENT_SIGNAL",
                counterfactual_supported=False,
                expected_impact_paths=("patent->technology_risk",),
            ),
        ),
        run_without_module=lambda _: baseline,
    )


def freeze_token(run_id: str):
    coverage = (DoctrineCoverageEntry("STATE_LEARNING", StageStatus.PASS, "ready"),)
    return issue_freeze_token(
        run_id=run_id,
        audit_passed=True,
        coverage_entries=coverage,
        expected_module_ids=("STATE_LEARNING",),
        assumption_set_hash="assumptions",
        valuation_hash="valuation",
        audit_hash="audit",
        industry_snapshot_hash="industry",
        source_snapshot_hash="source",
    )


def test_learning_store_is_immutable_and_reconstructs_measured_prior_history(tmp_path):
    store = ResearchLearningStore(tmp_path)
    store.save_batch(
        ticker="TEST",
        run_id="R1",
        batch=measured_batch(100.0),
        recorded_at="2026-01-01T00:00:00+00:00",
    )
    store.save_batch(
        ticker="TEST",
        run_id="R2",
        batch=measured_batch(120.0),
        recorded_at="2026-02-01T00:00:00+00:00",
    )

    history = store.load_prior_history("TEST")
    assert len(history["CAPACITY_RAMP"]) == 2
    assert all(item.assessment.material for item in history["CAPACITY_RAMP"])
    assert store.record_count("TEST") == 2
    latest = store.load_latest_recommendations("TEST")
    assert latest and latest[0].module_id == "CAPACITY_RAMP"

    with pytest.raises(FileExistsError):
        store.save_batch(ticker="TEST", run_id="R2", batch=measured_batch())


def test_not_measurable_state_is_retained_raw_but_never_treated_as_zero_impact_history(tmp_path):
    store = ResearchLearningStore(tmp_path)
    batch = not_measurable_batch()
    assert batch.module_observations[0].status is AblationStatus.NOT_MEASURABLE
    store.save_batch(ticker="TEST", run_id="R1", batch=batch)
    assert store.record_count("TEST") == 1
    assert store.load_prior_history("TEST") == {}


def test_load_and_save_adapters_form_post_freeze_learning_loop(tmp_path):
    store = ResearchLearningStore(tmp_path)
    batch = measured_batch()
    save_context = OrchestratorContext(
        run_id="R1",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        data={"ticker": "TEST", "decision_impact_batch": batch},
        freeze_token=freeze_token("R1"),
    )
    saved = save_research_learning_adapter(store=store)(save_context)
    assert saved.outputs["research_learning_record_hash"]

    load_context = OrchestratorContext(
        run_id="R2",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        data={"ticker": "TEST"},
    )
    loaded = load_research_learning_adapter(store=store)(load_context)
    assert loaded.outputs["research_learning_record_count"] == 1
    assert len(loaded.outputs["module_impact_prior_history"]["CAPACITY_RAMP"]) == 1
    assert loaded.outputs["prior_research_loadout_recommendations"]


def test_save_adapter_rejects_invalid_or_mismatched_freeze_token(tmp_path):
    store = ResearchLearningStore(tmp_path)
    context = OrchestratorContext(
        run_id="R2",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        data={"ticker": "TEST", "decision_impact_batch": measured_batch()},
        freeze_token=freeze_token("R1"),
    )
    result = save_research_learning_adapter(store=store)(context)
    assert result.status is StageStatus.BLOCKED
    assert "run mismatch" in result.rationale


def test_learning_store_rejects_unsafe_path_components(tmp_path):
    store = ResearchLearningStore(tmp_path)
    with pytest.raises(ValueError):
        store.save_batch(ticker="../TEST", run_id="R1", batch=measured_batch())
