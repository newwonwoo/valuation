from __future__ import annotations

from typing import Callable

from .control_plane import StageStatus
from .llm_staff import RedTeamProposal
from .module_plan import ModuleRequirementPlan
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .per import EconomicAssumptionFingerprint, validate_dcf_per_assumption_consistency
from .records import HypothesisRecord
from .scenario_binding import BoundScenarioSet
from .valuation_method_intent import ValuationMethodIntent


DCFConsistencyFingerprintLoader = Callable[
    [OrchestratorContext], EconomicAssumptionFingerprint
]


def chain_stage_adapters(*adapters: StageAdapter) -> StageAdapter:
    if not adapters:
        raise ValueError("adapter chain cannot be empty")

    def run(context: OrchestratorContext) -> StageExecutionResult:
        data = dict(context.data)
        outputs: dict[str, object] = {}
        rationales: list[str] = []
        statuses: list[StageStatus] = []
        for adapter in adapters:
            temp = OrchestratorContext(
                context.run_id,
                context.execution_mode,
                data,
                context.stage_traces,
                context.freeze_token,
            )
            result = adapter(temp)
            rationales.append(result.rationale)
            statuses.append(result.status)
            if result.blocking:
                merged = dict(outputs)
                overlap = set(merged).intersection(result.outputs)
                if overlap:
                    return StageExecutionResult(
                        StageStatus.BLOCKED,
                        "adapter chain produced duplicate output keys: "
                        + ", ".join(sorted(overlap)),
                        blocking=True,
                    )
                merged.update(result.outputs)
                return StageExecutionResult(
                    result.status,
                    " | ".join(rationales),
                    merged,
                    blocking=True,
                )
            overlap = set(data).intersection(result.outputs)
            if overlap:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "adapter chain attempted to overwrite context keys: "
                    + ", ".join(sorted(overlap)),
                    blocking=True,
                )
            data.update(result.outputs)
            outputs.update(result.outputs)
        if any(status is StageStatus.WARNING for status in statuses):
            status = StageStatus.WARNING
        elif any(status is StageStatus.RECOVERED for status in statuses):
            status = StageStatus.RECOVERED
        elif all(
            status is StageStatus.SKIPPED_NOT_APPLICABLE for status in statuses
        ):
            status = StageStatus.SKIPPED_NOT_APPLICABLE
        else:
            status = StageStatus.PASS
        return StageExecutionResult(status, " | ".join(rationales), outputs)

    return run


def conditional_funding_adapter(inner: StageAdapter | None) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        plan = context.data.get("module_requirement_plan")
        if not isinstance(plan, ModuleRequirementPlan):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "ModuleRequirementPlan missing before funding scan",
                blocking=True,
            )
        required = tuple(
            dict.fromkeys(
                scan for segment in plan.segments for scan in segment.funding_scans
            )
        )
        if not required:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "selected Industry DNA does not require a dedicated upstream funding scan",
                {"upstream_funding_scan_state": "NOT_APPLICABLE"},
            )
        if inner is None:
            return StageExecutionResult(
                StageStatus.NOT_IMPLEMENTED,
                "route requires upstream funding scan but no LIVE_PRIMARY FundingScanner provider is configured",
                {"required_funding_scans": required},
                blocking=True,
            )
        return inner(context)

    return run


def conditional_method_intent_adapter(
    inner: StageAdapter | None,
    *,
    requirement: str,
    label: str,
) -> StageAdapter:
    if requirement not in {"requires_beta", "requires_wacc"}:
        raise ValueError(
            "conditional method intent requirement must be requires_beta or requires_wacc"
        )

    def run(context: OrchestratorContext) -> StageExecutionResult:
        intent = context.data.get("valuation_method_intent")
        if not isinstance(intent, ValuationMethodIntent) or not intent.ready:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                f"resolved ValuationMethodIntent is required before {label}",
                blocking=True,
            )
        if not bool(getattr(intent, requirement)):
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                f"selected exact economic method path does not require {label}",
            )
        if inner is None:
            return StageExecutionResult(
                StageStatus.NOT_IMPLEMENTED,
                f"selected valuation method requires {label} but no LIVE_PRIMARY provider is configured",
                blocking=True,
            )
        return inner(context)

    return run


