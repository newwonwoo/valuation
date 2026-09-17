from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .ablation import AblationBatchResult, AblationStatus
from .assumption_compiler import CompiledAssumptionSet
from .control_plane import DoctrineCoverageEntry, StageStatus, validate_doctrine_coverage
from .distributional_runtime import (
    DistributionalAPVExecutionSpec,
    DistributionalPrimaryValuationResult,
    execute_distributional_apv,
)
from .ledger import EvidenceLedger
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .records import AuditFinding, AuditReport
from .risk_adapters import LiveBetaStageResult, LiveWACCStageResult
from .run_hash import (
    bound_scenario_set_hash,
    compiled_assumption_set_hash,
    compiled_evidence_hash_mismatches,
    evidence_ledger_snapshot_hash,
)
from .scenario_binding import BoundScenarioSet


_PROHIBITED_PRE_FREEZE_KEYS = {
    "market_price",
    "current_market_price",
    "target_price",
    "target_multiple",
    "target_company_consensus",
    "street_consensus",
    "rating",
}


@dataclass(frozen=True)
class DistributionalAuditResult:
    report: AuditReport
    audit_hash: str

    @property
    def passed(self) -> bool:
        return self.report.passed


def audit_distributional_intrinsic(
    *,
    run_id: str,
    ledger: EvidenceLedger,
    ledger_snapshot_hash: str,
    compiled: CompiledAssumptionSet,
    scenario_set: BoundScenarioSet,
    execution_spec: DistributionalAPVExecutionSpec,
    valuation: DistributionalPrimaryValuationResult,
    doctrine_coverage: tuple[DoctrineCoverageEntry, ...],
    expected_module_ids: tuple[str, ...],
    run_context_keys: tuple[str, ...] = (),
    decision_impact: AblationBatchResult | None = None,
    beta_result: LiveBetaStageResult | None = None,
    wacc_result: LiveWACCStageResult | None = None,
    risk_chain_requires_beta: bool = False,
    risk_chain_requires_wacc: bool = False,
) -> DistributionalAuditResult:
    findings: list[AuditFinding] = []

    findings.append(
        AuditFinding(
            "run_identity_binding",
            bool(run_id),
            True,
            "distributional audit is bound to a non-empty run_id",
        )
    )

    leaked = tuple(sorted(set(run_context_keys).intersection(_PROHIBITED_PRE_FREEZE_KEYS)))
    findings.append(
        AuditFinding(
            "pre_freeze_market_isolation",
            not leaked,
            True,
            "no target Street/current-price key exists before freeze"
            if not leaked
            else "leaked keys: " + ", ".join(leaked),
        )
    )

    current_ledger_hash = evidence_ledger_snapshot_hash(ledger)
    ledger_ok = bool(ledger_snapshot_hash) and current_ledger_hash == ledger_snapshot_hash
    findings.append(
        AuditFinding(
            "ledger_snapshot_integrity",
            ledger_ok,
            True,
            "EvidenceLedger content replays to the frozen ledger hash",
        )
    )

    evidence_mismatches = compiled_evidence_hash_mismatches(compiled, ledger)
    evidence_ok = not evidence_mismatches
    findings.append(
        AuditFinding(
            "evidence_assumption_hash_binding",
            evidence_ok,
            True,
            "compiled evidence hashes replay against the frozen ledger"
            if evidence_ok
            else "compiled evidence mismatch: " + ", ".join(evidence_mismatches),
        )
    )

    traceability_ok = bool(compiled.assumptions) and all(
        item.bridge_id
        and item.hypothesis_id
        and item.evidence_ids
        and item.economic_path_id
        and item.input_evidence_hash
        and item.transform_id
        for item in compiled.assumptions
    )
    findings.append(
        AuditFinding(
            "compiled_traceability",
            traceability_ok,
            True,
            "every compiled assumption preserves Evidence→Hypothesis→Bridge→Transform→EconomicPath trace",
        )
    )

    assumption_ok = (
        bool(compiled.assumption_set_hash)
        and compiled.assumption_set_hash == compiled_assumption_set_hash(compiled)
    )
    findings.append(
        AuditFinding(
            "assumption_hash_integrity",
            assumption_ok,
            True,
            "CompiledAssumptionSet hash replays from immutable contents",
        )
    )

    scenario_ok = (
        bool(scenario_set.scenario_set_hash)
        and scenario_set.scenario_set_hash == bound_scenario_set_hash(compiled, scenario_set)
    )
    findings.append(
        AuditFinding(
            "scenario_hash_integrity",
            scenario_ok,
            True,
            "BoundScenarioSet hash replays even when the primary value route remains continuous/unassigned",
        )
    )

    target_ok = (
        compiled.target_id == scenario_set.target_id == execution_spec.target_id == valuation.target_id
    )
    findings.append(
        AuditFinding(
            "target_identity_consistency",
            target_ok,
            True,
            "compiled, scenario, distribution spec and primary valuation target IDs match",
        )
    )

    try:
        valuation.envelope.validate()
        replay = execute_distributional_apv(execution_spec)
        replay_ok = replay == valuation
        replay_detail = (
            "operating, financing, APV, payoff, probability/ambiguity and route arithmetic replay exactly"
            if replay_ok
            else "distributional primary result differs from a fresh replay of the same typed input"
        )
    except Exception as exc:
        replay_ok = False
        replay_detail = f"distributional replay failed: {type(exc).__name__}: {exc}"
    findings.append(
        AuditFinding(
            "distributional_calculation_replay",
            replay_ok,
            True,
            replay_detail,
        )
    )

    lineage = valuation.envelope.distribution_lineage
    lineage_ok = (
        lineage is not None
        and lineage.distribution_hash == valuation.distribution_hash
        and lineage.route_authorization_hash
        == valuation.route_authorization.authorization_hash
        and valuation.envelope.source_value_hash == valuation.distribution_hash
    )
    findings.append(
        AuditFinding(
            "distribution_lineage_binding",
            lineage_ok,
            True,
            "distribution, route authorization and envelope hashes are one immutable lineage",
        )
    )

    authorization = valuation.route_authorization
    value_authorized = (
        authorization.pathwise_value_distribution_authorized
        or authorization.expected_value_interval_authorized
    )
    findings.append(
        AuditFinding(
            "distribution_route_authorization",
            value_authorized and not authorization.diagnostic_only,
            True,
            "primary intrinsic output is authorized by economic route policy rather than a scenario-anchor replay",
        )
    )

    if valuation.pathwise_distribution is not None:
        distribution_contract_ok = (
            valuation.pathwise_distribution.valuation_distribution_authorized
            and valuation.pathwise_distribution.distribution_hash == valuation.distribution_hash
            and authorization.pathwise_value_distribution_authorized
        )
    else:
        distribution_contract_ok = (
            valuation.ambiguity_intrinsic_range is not None
            and valuation.robust_entry is not None
            and lineage is not None
            and lineage.ambiguity_set_hash
            == valuation.robust_entry.probability_ambiguity_set_hash
            and lineage.payoff_model_set_hash == valuation.robust_entry.payoff_model_set_hash
            and authorization.expected_value_interval_authorized
            and not authorization.point_target_authorized
            and not authorization.success_probability_claim_authorized
        )
    findings.append(
        AuditFinding(
            "distribution_output_contract",
            distribution_contract_ok,
            True,
            "calibrated pathwise output or governed-prior ambiguity output obeys its allowed claim set",
        )
    )

    beta_ok = True
    beta_detail = "selected primary route does not require Beta"
    if risk_chain_requires_beta:
        beta_ok = isinstance(beta_result, LiveBetaStageResult)
        if beta_ok:
            beta_receipt = f"beta:{beta_result.snapshot_hash}"
            beta_ok = beta_receipt in execution_spec.route_evidence_path_ids
            beta_detail = (
                "Beta snapshot is explicitly consumed by the distributional route evidence"
                if beta_ok
                else "distributional route did not bind the current Beta snapshot"
            )
        else:
            beta_detail = "distributional primary route requires Beta but no typed Beta result exists"
    findings.append(AuditFinding("beta_to_distribution_consumption", beta_ok, True, beta_detail))

    wacc_ok = True
    wacc_detail = "selected primary route does not require WACC"
    if risk_chain_requires_wacc:
        wacc_ok = isinstance(wacc_result, LiveWACCStageResult)
        if wacc_ok:
            wacc_receipt = f"wacc:{wacc_result.snapshot_hash}"
            wacc_ok = wacc_receipt in execution_spec.route_evidence_path_ids
            wacc_detail = (
                "WACC snapshot is explicitly consumed by the distributional route evidence"
                if wacc_ok
                else "distributional route did not bind the current WACC snapshot"
            )
        else:
            wacc_detail = "selected reference-method loadout requires WACC but no typed WACC result exists"
    findings.append(AuditFinding("wacc_to_distribution_consumption", wacc_ok, True, wacc_detail))

    try:
        validate_doctrine_coverage(doctrine_coverage, expected_module_ids=expected_module_ids)
        blockers = tuple(item.module_id for item in doctrine_coverage if item.unresolved_blocker)
        doctrine_ok = not blockers
        doctrine_detail = (
            "pre-audit doctrine coverage complete"
            if doctrine_ok
            else "unresolved blockers: " + ", ".join(blockers)
        )
    except ValueError as exc:
        doctrine_ok = False
        doctrine_detail = str(exc)
    findings.append(AuditFinding("doctrine_coverage", doctrine_ok, True, doctrine_detail))

    if decision_impact is None:
        impact_ok = False
        impact_detail = "decision-impact artifact missing; disclosed as non-blocking"
    else:
        failed = tuple(
            item.module_id
            for item in decision_impact.module_observations
            if item.status is AblationStatus.FAILED
        )
        impact_ok = not failed
        impact_detail = (
            "decision-impact measurement completed"
            if impact_ok
            else "decision-impact failures: " + ", ".join(failed)
        )
    findings.append(AuditFinding("decision_impact_trace", impact_ok, False, impact_detail))

    hash_chain_ok = all((ledger_ok, evidence_ok, assumption_ok, scenario_ok, replay_ok, lineage_ok))
    findings.append(
        AuditFinding(
            "immutable_hash_chain",
            hash_chain_ok,
            True,
            "Evidence→Assumption→Scenario→Distributional calculation→Envelope lineage replays as one chain",
        )
    )

    report = AuditReport(tuple(findings))
    payload = "\n".join(
        [
            run_id,
            ledger_snapshot_hash,
            compiled.assumption_set_hash,
            scenario_set.scenario_set_hash,
            valuation.envelope.envelope_hash,
            valuation.distribution_hash,
            valuation.route_authorization.authorization_hash,
            lineage.ambiguity_set_hash if lineage is not None else "",
            lineage.payoff_model_set_hash if lineage is not None else "",
            lineage.entry_policy_version if lineage is not None else "",
            lineage.entry_calculation_hash if lineage is not None else "",
        ]
        + [f"{item.check}|{item.passed}|{item.blocking}|{item.detail}" for item in report.findings]
    )
    return DistributionalAuditResult(
        report=report,
        audit_hash=sha256(payload.encode("utf-8")).hexdigest(),
    )


