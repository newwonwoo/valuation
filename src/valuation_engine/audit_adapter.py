from __future__ import annotations

from .assumption_compiler import CompiledAssumptionSet
from .control_plane import DoctrineCoverageEntry, StageStatus
from .generic_audit import audit_generic_intrinsic
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .scenario_binding import BoundScenarioSet
from .valuation_execution import GenericValuationResult


def generic_audit_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        compiled = context.data.get("compiled_assumption_set")
        scenario_set = context.data.get("bound_scenario_set")
        valuation = context.data.get("generic_valuation_result")
        coverage = context.data.get("doctrine_coverage")
        expected_modules = context.data.get("expected_module_ids")

        if not isinstance(compiled, CompiledAssumptionSet):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "CompiledAssumptionSet missing", blocking=True)
        if not isinstance(scenario_set, BoundScenarioSet):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "BoundScenarioSet missing", blocking=True)
        if not isinstance(valuation, GenericValuationResult):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "GenericValuationResult missing", blocking=True)
        if not isinstance(coverage, tuple) or not all(isinstance(item, DoctrineCoverageEntry) for item in coverage):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "doctrine_coverage missing", blocking=True)
        if not isinstance(expected_modules, tuple) or not expected_modules or not all(isinstance(item, str) and item for item in expected_modules):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "expected_module_ids missing", blocking=True)

        audit = audit_generic_intrinsic(
            compiled=compiled,
            scenario_set=scenario_set,
            valuation=valuation,
            doctrine_coverage=coverage,
            expected_module_ids=expected_modules,
            run_context_keys=tuple(context.data),
        )
        blocking = tuple(item.detail for item in audit.report.findings if item.blocking and not item.passed)
        if blocking:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "generic intrinsic audit failed: " + " | ".join(blocking),
                {
                    "generic_audit_report": audit.report,
                    "audit_hash": audit.audit_hash,
                    "audit_passed": False,
                },
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "generic intrinsic audit passed; run is eligible for freeze if snapshot hashes are present",
            {
                "generic_audit_report": audit.report,
                "audit_hash": audit.audit_hash,
                "audit_passed": True,
            },
        )

    return run