def conditional_warranted_per_adapter(inner: StageAdapter | None) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        intent = context.data.get("valuation_method_intent")
        if not isinstance(intent, ValuationMethodIntent) or not intent.ready:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "resolved ValuationMethodIntent is required before Warranted PER",
                blocking=True,
            )
        if not intent.warranted_per_segments:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "selected Industry DNA does not route any segment to Warranted PER",
                {"warranted_per_applicable": False},
            )
        if inner is None:
            return StageExecutionResult(
                StageStatus.NOT_IMPLEMENTED,
                "Warranted PER is routed but no LIVE_PRIMARY PERInputsLoader provider is configured",
                {"warranted_per_segments": intent.warranted_per_segments},
                blocking=True,
            )
        return inner(context)

    return run


def recoverable_red_team_adapter(inner: StageAdapter) -> StageAdapter:
    """Let the canonical next RESEARCH_LOOP stage handle recoverable blockers."""

    def run(context: OrchestratorContext) -> StageExecutionResult:
        result = inner(context)
        if result.status is StageStatus.RECOVERY_REQUIRED and result.blocking:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                result.rationale
                + "; recovery delegated to the canonical RESEARCH_LOOP stage",
                result.outputs,
                blocking=False,
            )
        return result

    return run


def research_loop_recovery_adapter(
    recovery_adapter: StageAdapter | None,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        proposal = context.data.get("red_team_proposal")
        if not isinstance(proposal, RedTeamProposal):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "RedTeamProposal missing before research recovery",
                blocking=True,
            )
        unresolved = tuple(
            item.id for item in proposal.issues if item.blocking and not item.resolved
        )
        if not unresolved:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "Blind Red Team left no unresolved blocking issue",
                {"research_round_count": 1},
            )
        if recovery_adapter is None:
            return StageExecutionResult(
                StageStatus.NOT_IMPLEMENTED,
                "Red Team requires targeted recovery but no LIVE_PRIMARY research-recovery provider is configured",
                {"unresolved_red_team_issue_ids": unresolved},
                blocking=True,
            )

        result = recovery_adapter(context)
        if result.blocking:
            return result
        recovered = result.outputs.get("recovered_red_team_proposal")
        if not isinstance(recovered, RedTeamProposal):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "research recovery must emit recovered_red_team_proposal",
                dict(result.outputs),
                blocking=True,
            )
        try:
            recovered.validate()
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"recovered RedTeamProposal is invalid: {exc}",
                blocking=True,
            )

        recovered_by_id = {item.id: item for item in recovered.issues}
        omitted_original = tuple(
            issue_id for issue_id in unresolved if issue_id not in recovered_by_id
        )
        unresolved_original = tuple(
            issue_id
            for issue_id in unresolved
            if issue_id in recovered_by_id and not recovered_by_id[issue_id].resolved
        )
        if omitted_original or unresolved_original:
            details: list[str] = []
            if omitted_original:
                details.append("omitted=" + ", ".join(omitted_original))
            if unresolved_original:
                details.append("not_resolved=" + ", ".join(unresolved_original))
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "research recovery must retain and explicitly resolve every original Red-Team blocker: "
                + "; ".join(details),
                dict(result.outputs),
                blocking=True,
            )

        remaining = tuple(
            item.id for item in recovered.issues if item.blocking and not item.resolved
        )
        if remaining:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "research recovery left unresolved Red-Team blockers: "
                + ", ".join(remaining),
                dict(result.outputs),
                blocking=True,
            )

        hypotheses = result.outputs.get("recovered_hypotheses")
        if hypotheses is not None and (
            not isinstance(hypotheses, tuple)
            or not all(isinstance(item, HypothesisRecord) for item in hypotheses)
        ):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "recovered_hypotheses must be a tuple of HypothesisRecord",
                blocking=True,
            )
        outputs = dict(result.outputs)
        outputs.setdefault("research_round_count", 2)
        outputs["recovered_red_team_issue_ids"] = unresolved
        return StageExecutionResult(
            StageStatus.RECOVERED,
            "targeted research recovery explicitly resolved every prior Blind Red Team blocker",
            outputs,
        )

    return run


