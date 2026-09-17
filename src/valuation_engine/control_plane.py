from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib


class MissionMode(str, Enum):
    FULL_VALUATION = "full_valuation"
    DELTA_REVALIDATION = "delta_revalidation"
    MONITORING = "monitoring"
    COMPANY_BRIEF = "company_brief"
    CREDIT_RISK = "credit_risk"
    DISCLOSURE_MONITOR = "disclosure_monitor"


class ExecutionMode(str, Enum):
    LEGACY_REGRESSION = "legacy_regression"
    PRIMARY_SHADOW = "primary_shadow"
    LIVE_PRIMARY = "live_primary"


class LLMAction(str, Enum):
    OBSERVE = "observe"
    REASON = "reason"
    PROPOSE = "propose"
    RECOVER = "recover"
    DESIGN = "design"
    ASK = "ask"


class StageStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    PASS = "pass"
    WARNING = "warning"
    BLOCKED = "blocked"
    SKIPPED_NOT_APPLICABLE = "skipped_not_applicable"
    NOT_IMPLEMENTED = "not_implemented"
    RECOVERY_REQUIRED = "recovery_required"
    RECOVERED = "recovered"
    AWAITING_USER_DECISION = "awaiting_user_decision"


class GapType(str, Enum):
    EVIDENCE_GAP = "evidence_gap"
    REASONING_GAP = "reasoning_gap"
    METHOD_GAP = "method_gap"
    CAPABILITY_GAP = "capability_gap"


class RecoveryStep(str, Enum):
    RESEARCH = "research"
    RECONCILE = "reconcile"
    DERIVE = "derive"
    PROXY = "proxy"
    ALTERNATE_MODEL = "alternate_model"
    BOUNDED_ESTIMATE = "bounded_estimate"
    PARTIAL_VALUATION = "partial_valuation"
    CAPABILITY_DESIGN = "capability_design"
    VALUATION_BLOCKED = "valuation_blocked"


RECOVERY_LADDER = (
    RecoveryStep.RESEARCH,
    RecoveryStep.RECONCILE,
    RecoveryStep.DERIVE,
    RecoveryStep.PROXY,
    RecoveryStep.ALTERNATE_MODEL,
    RecoveryStep.BOUNDED_ESTIMATE,
    RecoveryStep.PARTIAL_VALUATION,
    RecoveryStep.CAPABILITY_DESIGN,
    RecoveryStep.VALUATION_BLOCKED,
)


@dataclass(frozen=True)
class DoctrineCoverageEntry:
    module_id: str
    status: StageStatus
    rationale: str
    blocking: bool = False

    def __post_init__(self) -> None:
        if not self.module_id or not self.rationale:
            raise ValueError("coverage entry requires module_id and rationale")

    @property
    def unresolved_blocker(self) -> bool:
        return self.blocking and self.status in {
            StageStatus.BLOCKED,
            StageStatus.NOT_IMPLEMENTED,
            StageStatus.RECOVERY_REQUIRED,
            StageStatus.AWAITING_USER_DECISION,
        }


@dataclass(frozen=True)
class CapabilityGap:
    gap_id: str
    capability_type: str
    reason: str
    existing_capability_exhausted: bool
    material_to_current_analysis: bool
    reusable_beyond_current_case: bool
    input_output_contract_defined: bool

    def __post_init__(self) -> None:
        if not self.gap_id or not self.capability_type or not self.reason:
            raise ValueError("capability gap requires identity, type and reason")


@dataclass(frozen=True)
class BuildProposal:
    gap_id: str
    title: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    affected_components: tuple[str, ...]
    validation_plan: tuple[str, ...]
    canonical_promotion_required: bool = True

    def __post_init__(self) -> None:
        if not self.gap_id or not self.title:
            raise ValueError("build proposal requires gap_id and title")
        if not self.inputs or not self.outputs or not self.validation_plan:
            raise ValueError("build proposal requires inputs, outputs and validation plan")


