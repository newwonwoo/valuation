from dataclasses import replace

import pytest

from valuation_engine.control_plane import (
    BuildProposal,
    CapabilityGap,
    DoctrineCoverageEntry,
    LLMAction,
    RecoveryStep,
    StageStatus,
    authorize_post_freeze,
    build_proposal_allowed,
    issue_freeze_token,
    next_recovery_step,
    validate_doctrine_coverage,
    validate_llm_authority,
)


def test_llm_may_reason_but_may_not_commit_or_authorize():
    validate_llm_authority(LLMAction.REASON)
    validate_llm_authority(LLMAction.RECOVER)
    with pytest.raises(PermissionError):
        validate_llm_authority(LLMAction.PROPOSE, commits_assumption=True)
    with pytest.raises(PermissionError):
        validate_llm_authority(LLMAction.DESIGN, mutates_canonical_system=True)
    with pytest.raises(PermissionError):
        validate_llm_authority(LLMAction.ASK, authorizes_stage=True)


def test_none_recovery_uses_full_ladder_before_blocking():
    attempted = ()
    assert next_recovery_step(attempted) is RecoveryStep.RESEARCH
    attempted = (
        RecoveryStep.RESEARCH,
        RecoveryStep.RECONCILE,
        RecoveryStep.DERIVE,
        RecoveryStep.PROXY,
        RecoveryStep.ALTERNATE_MODEL,
        RecoveryStep.BOUNDED_ESTIMATE,
        RecoveryStep.PARTIAL_VALUATION,
    )
    assert next_recovery_step(attempted) is RecoveryStep.CAPABILITY_DESIGN
    attempted += (RecoveryStep.CAPABILITY_DESIGN,)
    assert next_recovery_step(attempted) is RecoveryStep.VALUATION_BLOCKED


def test_capability_build_is_proposed_only_after_overbuild_gate_passes():
    gap = CapabilityGap(
        "G1", "hybrid_security_evaluator", "existing evaluator cannot model reset terms",
        True, True, True, True,
    )
    assert build_proposal_allowed(gap)
    BuildProposal(
        gap_id="G1",
        title="Hybrid security evaluator",
        inputs=("contract_terms",),
        outputs=("diluted_share_paths",),
        affected_components=("compiler", "ev_to_equity"),
        validation_plan=("unit tests", "regression", "red team"),
    )
    one_off = CapabilityGap("G2", "one_off", "immaterial", True, False, False, True)
    assert not build_proposal_allowed(one_off)


def test_doctrine_coverage_forbids_silent_skip():
    entries = (
        DoctrineCoverageEntry("industry_dna", StageStatus.PASS, "routed"),
        DoctrineCoverageEntry("funding", StageStatus.SKIPPED_NOT_APPLICABLE, "no external funding dependency"),
    )
    validate_doctrine_coverage(entries, expected_module_ids=("industry_dna", "funding"))
    with pytest.raises(ValueError, match="silent skip"):
        validate_doctrine_coverage(entries, expected_module_ids=("industry_dna", "funding", "wacc"))


def test_freeze_requires_audit_and_resolved_blocking_coverage():
    entries = (
        DoctrineCoverageEntry("industry_dna", StageStatus.PASS, "routed", True),
        DoctrineCoverageEntry("clinical", StageStatus.SKIPPED_NOT_APPLICABLE, "not healthcare"),
    )
    with pytest.raises(ValueError, match="audit PASS"):
        issue_freeze_token(
            run_id="R1", audit_passed=False, coverage_entries=entries,
            expected_module_ids=("industry_dna", "clinical"),
            ledger_snapshot_hash="l", assumption_set_hash="a", valuation_hash="v", audit_hash="q",
            industry_snapshot_hash="i", source_snapshot_hash="s",
        )

    token = issue_freeze_token(
        run_id="R1", audit_passed=True, coverage_entries=entries,
        expected_module_ids=("industry_dna", "clinical"),
        ledger_snapshot_hash="l", assumption_set_hash="a", valuation_hash="v", audit_hash="q",
        industry_snapshot_hash="i", source_snapshot_hash="s",
    )
    assert token.ledger_snapshot_hash == "l"
    authorize_post_freeze(token, run_id="R1")
    with pytest.raises(PermissionError):
        authorize_post_freeze(token, run_id="R2")
    with pytest.raises(PermissionError, match="invalid intrinsic freeze token"):
        authorize_post_freeze(replace(token, ledger_snapshot_hash="tampered"), run_id="R1")

    calibrated = issue_freeze_token(
        run_id="R1", audit_passed=True, coverage_entries=entries,
        expected_module_ids=("industry_dna", "clinical"),
        ledger_snapshot_hash="l", assumption_set_hash="a", valuation_hash="v", audit_hash="q",
        industry_snapshot_hash="i", source_snapshot_hash="s",
        calibration_dataset_hash="dataset", calibration_snapshot_hash="snapshot",
    )
    authorize_post_freeze(calibrated, run_id="R1")
    assert calibrated.calibration_dataset_hash == "dataset"
    assert calibrated.calibration_snapshot_hash == "snapshot"
    with pytest.raises(PermissionError, match="invalid intrinsic freeze token"):
        authorize_post_freeze(
            replace(calibrated, calibration_dataset_hash="tampered"), run_id="R1"
        )


def test_blocking_not_implemented_prevents_freeze():
    entries = (
        DoctrineCoverageEntry("required_evaluator", StageStatus.NOT_IMPLEMENTED, "missing", True),
    )
    with pytest.raises(ValueError, match="unresolved blocking"):
        issue_freeze_token(
            run_id="R1", audit_passed=True, coverage_entries=entries,
            expected_module_ids=("required_evaluator",),
            ledger_snapshot_hash="l", assumption_set_hash="a", valuation_hash="v", audit_hash="q",
            industry_snapshot_hash="i", source_snapshot_hash="s",
        )
