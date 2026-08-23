from __future__ import annotations

from dataclasses import dataclass

from .control_plane import StageStatus
from .funding_runtime import FundingSourceUseBinding, assess_funding_sources_and_uses
from .ledger import EvidenceLedger
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .wacc import CustomerAdvanceCreditEvidence


@dataclass(frozen=True)
class FundingRuntimeConfig:
    binding: FundingSourceUseBinding | None = None
    credit_evidence: CustomerAdvanceCreditEvidence | None = None


def _required_funding_scans(context: OrchestratorContext) -> tuple[str, ...]:
    plan = context.data.get("module_requirement_plan")
    segments = getattr(plan, "segments", ())
    return tuple(
        dict.fromkeys(
            scan
            for segment in segments
            for scan in getattr(segment, "funding_scans", ())
        )
    )


def upstream_funding_runtime_adapter(
    *,
    config: FundingRuntimeConfig,
) -> StageAdapter:
    """Assess funding coverage from typed Evidence without directly changing WACC."""

    def run(context: OrchestratorContext) -> StageExecutionResult:
        required_scans = _required_funding_scans(context)
        if not required_scans:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "selected Industry DNA does not require a dedicated upstream-funding scan",
                {
                    "upstream_funding_scan_state": "NOT_APPLICABLE",
                    "required_funding_scans": (),
                },
            )

        if config.binding is None:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "funding scan is required but no FundingSourceUseBinding was supplied",
                {"required_funding_scans": required_scans},
                blocking=True,
            )

        ledger = context.data.get("evidence_ledger")
        target_id = context.data.get("target_id")
        if not isinstance(ledger, EvidenceLedger):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "EvidenceLedger is missing before upstream funding assessment",
                {"required_funding_scans": required_scans},
                blocking=True,
            )
        if not isinstance(target_id, str) or not target_id:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "target_id is missing before upstream funding assessment",
                {"required_funding_scans": required_scans},
                blocking=True,
            )

        try:
            result = assess_funding_sources_and_uses(
                target_id=target_id,
                ledger=ledger,
                binding=config.binding,
                credit_evidence=config.credit_evidence,
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"funding assessment failed: {type(exc).__name__}: {exc}",
                {"required_funding_scans": required_scans},
                blocking=True,
            )

        if not result.passed or result.assessment is None:
            detail_parts: list[str] = []
            if result.missing_metrics:
                detail_parts.append("missing=" + ",".join(result.missing_metrics))
            if result.blocking_findings:
                detail_parts.append("findings=" + " | ".join(result.blocking_findings))
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "funding evidence is incomplete or inconsistent"
                + (": " + "; ".join(detail_parts) if detail_parts else ""),
                {
                    "required_funding_scans": required_scans,
                    "funding_missing_metrics": result.missing_metrics,
                    "funding_blocking_findings": result.blocking_findings,
                },
                blocking=True,
            )

        assessment = result.assessment
        state = "FULLY_FUNDED" if assessment.fully_funded else "FUNDING_GAP"
        constraints = (
            ()
            if assessment.fully_funded
            else (
                f"verified funding shortfall {assessment.funding_gap} {assessment.reporting_unit} as of {assessment.as_of}",
            )
        )
        status = StageStatus.PASS if assessment.fully_funded else StageStatus.WARNING
        rationale = (
            "verified funding sources cover the observed funding need"
            if assessment.fully_funded
            else "verified funding sources do not fully cover the observed funding need"
        )
        if assessment.credit_improvement_candidate:
            rationale += "; separate six-part structural credit evidence passed, so WACC review may consider a credit-improvement candidate"
        elif assessment.credit_evidence_present:
            rationale += "; supplied credit evidence did not pass the full WACC-reduction gate"
        else:
            rationale += "; no WACC credit improvement is inferred from funding coverage alone"

        return StageExecutionResult(
            status,
            rationale,
            {
                "required_funding_scans": required_scans,
                "funding_assessment": assessment,
                "funded_demand_assessment": state,
                "funding_gap": assessment.funding_gap,
                "funding_coverage_ratio": assessment.funding_coverage_ratio,
                "credit_improvement_candidate": assessment.credit_improvement_candidate,
                "financing_constraints": constraints,
                "upstream_funding_scan_state": state,
            },
        )

    return run