@dataclass(frozen=True)
class IntrinsicFreezeToken:
    run_id: str
    ledger_snapshot_hash: str
    assumption_set_hash: str
    valuation_hash: str
    audit_hash: str
    industry_snapshot_hash: str
    source_snapshot_hash: str
    token_hash: str
    calibration_dataset_hash: str = ""
    calibration_snapshot_hash: str = ""
    distribution_hash: str = ""
    ambiguity_set_hash: str = ""
    payoff_model_set_hash: str = ""
    route_authorization_hash: str = ""
    entry_policy_version: str = ""
    entry_calculation_hash: str = ""


def validate_llm_authority(
    action: LLMAction,
    *,
    commits_assumption: bool = False,
    performs_valuation_math: bool = False,
    authorizes_stage: bool = False,
    mutates_canonical_system: bool = False,
) -> None:
    """LLM may observe/reason/propose/recover/design/ask, but never commit or authorize."""
    if action not in set(LLMAction):
        raise ValueError("unknown LLM action")
    forbidden = {
        "commits_assumption": commits_assumption,
        "performs_valuation_math": performs_valuation_math,
        "authorizes_stage": authorizes_stage,
        "mutates_canonical_system": mutates_canonical_system,
    }
    violations = tuple(name for name, enabled in forbidden.items() if enabled)
    if violations:
        raise PermissionError("LLM authority violation: " + ", ".join(violations))


def validate_doctrine_coverage(
    entries: tuple[DoctrineCoverageEntry, ...],
    *,
    expected_module_ids: tuple[str, ...],
) -> None:
    """No silent skip: every expected module/scanner/gate must leave a terminal trace."""
    by_id = {entry.module_id: entry for entry in entries}
    if len(by_id) != len(entries):
        raise ValueError("duplicate doctrine coverage module_id")
    missing = tuple(module_id for module_id in expected_module_ids if module_id not in by_id)
    if missing:
        raise ValueError("silent skip detected: " + ", ".join(missing))
    nonterminal = tuple(
        entry.module_id
        for entry in entries
        if entry.status in {StageStatus.PENDING, StageStatus.READY, StageStatus.RUNNING}
    )
    if nonterminal:
        raise ValueError("non-terminal doctrine coverage: " + ", ".join(nonterminal))


def next_recovery_step(attempted: tuple[RecoveryStep, ...]) -> RecoveryStep:
    """BLOCKED is the last resort, never the first response to None/missing output."""
    if len(set(attempted)) != len(attempted):
        raise ValueError("recovery steps cannot repeat")
    for index, step in enumerate(attempted):
        if index >= len(RECOVERY_LADDER) or step is not RECOVERY_LADDER[index]:
            raise ValueError("recovery steps must follow the canonical ladder")
    if len(attempted) >= len(RECOVERY_LADDER):
        return RecoveryStep.VALUATION_BLOCKED
    return RECOVERY_LADDER[len(attempted)]


def build_proposal_allowed(gap: CapabilityGap) -> bool:
    """Avoid over-building: ask the user only for material, reusable, genuinely missing capability."""
    return all(
        (
            gap.existing_capability_exhausted,
            gap.material_to_current_analysis,
            gap.reusable_beyond_current_case,
            gap.input_output_contract_defined,
        )
    )


def _distribution_freeze_lineage(
    *,
    distribution_hash: str,
    ambiguity_set_hash: str,
    payoff_model_set_hash: str,
    route_authorization_hash: str,
    entry_policy_version: str,
    entry_calculation_hash: str,
) -> tuple[str, ...]:
    values = (
        distribution_hash,
        ambiguity_set_hash,
        payoff_model_set_hash,
        route_authorization_hash,
        entry_policy_version,
        entry_calculation_hash,
    )
    if not any(values):
        return ()
    if not distribution_hash or not route_authorization_hash or not entry_policy_version:
        raise ValueError(
            "distribution freeze requires distribution, route authorization and entry-policy lineage"
        )
    if bool(ambiguity_set_hash) != bool(payoff_model_set_hash):
        raise ValueError(
            "distribution freeze ambiguity/payoff-model hashes must be supplied together"
        )
    return values


