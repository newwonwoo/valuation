from decimal import Decimal
from pathlib import Path

import pytest

from valuation_engine.ablation import ModuleAblationSpec, run_module_ablations
from valuation_engine.control_plane import (
    DoctrineCoverageEntry,
    ExecutionMode,
    StageStatus,
    issue_freeze_token,
)
from valuation_engine.decision_impact import DecisionOutcome, ResearchEffort
from valuation_engine.generic_reporting import render_generic_report, save_state_adapter
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.records import AuditFinding, AuditReport, RunManifest, RunStatus
from valuation_engine.research_learning import ResearchLearningStore
from valuation_engine.sotp import ScenarioEquityAggregation
from valuation_engine.state import StateStore
from valuation_engine.valuation_execution import GenericValuationResult, ScenarioPerShareValue


def _coverage():
    return (DoctrineCoverageEntry("STATE_LEARNING", StageStatus.PASS, "ready"),)


def _freeze_token(run_id: str):
    coverage = _coverage()
    return issue_freeze_token(
        run_id=run_id,
        audit_passed=True,
        coverage_entries=coverage,
        expected_module_ids=("STATE_LEARNING",),
        assumption_set_hash="ASSUMPTIONS",
        valuation_hash="VALUATION",
        audit_hash="AUDIT",
        industry_snapshot_hash="INDUSTRY",
        source_snapshot_hash="SOURCE",
    )


def _valuation():
    return GenericValuationResult(
        scenarios=(
            ScenarioPerShareValue(
                scenario_id="Base",
                equity_value_amount=Decimal("700"),
                reporting_unit="KRW",
                diluted_shares=Decimal("10"),
                value_per_share=Decimal("70"),
                aggregation_hash="AGG:BASE",
                economic_path_ids=("PATH:BASE",),
            ),
        ),
        equity_aggregation=ScenarioEquityAggregation((), None, False),
        expected_value_per_share=None,
        reporting_unit="KRW",
        valuation_hash="VALUATION",
    )


def _audit():
    return AuditReport((AuditFinding("fixture", True, True, "passed"),))


def _impact_batch():
    baseline = DecisionOutcome(status="COMPLETED", intrinsic_value_per_share=100.0)
    return run_module_ablations(
        baseline=baseline,
        specs=(
            ModuleAblationSpec(
                "MEASURED_MODULE",
                research_effort=ResearchEffort(
                    source_queries=2,
                    documents_reviewed=3,
                    llm_calls=1,
                    elapsed_seconds=4.5,
                ),
                expected_impact_paths=("evidence->assumption->intrinsic",),
            ),
            ModuleAblationSpec(
                "UNMEASURED_MODULE",
                counterfactual_supported=False,
                research_effort=ResearchEffort(documents_reviewed=1),
                expected_impact_paths=("signal->verification",),
            ),
        ),
        run_without_module=lambda module_id: (
            DecisionOutcome(status="COMPLETED", intrinsic_value_per_share=80.0)
            if module_id == "MEASURED_MODULE"
            else baseline
        ),
    )


def _context(tmp_path: Path, *, run_id: str, learning_path: str):
    token = _freeze_token(run_id)
    return OrchestratorContext(
        run_id=run_id,
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        data={
            "company": "Example",
            "ticker": "TEST",
            "company_state": {},
            "current_thesis": "Measured operating evidence supports the base thesis.",
            "generic_valuation_result": _valuation(),
            "generic_audit_report": _audit(),
            "doctrine_coverage": _coverage(),
            "intrinsic_freeze_token": token,
            "decision_impact_batch": _impact_batch(),
            "decision_impact_hash": "IMPACT",
            "research_learning_record_path": learning_path,
            "research_learning_record_hash": "LEARNING",
            "assumption_set_hash": "ASSUMPTIONS",
            "valuation_hash": "VALUATION",
            "audit_hash": "AUDIT",
        },
        freeze_token=token,
    )