def distributional_audit_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        ledger = context.data.get("evidence_ledger")
        ledger_hash = context.data.get("ledger_snapshot_hash")
        compiled = context.data.get("compiled_assumption_set")
        scenario_set = context.data.get("bound_scenario_set")
        execution_spec = context.data.get("distributional_apv_execution_spec")
        valuation = context.data.get("distributional_primary_result")
        coverage = context.data.get("pre_audit_doctrine_coverage")
        expected = context.data.get("expected_module_ids")
        beta_result = context.data.get("live_beta_result")
        wacc_result = context.data.get("live_wacc_result")
        decision_impact = context.data.get("decision_impact_batch")

        if not isinstance(ledger, EvidenceLedger):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "EvidenceLedger missing before distributional audit", blocking=True)
        if not isinstance(ledger_hash, str) or not ledger_hash:
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "ledger_snapshot_hash missing before distributional audit", blocking=True)
        if not isinstance(compiled, CompiledAssumptionSet):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "CompiledAssumptionSet missing before distributional audit", blocking=True)
        if not isinstance(scenario_set, BoundScenarioSet):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "BoundScenarioSet missing before distributional audit", blocking=True)
        if not isinstance(execution_spec, DistributionalAPVExecutionSpec):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "DistributionalAPVExecutionSpec missing before distributional audit", blocking=True)
        if not isinstance(valuation, DistributionalPrimaryValuationResult):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "distributional primary result missing before audit", blocking=True)
        if not isinstance(coverage, tuple) or not all(isinstance(item, DoctrineCoverageEntry) for item in coverage):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "pre-audit doctrine coverage missing", blocking=True)
        if not isinstance(expected, tuple) or not all(isinstance(item, str) and item for item in expected):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "expected module IDs missing", blocking=True)
        if decision_impact is not None and not isinstance(decision_impact, AblationBatchResult):
            return StageExecutionResult(StageStatus.BLOCKED, "decision_impact_batch has invalid type", blocking=True)

        try:
            result = audit_distributional_intrinsic(
                run_id=context.run_id,
                ledger=ledger,
                ledger_snapshot_hash=ledger_hash,
                compiled=compiled,
                scenario_set=scenario_set,
                execution_spec=execution_spec,
                valuation=valuation,
                doctrine_coverage=coverage,
                expected_module_ids=expected,
                run_context_keys=tuple(context.data.keys()),
                decision_impact=decision_impact,
                beta_result=beta_result if isinstance(beta_result, LiveBetaStageResult) else None,
                wacc_result=wacc_result if isinstance(wacc_result, LiveWACCStageResult) else None,
                risk_chain_requires_beta=bool(context.data.get("risk_chain_requires_beta", False)),
                risk_chain_requires_wacc=bool(context.data.get("risk_chain_requires_wacc", False)),
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"distributional audit failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        lineage = valuation.envelope.distribution_lineage
        outputs: dict[str, object] = {
            "audit_report": result.report,
            "audit_hash": result.audit_hash,
            "audit_passed": result.passed,
            "distribution_authorization": valuation.route_authorization,
            "entry_price_authorization": valuation.route_authorization.entry_price_authorized,
        }
        if lineage is not None and lineage.payoff_model_set_hash:
            outputs["payoff_ambiguity_audit_hash"] = sha256(
                (
                    "distributional-replay-audit/v1:"
                    + result.audit_hash
                    + ":"
                    + lineage.payoff_model_set_hash
                ).encode("utf-8")
            ).hexdigest()
        if not result.passed:
            blockers = tuple(
                item.detail for item in result.report.findings if item.blocking and not item.passed
            )
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "distributional audit blocking findings: " + " | ".join(blockers),
                outputs,
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "distributional intrinsic calculation, route authority and immutable lineage replayed successfully",
            outputs,
        )

    return run


__all__ = [
    "DistributionalAuditResult",
    "audit_distributional_intrinsic",
    "distributional_audit_adapter",
]
