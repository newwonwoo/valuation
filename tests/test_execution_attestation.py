from pathlib import Path

import pytest

from valuation_engine.capacity_commitment import CapacityCommitmentAssessment
from valuation_engine.control_plane import (
    ExecutionMode,
    IntrinsicFreezeToken,
    StageStatus,
)
from valuation_engine.execution_attestation import build_execution_attestation
from valuation_engine.orchestrator import (
    OrchestratorContext,
    StageTrace,
    load_stage_sequence,
)
from valuation_engine.records import AuditReport


ROOT = Path(__file__).resolve().parents[1]
STAGES = ROOT / "config" / "control_plane_stage_registry.yaml"


def context(*, selected_methods=("commodity_price_taker/normalized_multiple/1",)):
    sequence = load_stage_sequence(STAGES)
    expected = sequence[: sequence.index("SAVE_STATE")]
    token = IntrinsicFreezeToken(
        run_id="RUN-1",
        ledger_snapshot_hash="LEDGER",
        assumption_set_hash="ASSUMPTIONS",
        valuation_hash="VALUATION",
        audit_hash="AUDIT",
        industry_snapshot_hash="INDUSTRY",
        source_snapshot_hash="SOURCE",
        token_hash="FREEZE",
    )
    data = {
        "generic_audit_report": AuditReport(()),
        "audit_passed": True,
        "capacity_audit_report": AuditReport(()),
        "capacity_commitment_assessment": CapacityCommitmentAssessment(
            (),
            "CAPACITY-ASSESSMENT",
        ),
        "intrinsic_freeze_token": token,
        "ledger_snapshot_hash": "LEDGER",
        "assumption_set_hash": "ASSUMPTIONS",
        "scenario_set_hash": "SCENARIOS",
        "valuation_hash": "VALUATION",
        "audit_hash": "AUDIT",
        "capacity_audit_hash": "CAPACITY-AUDIT",
        "selected_methods": selected_methods,
    }
    return OrchestratorContext(
        "RUN-1",
        ExecutionMode.LIVE_PRIMARY,
        data,
        [
            StageTrace(stage, StageStatus.PASS, "verified", False)
            for stage in expected
        ],
        token,
    )


def test_execution_attestation_requires_exact_canonical_stage_prefix():
    value = build_execution_attestation(
        context(),
        stage_registry_path=STAGES,
    )

    assert value.execution_mode == "live_primary"
    assert value.expected_stage_prefix == value.observed_stage_prefix
    assert value.capacity_assessment_hash == "CAPACITY-ASSESSMENT"
    assert value.attestation_hash


def test_execution_attestation_rejects_manual_or_incomplete_trace():
    broken = context()
    broken.stage_traces.pop()

    with pytest.raises(ValueError, match="stage prefix"):
        build_execution_attestation(
            broken,
            stage_registry_path=STAGES,
        )


def test_execution_attestation_requires_beta_and_wacc_for_dcf_methods():
    with pytest.raises(ValueError, match="Beta stage"):
        build_execution_attestation(
            context(selected_methods=("capacity_manufacturing/driver_dcf/1",)),
            stage_registry_path=STAGES,
        )