def issue_freeze_token(
    *,
    run_id: str,
    audit_passed: bool,
    coverage_entries: tuple[DoctrineCoverageEntry, ...],
    expected_module_ids: tuple[str, ...],
    ledger_snapshot_hash: str,
    assumption_set_hash: str,
    valuation_hash: str,
    audit_hash: str,
    industry_snapshot_hash: str,
    source_snapshot_hash: str,
    calibration_dataset_hash: str = "",
    calibration_snapshot_hash: str = "",
    distribution_hash: str = "",
    ambiguity_set_hash: str = "",
    payoff_model_set_hash: str = "",
    route_authorization_hash: str = "",
    entry_policy_version: str = "",
    entry_calculation_hash: str = "",
) -> IntrinsicFreezeToken:
    if not audit_passed:
        raise ValueError("audit PASS is required before intrinsic freeze")
    validate_doctrine_coverage(coverage_entries, expected_module_ids=expected_module_ids)
    blockers = tuple(entry.module_id for entry in coverage_entries if entry.unresolved_blocker)
    if blockers:
        raise ValueError("unresolved blocking coverage prevents freeze: " + ", ".join(blockers))
    fields = (
        run_id,
        ledger_snapshot_hash,
        assumption_set_hash,
        valuation_hash,
        audit_hash,
        industry_snapshot_hash,
        source_snapshot_hash,
    )
    if any(not value for value in fields):
        raise ValueError("freeze token requires all snapshot/value hashes")
    calibration_lineage = (calibration_dataset_hash, calibration_snapshot_hash)
    if bool(calibration_lineage[0]) != bool(calibration_lineage[1]):
        raise ValueError("calibration dataset and snapshot hashes must be supplied together")
    distribution_lineage = _distribution_freeze_lineage(
        distribution_hash=distribution_hash,
        ambiguity_set_hash=ambiguity_set_hash,
        payoff_model_set_hash=payoff_model_set_hash,
        route_authorization_hash=route_authorization_hash,
        entry_policy_version=entry_policy_version,
        entry_calculation_hash=entry_calculation_hash,
    )
    digest_fields = (*fields, *calibration_lineage)
    if distribution_lineage:
        digest_fields = (*digest_fields, "DISTRIBUTIONAL_LINEAGE_V1", *distribution_lineage)
    digest = hashlib.sha256("|".join(digest_fields).encode("utf-8")).hexdigest()
    return IntrinsicFreezeToken(
        *fields,
        digest,
        *calibration_lineage,
        distribution_hash,
        ambiguity_set_hash,
        payoff_model_set_hash,
        route_authorization_hash,
        entry_policy_version,
        entry_calculation_hash,
    )


def authorize_post_freeze(token: IntrinsicFreezeToken, *, run_id: str) -> None:
    if token.run_id != run_id:
        raise PermissionError("freeze token run mismatch")
    fields = (
        token.run_id,
        token.ledger_snapshot_hash,
        token.assumption_set_hash,
        token.valuation_hash,
        token.audit_hash,
        token.industry_snapshot_hash,
        token.source_snapshot_hash,
        token.calibration_dataset_hash,
        token.calibration_snapshot_hash,
    )
    try:
        distribution_lineage = _distribution_freeze_lineage(
            distribution_hash=token.distribution_hash,
            ambiguity_set_hash=token.ambiguity_set_hash,
            payoff_model_set_hash=token.payoff_model_set_hash,
            route_authorization_hash=token.route_authorization_hash,
            entry_policy_version=token.entry_policy_version,
            entry_calculation_hash=token.entry_calculation_hash,
        )
    except ValueError as exc:
        raise PermissionError(str(exc)) from exc
    digest_fields = fields
    if distribution_lineage:
        digest_fields = (*fields, "DISTRIBUTIONAL_LINEAGE_V1", *distribution_lineage)
    expected = hashlib.sha256("|".join(digest_fields).encode("utf-8")).hexdigest()
    if token.token_hash != expected:
        raise PermissionError("invalid intrinsic freeze token")
