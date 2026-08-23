from __future__ import annotations

from .assumption_compiler import CompiledAssumptionSet
from .control_plane import DoctrineCoverageEntry, StageStatus
from .doctrine_runtime import load_default_unit_contract_registry
from .generic_audit import audit_generic_intrinsic
from .impact_adapter import GenericDecisionImpactConfig, run_generic_decision_impact
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .scenario_binding import BoundScenarioSet
from .unit_contracts import UnitContractRegistry
from .valuation_execution import GenericValuationResult


def generic_audit_adapter(
    *,
    impact_config: GenericDecisionImpactConfig | None = None,
    unit_contract_registry: UnitContractRegistry | None = None,
) -> StageAdapter:
    registry = unit_contract_registry or load_default_unit_contract_registry()

    def run(context: OrchestratorContext) -> StageExecutionResult:
        compiled = context.data.get("compiled_assumption_set")
        scenario_set = context.data.get("bound_scenario_set")
        valuation = context.data.get("generic_valuation_result")
        coverage = context.data.get("pre_audit_doctrine_coverage")
        expected_modules = context.data.get("pre_audit_expected_unit_ids")

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

        try:
            impact = run_generic_decision_impact(
                context,
                registry=registry,
                config=impact_config,
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"decision-impact runtime failed before audit: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        audit = audit_generic_intrinsic(
            compiled=compiled,
            scenario_set=scenario_set,
            valuation=valuation,
            doctrine_coverage=coverage,
            expected_module_ids=expected_modules,
            run_context_keys=tuple(context.data),
            decision_impact=impact.batch,
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
            "decision-impact record and generic intrinsic audit passed; run is eligible for freeze if snapshot hashes are present",
            {
                **common_outputs,
                "generic_audit_report": audit.report,
                "audit_hash": audit.audit_hash,
                "audit_passed": True,
            },
        )

    return run
