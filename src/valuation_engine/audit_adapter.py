from __future__ import annotations

from dataclasses import replace

from .assumption_compiler import CompiledAssumptionSet
from .control_plane import DoctrineCoverageEntry, ExecutionMode, StageStatus
from .decision_impact import ModuleHistoryEntry
from .doctrine_runtime import load_default_unit_contract_registry
from .generic_audit import audit_generic_intrinsic
from .impact_adapter import GenericDecisionImpactConfig, run_generic_decision_impact
from .ledger import EvidenceLedger
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .records import AuditReport
from .risk_adapters import LiveBetaStageResult, LiveWACCStageResult
from .risk_impact import build_risk_impact_traces
from .scenario_binding import BoundScenarioSet
from .unit_contracts import UnitContractRegistry
from .valuation_execution import GenericValuationResult


def _effective_impact_config(
    configured: GenericDecisionImpactConfig | None,
    loaded_history,
) -> GenericDecisionImpactConfig:
    base = configured or GenericDecisionImpactConfig()
    if loaded_history in (None, {}):
        return base
    if not isinstance(loaded_history, dict):
        raise ValueError("module_impact_prior_history must be a mapping")
    normalized: dict[str, tuple[ModuleHistoryEntry, ...]] = {}
    for module_id, entries in loaded_history.items():
        if not isinstance(module_id, str) or not isinstance(entries, tuple) or not all(
            isinstance(item, ModuleHistoryEntry) for item in entries
        ):
            raise ValueError("module_impact_prior_history contains invalid entries")
        normalized[module_id] = entries
    normalized.update(base.prior_history)
    return replace(base, prior_history=normalized)


