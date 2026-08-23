from __future__ import annotations

from datetime import datetime, timezone

import pytest

from valuation_engine.control_plane_impact import (
    DeploymentStatus,
    build_control_plane_impact_loadout,
)
from valuation_engine.decision_impact import DecisionOutcome, ModuleImpactTrace, ResearchEffort
from valuation_engine.impact_history import ModuleImpactHistoryLedger
from valuation_engine.impact_orchestrator import (
    ExperimentArtifact,
    ExperimentRequest,
    ModuleExperimentSpec,
    run_automatic_ablation,
)


def _trace(module_id: str) -> ModuleImpactTrace:
    return ModuleImpactTrace(module_id, affected_assumptions=(f"assumption:{module_id}",))


def _runner(request: ExperimentRequest) -> ExperimentArtifact:
    active = set(request.active_modules)
    value = 100.0 + (20.0 if "alpha" in active else 0.0) + (0.2 if "beta" in active else 0.0)
    violations = ("audit_gate",) if "audit_gate" in request.removed_modules else ()
    return ExperimentArtifact(
        DecisionOutcome(
            status="complete",
            intrinsic_value_per_share=value,
            assumption_hash="|".join(sorted(active)),
        ),
        tuple(_trace(module_id) for module_id in request.active_modules),
        violations,
    )


def test_history_ledger_appends_report_and_groups_by_module():
    specs = (
        ModuleExperimentSpec("alpha", effort=ResearchEffort(documents_reviewed=2)),
        ModuleExperimentSpec("beta", effort=ResearchEffort(documents_reviewed=3)),
    )
    report = run_automatic_ablation(specs, _runner, measure_pair_interactions=False)
    ledger = ModuleImpactHistoryLedger()
    records = ledger.append_ablation_report(
        run_id="RUN-1",
        report=report,
        specs=specs,
        observed_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )

    assert len(records) == 2
    assert len(ledger.for_module("alpha")) == 1
    assert len(ledger.module_histories()) == 2
    assert ledger.to_list()[0]["observed_at"].startswith("2026-08-22")


def test_history_ledger_is_append_only_by_run_and_module():
    specs = (ModuleExperimentSpec("alpha"),)
    report = run_automatic_ablation(specs, _runner, measure_pair_interactions=False)
    ledger = ModuleImpactHistoryLedger()
    ledger.append_ablation_report(run_id="RUN-1", report=report, specs=specs)
    with pytest.raises(ValueError, match="duplicate impact history record"):
        ledger.append_ablation_report(run_id="RUN-1", report=report, specs=specs)


def test_repeated_low_impact_history_becomes_user_review_not_silent_removal():
    specs = (
        ModuleExperimentSpec(
            "beta",
            effort=ResearchEffort(documents_reviewed=3, elapsed_seconds=60),
        ),
        ModuleExperimentSpec("audit_gate", mandatory_guardrail=True),
    )
    report = run_automatic_ablation(specs, _runner, measure_pair_interactions=False)
    ledger = ModuleImpactHistoryLedger()
    for index in range(6):
        ledger.append_ablation_report(run_id=f"RUN-{index}", report=report, specs=specs)

    loadout = build_control_plane_impact_loadout(specs, ledger)
    rows = {row.module_id: row for row in loadout.orders}
    assert rows["beta"].status is DeploymentStatus.RETIRE_REVIEW
    assert rows["beta"].user_decision_required
    assert not rows["beta"].deploy
    assert rows["audit_gate"].status is DeploymentStatus.DEPLOYED
    assert rows["audit_gate"].deploy


def test_mission_required_module_overrides_sampling_deferral():
    specs = (ModuleExperimentSpec("special_scan", sample_due=False),)
    ledger = ModuleImpactHistoryLedger()

    normal = build_control_plane_impact_loadout(specs, ledger)
    assert normal.orders[0].status is DeploymentStatus.DEFERRED_SAMPLE
    assert not normal.orders[0].deploy

    required = build_control_plane_impact_loadout(
        specs,
        ledger,
        mission_required_modules=("special_scan",),
    )
    assert required.orders[0].status is DeploymentStatus.DEPLOYED
    assert required.orders[0].deploy
    assert "mission requirement" in required.orders[0].rationale


def test_non_applicable_and_unknown_mission_requirements_fail_closed():
    specs = (ModuleExperimentSpec("not_here", applicable=False, research_performed=False),)
    ledger = ModuleImpactHistoryLedger()

    loadout = build_control_plane_impact_loadout(specs, ledger)
    assert loadout.orders[0].status is DeploymentStatus.SKIPPED_NOT_APPLICABLE
    assert loadout.skipped_not_applicable == ("not_here",)

    with pytest.raises(ValueError, match="non-applicable"):
        build_control_plane_impact_loadout(
            specs,
            ledger,
            mission_required_modules=("not_here",),
        )
    with pytest.raises(ValueError, match="unknown mission-required"):
        build_control_plane_impact_loadout(
            specs,
            ledger,
            mission_required_modules=("missing",),
        )


def test_control_plane_exposes_user_review_and_active_module_lists():
    specs = (
        ModuleExperimentSpec("audit_gate", mandatory_guardrail=True),
        ModuleExperimentSpec("sample", sample_due=True),
    )
    loadout = build_control_plane_impact_loadout(specs, ModuleImpactHistoryLedger())
    assert set(loadout.active_modules) == {"audit_gate", "sample"}
    assert loadout.user_review_modules == ()
