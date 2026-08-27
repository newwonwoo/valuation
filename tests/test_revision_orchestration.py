from pathlib import Path

import pytest

from valuation_engine.revision_orchestration import (
    RevisionClaimTreatment,
    RevisionClause,
    RevisionScope,
    RevisionTask,
    RevisionTaskResult,
    RevisionTaskStatus,
    audit_revision_execution,
    build_parallel_waves,
    build_revision_plan,
    invalidate_descendants,
    required_unit_ids,
)
from valuation_engine.unit_contracts import load_unit_contract_registry


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = load_unit_contract_registry(ROOT / "config" / "unit_contract_registry.yaml")


def task(
    task_id: str,
    *,
    clauses: tuple[str, ...] = ("C1",),
    units: tuple[str, ...] = ("FINAL_REPORT",),
    writes: tuple[str, ...] = (),
    depends_on: tuple[str, ...] = (),
) -> RevisionTask:
    return RevisionTask(
        task_id=task_id,
        clause_ids=clauses,
        owner=f"owner:{task_id}",
        unit_ids=units,
        read_set=("input",),
        write_set=writes,
        depends_on=depends_on,
        output_ids=(f"output:{task_id}",),
        validators=(f"validate:{task_id}",),
    )


def report_clause() -> RevisionClause:
    return RevisionClause(
        clause_id="C1",
        desired_outcome="보고서 문구만 수정",
        scopes=(RevisionScope.REPORT_CONTENT,),
        root_unit_ids=("FINAL_REPORT",),
        acceptance_criteria=("새 문구가 보고서에 한 번만 표시",),
    )


def test_report_only_plan_selects_reporter_and_skips_model_units():
    plan = build_revision_plan(
        request_id="R1",
        base_revision="abc123",
        clauses=(report_clause(),),
        tasks=(task("report", writes=("report.html",)),),
        registry=REGISTRY,
    )

    assert plan.selected_unit_ids == ("FINAL_REPORT",)
    assert "ASSUMPTION_COMPILER" in plan.skipped_unit_ids
    assert "DETERMINISTIC_VALUATION" in plan.skipped_unit_ids


def test_report_only_plan_rejects_unrelated_unit_invocation():
    with pytest.raises(ValueError, match="invokes unrelated units"):
        build_revision_plan(
            request_id="R1-extra",
            base_revision="abc123",
            clauses=(report_clause(),),
            tasks=(
                task(
                    "report",
                    units=("FINAL_REPORT", "PRIMARY_EVIDENCE_COLLECTION"),
                ),
            ),
            registry=REGISTRY,
        )


def test_independent_non_overlapping_tasks_share_a_parallel_wave():
    waves = build_parallel_waves(
        (
            task("copy", writes=("report.md",)),
            task("layout", writes=("style.css",)),
        )
    )

    assert waves[0].task_ids == ("copy", "layout")


def test_unordered_overlapping_writes_fail_closed():
    with pytest.raises(ValueError, match="overlapping write sets"):
        build_parallel_waves(
            (
                task("one", writes=("report.html",)),
                task("two", writes=("report.html",)),
            )
        )


def test_dependencies_create_sequential_waves_and_cycles_are_rejected():
    waves = build_parallel_waves(
        (
            task("model", writes=("model.yaml",)),
            task("report", writes=("report.html",), depends_on=("model",)),
        )
    )
    assert tuple(wave.task_ids for wave in waves) == (("model",), ("report",))

    with pytest.raises(ValueError, match="contains a cycle"):
        build_parallel_waves(
            (
                task("one", depends_on=("two",)),
                task("two", depends_on=("one",)),
            )
        )


def test_valued_headline_requires_model_to_report_path():
    clause = RevisionClause(
        clause_id="C1",
        desired_outcome="정책 논지를 제목과 내재가치에 반영",
        scopes=(RevisionScope.REPORT_CONTENT,),
        root_unit_ids=("FINAL_REPORT",),
        acceptance_criteria=("전후 가치와 차이 표시",),
        material_report_claim=True,
        claim_treatment=RevisionClaimTreatment.VALUED,
    )
    selected = required_unit_ids((clause,))
    assert "EVIDENCE_TO_ASSUMPTION_BRIDGE" in selected
    assert "DETERMINISTIC_VALUATION" in selected
    assert "AUDIT_GATE" in selected
    assert "FINAL_REPORT" in selected
    with pytest.raises(ValueError, match="omits required unit path"):
        build_revision_plan(
            request_id="R2",
            base_revision="abc123",
            clauses=(clause,),
            tasks=(task("report"),),
            registry=REGISTRY,
        )


def test_failed_task_invalidates_only_itself_and_its_descendants():
    plan = build_revision_plan(
        request_id="R3",
        base_revision="abc123",
        clauses=(report_clause(),),
        tasks=(
            task("copy"),
            task("layout"),
            task("bundle", depends_on=("copy", "layout")),
        ),
        registry=REGISTRY,
    )

    assert invalidate_descendants(plan, ("copy",)) == ("bundle", "copy")


def test_execution_audit_rejects_stale_plan_and_unplanned_writes():
    plan = build_revision_plan(
        request_id="R4",
        base_revision="abc123",
        clauses=(report_clause(),),
        tasks=(task("report", writes=("report.html",)),),
        registry=REGISTRY,
    )
    result = RevisionTaskResult(
        plan_hash=plan.plan_hash,
        base_revision=plan.base_revision,
        task_id="report",
        status=RevisionTaskStatus.PASS,
        actual_write_set=("report.html", "model.yaml"),
        output_ids=("output:report",),
        completed_validators=("validate:report",),
    )

    audit = audit_revision_execution(
        plan,
        (result,),
        current_base_revision="new-base",
    )
    assert not audit.merge_ready
    assert audit.stale_result_task_ids == ("report",)
    assert audit.unplanned_write_paths == ("model.yaml",)