def generic_audit_adapter(
    *,
    impact_config: GenericDecisionImpactConfig | None = None,
    unit_contract_registry: UnitContractRegistry | None = None,
) -> StageAdapter:
    registry = unit_contract_registry or load_default_unit_contract_registry()

    def run(context: OrchestratorContext) -> StageExecutionResult:
        ledger = context.data.get("evidence_ledger")
        ledger_snapshot_hash = context.data.get("ledger_snapshot_hash")
        compiled = context.data.get("compiled_assumption_set")
        scenario_set = context.data.get("bound_scenario_set")
        valuation = context.data.get("generic_valuation_result")
        coverage = context.data.get("pre_audit_doctrine_coverage")
        expected_modules = context.data.get("pre_audit_expected_unit_ids")

        if not isinstance(ledger, EvidenceLedger):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "EvidenceLedger missing before audit", blocking=True)
        if not isinstance(ledger_snapshot_hash, str) or not ledger_snapshot_hash:
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "ledger_snapshot_hash missing before audit", blocking=True)
        if not isinstance(compiled, CompiledAssumptionSet):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "CompiledAssumptionSet missing", blocking=True)
        if not isinstance(scenario_set, BoundScenarioSet):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "BoundScenarioSet missing", blocking=True)
        if not isinstance(valuation, GenericValuationResult):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "GenericValuationResult missing", blocking=True)
        if not isinstance(coverage, tuple) or not all(isinstance(item, DoctrineCoverageEntry) for item in coverage):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "generated pre-audit doctrine coverage missing", blocking=True)
        if not isinstance(expected_modules, tuple) or not expected_modules or not all(isinstance(item, str) and item for item in expected_modules):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "generated pre-audit expected unit IDs missing", blocking=True)

        selected_methods = context.data.get("selected_methods", ())
        if not isinstance(selected_methods, tuple) or not all(isinstance(item, str) for item in selected_methods):
            return StageExecutionResult(StageStatus.BLOCKED, "selected_methods must be a string tuple", blocking=True)
        beta_raw = context.data.get("live_beta_result")
        wacc_raw = context.data.get("live_wacc_result")
        beta_result = beta_raw if isinstance(beta_raw, LiveBetaStageResult) else None
        wacc_result = wacc_raw if isinstance(wacc_raw, LiveWACCStageResult) else None
        capacity_report_raw = context.data.get("capacity_audit_report")
        capacity_hash_raw = context.data.get("capacity_audit_hash")
        if context.execution_mode is ExecutionMode.LIVE_PRIMARY and (
            not isinstance(capacity_report_raw, AuditReport)
            or not isinstance(capacity_hash_raw, str)
            or not capacity_hash_raw
        ):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "LIVE_PRIMARY capacity audit artifact/hash is required before generic audit",
                blocking=True,
            )
        capacity_report = (
            capacity_report_raw
            if isinstance(capacity_report_raw, AuditReport)
            else None
        )
        capacity_hash = (
            capacity_hash_raw
            if isinstance(capacity_hash_raw, str) and capacity_hash_raw
            else None
        )
        broker_required = bool(context.data.get("broker_research_required", False))
        broker_result_present = context.data.get("broker_research_prefreeze_result") is not None
        broker_report_raw = context.data.get("broker_research_audit_report")
        broker_hash_raw = context.data.get("broker_research_audit_hash")
        if (broker_required or broker_result_present) and (
            not isinstance(broker_report_raw, AuditReport)
            or not isinstance(broker_hash_raw, str)
            or not broker_hash_raw
            or not bool(context.data.get("broker_research_audit_passed"))
        ):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "Broker Research audit artifact/hash is required before generic audit",
                blocking=True,
            )
        broker_report = (
            broker_report_raw if isinstance(broker_report_raw, AuditReport) else None
        )
        broker_hash = (
            broker_hash_raw
            if isinstance(broker_hash_raw, str) and broker_hash_raw
            else None
        )

        try:
            effective_config = _effective_impact_config(
                impact_config,
                context.data.get("module_impact_prior_history"),
            )
            impact = run_generic_decision_impact(
                context,
                registry=registry,
                config=effective_config,
            )
            risk_traces = build_risk_impact_traces(
                beta_result=beta_result,
                wacc_result=wacc_result,
                valuation=valuation,
                selected_methods=selected_methods,
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"decision-impact runtime failed before audit: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        audit = audit_generic_intrinsic(
            run_id=context.run_id,
            ledger=ledger,
            ledger_snapshot_hash=ledger_snapshot_hash,
            compiled=compiled,
            scenario_set=scenario_set,
            valuation=valuation,
            doctrine_coverage=coverage,
            expected_module_ids=expected_modules,
            run_context_keys=tuple(context.data),
            decision_impact=impact.batch,
            selected_methods=selected_methods,
            beta_result=beta_result,
            wacc_result=wacc_result,
            external_guardrail_findings=(
                *(capacity_report.findings if capacity_report is not None else ()),
                *(broker_report.findings if broker_report is not None else ()),
            ),
            external_guardrail_hashes=(
                *((capacity_hash,) if capacity_hash is not None else ()),
                *((broker_hash,) if broker_hash is not None else ()),
            ),
        )
        common_outputs = {
            "decision_impact_result": impact,
            "decision_impact_batch": impact.batch,
            "decision_impact_hash": impact.impact_hash,
            "decision_impact_completed": True,
            "decision_impact_measurement_clean": impact.completed,
            "module_impact_assessments": tuple(
                item.assessment
                for item in impact.batch.module_observations
                if item.assessment is not None
            ),
            "observed_risk_impact_traces": risk_traces,
            "research_loadout_recommendations": impact.batch.loadout_recommendations,
            "retirement_review_candidates": impact.retirement_review_candidates,
        }
        blocking = tuple(item.detail for item in audit.report.findings if item.blocking and not item.passed)
        if blocking:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "generic intrinsic audit failed after decision-impact measurement: " + " | ".join(blocking),
                {
                    **common_outputs,
                    "generic_audit_report": audit.report,
                    "audit_hash": audit.audit_hash,
                    "audit_passed": False,
                },
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "decision-impact record and run-bound generic intrinsic audit passed; run is eligible for freeze if snapshot hashes are present",
            {
                **common_outputs,
                "generic_audit_report": audit.report,
                "audit_hash": audit.audit_hash,
                "audit_passed": True,
            },
        )

    return run