def recovery_aware_bridge_adapter(inner: StageAdapter) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        recovered_red_team = context.data.get("recovered_red_team_proposal")
        recovered_hypotheses = context.data.get("recovered_hypotheses")
        if recovered_red_team is None and recovered_hypotheses is None:
            return inner(context)
        data = dict(context.data)
        if recovered_red_team is not None:
            if not isinstance(recovered_red_team, RedTeamProposal):
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "recovered_red_team_proposal has invalid type",
                    blocking=True,
                )
            data["red_team_proposal"] = recovered_red_team
        if recovered_hypotheses is not None:
            if not isinstance(recovered_hypotheses, tuple) or not all(
                isinstance(item, HypothesisRecord) for item in recovered_hypotheses
            ):
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "recovered_hypotheses has invalid type",
                    blocking=True,
                )
            data["hypotheses"] = recovered_hypotheses
        return inner(
            OrchestratorContext(
                context.run_id,
                context.execution_mode,
                data,
                context.stage_traces,
                context.freeze_token,
            )
        )

    return run


def dcf_consistency_fingerprint_adapter(
    loader: DCFConsistencyFingerprintLoader | None,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        intent = context.data.get("valuation_method_intent")
        if not isinstance(intent, ValuationMethodIntent) or not intent.ready:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "ValuationMethodIntent missing before DCF consistency fingerprint",
                blocking=True,
            )
        if not intent.warranted_per_segments:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "no Warranted PER cross-check requires a DCF fingerprint",
            )
        if loader is None:
            return StageExecutionResult(
                StageStatus.NOT_IMPLEMENTED,
                "Warranted PER requires a driver-specific DCF EconomicAssumptionFingerprint provider",
                {"warranted_per_segments": intent.warranted_per_segments},
                blocking=True,
            )
        try:
            fingerprint = loader(context)
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                f"DCF fingerprint loader failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        if not isinstance(fingerprint, EconomicAssumptionFingerprint):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "DCF fingerprint loader must return EconomicAssumptionFingerprint",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "driver-specific DCF economic fingerprint bound for cross-method consistency",
            {"dcf_assumption_fingerprint": fingerprint},
        )

    return run


def dcf_per_consistency_gate_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        intent = context.data.get("valuation_method_intent")
        if not isinstance(intent, ValuationMethodIntent) or not intent.ready:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "ValuationMethodIntent missing before DCF-PER consistency gate",
                blocking=True,
            )
        if (
            not intent.warranted_per_segments
            or context.data.get("warranted_per_applicable") is False
        ):
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "DCF-PER consistency gate is not applicable",
            )
        dcf = context.data.get("dcf_assumption_fingerprint")
        per = context.data.get("per_assumption_fingerprint")
        if not isinstance(dcf, EconomicAssumptionFingerprint) or not isinstance(
            per, EconomicAssumptionFingerprint
        ):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "DCF and PER assumption fingerprints are required",
                blocking=True,
            )
        try:
            validate_dcf_per_assumption_consistency(dcf, per)
        except ValueError as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"DCF-PER assumption consistency failed: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "DCF and PER growth/margin/reinvestment fingerprints are consistent",
        )

    return run


def cross_method_double_count_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        valuation = context.data.get("generic_valuation_result")
        if valuation is None:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "valuation output missing before cross-method audit",
                blocking=True,
            )
        for scenario in valuation.scenarios:
            if len(scenario.economic_path_ids) != len(
                set(scenario.economic_path_ids)
            ):
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    f"duplicate economic path in scenario {scenario.scenario_id}",
                    blocking=True,
                )
        return StageExecutionResult(
            StageStatus.PASS,
            "cross-method economic paths are unique",
        )

    return run


def probability_distribution_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        scenarios = context.data.get("bound_scenario_set")
        if not isinstance(scenarios, BoundScenarioSet):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "scenario set missing before probability analysis",
                blocking=True,
            )
        if not scenarios.numeric_weighting_allowed:
            return StageExecutionResult(
                StageStatus.WARNING,
                "scenario probabilities are not calibration-authorized; numeric expected value remains disabled",
                {"probability_distribution_status": "DESCRIPTIVE_ONLY"},
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "calibrated probability weighting is authorized by the bound scenario set",
            {"probability_distribution_status": "CALIBRATED"},
        )

    return run