def test_state_promotion_failure_rolls_back_run_and_same_run_learning_record(tmp_path, monkeypatch):
    batch = _impact_batch()
    learning = ResearchLearningStore(tmp_path).save_batch(
        ticker="TEST",
        run_id="R1",
        batch=batch,
        recorded_at="2026-08-23T00:00:00+00:00",
    )
    context = _context(tmp_path, run_id="R1", learning_path=learning.path)

    def fail_promotion(self, manifest, current_state):
        raise RuntimeError("simulated promotion failure")

    monkeypatch.setattr(StateStore, "promote_current", fail_promotion)
    result = save_state_adapter(state_root=tmp_path)(context)

    assert result.status is StageStatus.BLOCKED
    assert "simulated promotion failure" in result.rationale
    assert not Path(learning.path).exists()
    assert not (tmp_path / "runs" / "TEST" / "R1").exists()
    assert not (tmp_path / "state" / "TEST" / "current_state.json").exists()


def test_duplicate_run_id_preserves_prior_successful_state_and_artifacts(tmp_path):
    learning = ResearchLearningStore(tmp_path).save_batch(
        ticker="TEST",
        run_id="R1",
        batch=_impact_batch(),
        recorded_at="2026-08-23T00:00:00+00:00",
    )
    adapter = save_state_adapter(state_root=tmp_path)
    first = adapter(_context(tmp_path, run_id="R1", learning_path=learning.path))
    assert first.status is StageStatus.PASS

    run_dir = tmp_path / "runs" / "TEST" / "R1"
    current_path = tmp_path / "state" / "TEST" / "current_state.json"
    prior_manifest = (run_dir / "manifest.json").read_bytes()
    prior_current = current_path.read_bytes()
    prior_learning = Path(learning.path).read_bytes()

    duplicate_context = _context(tmp_path, run_id="R1", learning_path="")
    duplicate_context.data["company_state"] = StateStore(tmp_path).load_current("TEST")
    duplicate = adapter(duplicate_context)

    assert duplicate.status is StageStatus.BLOCKED
    assert "FileExistsError" in duplicate.rationale
    assert "already exists" in duplicate.rationale
    assert (run_dir / "manifest.json").read_bytes() == prior_manifest
    assert current_path.read_bytes() == prior_current
    assert Path(learning.path).read_bytes() == prior_learning
    assert StateStore(tmp_path).load_current("TEST")["last_completed_run"] == "R1"


def test_save_run_removes_partial_directory_created_by_failed_write(tmp_path):
    manifest = RunManifest(
        run_id="PARTIAL",
        ticker="TEST",
        company="Example",
        started_at="2026-08-23T00:00:00+00:00",
        finished_at="2026-08-23T00:01:00+00:00",
        status=RunStatus.COMPLETED,
        round_count=1,
        audit_passed=True,
        parent_run_id=None,
        blocked_reasons=(),
    )

    with pytest.raises(TypeError, match="not JSON serializable"):
        StateStore(tmp_path).save_run(manifest, {"invalid.json": object()})

    assert not (tmp_path / "runs" / "TEST" / "PARTIAL").exists()


def test_final_report_distinguishes_measured_from_not_measurable_and_shows_cost():
    data = {
        "company": "Example",
        "generic_valuation_result": _valuation(),
        "generic_audit_report": _audit(),
        "doctrine_coverage": _coverage(),
        "decision_impact_batch": _impact_batch(),
        "assumption_set_hash": "ASSUMPTIONS",
        "valuation_hash": "VALUATION",
        "audit_hash": "AUDIT",
        "intrinsic_freeze_token": _freeze_token("REPORT"),
    }

    report = render_generic_report(data)

    assert "## Module Impact / Research Efficiency" in report
    assert "측정 완료: MEASURED_MODULE" in report
    assert "미측정(NOT_MEASURABLE): UNMEASURED_MODULE" in report
    assert "source queries 2, documents 4, LLM calls 1, elapsed 4.5s" in report
    assert "미측정 모듈은 0 영향이 아니라 NOT_MEASURABLE" in report
