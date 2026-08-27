from pathlib import Path

from valuation_engine.capacity_commitment import CapacityCommitmentAssessment
from valuation_engine.control_plane import (
    ExecutionMode,
    IntrinsicFreezeToken,
    StageStatus,
)
from valuation_engine.orchestrator import (
    ControlledRunResult,
    StageTrace,
    load_stage_sequence,
)
from valuation_engine.report_form import (
    attest_controlled_run,
    render_controlled_run_report,
    render_report_form_template,
)


ROOT = Path(__file__).resolve().parents[1]
STAGES = ROOT / "config" / "control_plane_stage_registry.yaml"


def completed_result() -> ControlledRunResult:
    sequence = load_stage_sequence(STAGES)
    token = IntrinsicFreezeToken(
        run_id="REPORT-RUN",
        ledger_snapshot_hash="LEDGER",
        assumption_set_hash="ASSUMPTIONS",
        valuation_hash="VALUATION",
        audit_hash="AUDIT",
        industry_snapshot_hash="INDUSTRY",
        source_snapshot_hash="SOURCE",
        token_hash="FREEZE",
    )
    data = {
        "ledger_snapshot_hash": "LEDGER",
        "assumption_set_hash": "ASSUMPTIONS",
        "scenario_set_hash": "SCENARIOS",
        "valuation_hash": "VALUATION",
        "audit_hash": "AUDIT",
        "audit_passed": True,
        "selected_methods": ("commodity_price_taker/normalized_multiple/1",),
        "capacity_commitment_assessment": CapacityCommitmentAssessment(
            (),
            "CAPACITY-ASSESSMENT",
        ),
        "capacity_commitment_assessment_hash": "CAPACITY-ASSESSMENT",
        "capacity_audit_hash": "CAPACITY-AUDIT",
        "capacity_audit_passed": True,
        "final_report": "# Persisted fixture report\n\n- intrinsic: 70,000 KRW/share\n",
    }
    return ControlledRunResult(
        run_id="REPORT-RUN",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_traces=tuple(
            StageTrace(stage, StageStatus.PASS, "verified", False)
            for stage in sequence
        ),
        data=data,
        blocked_reasons=(),
        freeze_token=token,
    )


def test_completed_controlled_run_is_verified_and_renders_trace():
    result = completed_result()
    attestation = attest_controlled_run(result, stage_registry_path=STAGES)
    report = render_controlled_run_report(result, stage_registry_path=STAGES)

    assert attestation.passed
    assert "Run status: **VERIFIED_FROZEN**" in report
    assert "## Stage Trace" in report
    assert "CAPACITY-AUDIT" in report
    assert "# Persisted fixture report" in report


def test_manual_or_partial_result_cannot_be_labelled_verified():
    result = completed_result()
    broken = ControlledRunResult(
        run_id=result.run_id,
        execution_mode=result.execution_mode,
        stage_traces=result.stage_traces[:-1],
        data=result.data,
        blocked_reasons=(),
        freeze_token=result.freeze_token,
    )
    attestation = attest_controlled_run(broken, stage_registry_path=STAGES)
    report = render_controlled_run_report(broken, stage_registry_path=STAGES)

    assert not attestation.passed
    assert "Run status: **INCOMPLETE**" in report
    assert "`canonical_stage_sequence` | **FAIL**" in report


def test_report_form_template_contains_required_execution_identities():
    template = render_report_form_template()

    assert "capacity_audit_hash" in template
    assert "beta_snapshot_hash" in template
    assert "wacc_snapshot_hash" in template
    assert "freeze_token_hash" in template
    assert "immutable_saved_final_report" in template


def test_report_template_exposes_broker_research_audit_identity():
    template = render_report_form_template()

    assert "broker_research_primary_verification_chain" in template
    assert "broker_research_snapshot_hash" in template
    assert "broker_research_audit_hash" in template
