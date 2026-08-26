from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .industry_knowledge import MechanismAssessment, PromotionStatus


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    DEFER = "defer"


@dataclass(frozen=True)
class ModuleApprovalRecord:
    mechanism_id: str
    decision: ApprovalDecision
    reviewer: str
    rationale: str
    regression_passed: bool
    red_team_passed: bool
    unresolved_critical_conflict: bool = False


@dataclass(frozen=True)
class CompiledIndustryRule:
    mechanism_id: str
    version: str
    canonical: bool
    rationale: str


def compile_approved_rule(
    mechanism_id: str,
    assessment: MechanismAssessment,
    approval: ModuleApprovalRecord,
    *,
    version: str,
) -> CompiledIndustryRule:
    if assessment.status is not PromotionStatus.MANUAL_APPROVAL_REQUIRED:
        raise ValueError("mechanism is not eligible for canonical approval")
    if approval.mechanism_id != mechanism_id:
        raise ValueError("approval record mechanism mismatch")
    if approval.decision is not ApprovalDecision.APPROVE:
        raise ValueError("explicit approval is required")
    if not approval.regression_passed or not approval.red_team_passed:
        raise ValueError("regression and Red Team must pass")
    if approval.unresolved_critical_conflict:
        raise ValueError("critical conflict blocks module promotion")
    return CompiledIndustryRule(mechanism_id, version, True, approval.rationale)
