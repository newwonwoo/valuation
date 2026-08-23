from __future__ import annotations

from .control_plane import StageStatus
from .evaluator_registry import EvaluatorRegistry
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .scenario_binding import BoundScenarioSet
from .valuation_execution import CompanyValuationPlan, execute_company_valuation


def deterministic_valuation_adapter(
    *,
    registry: EvaluatorRegistry,
    plan: CompanyValuationPlan,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        scenario_set = context.data.get("bound_scenario_set")
        if not isinstance(scenario_set, BoundScenarioSet):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "BoundScenarioSet is missing; SCENARIO_BUILD must complete before valuation",
                blocking=True,
            )
        try:
            result = execute_company_valuation(scenario_set, plan=plan, registry=registry)
        except KeyError as exc:
            return StageExecutionResult(
                StageStatus.NOT_IMPLEMENTED,
                f"exact evaluator or compiled assumption is unavailable: {exc}",
                blocking=True,
            )
        except ValueError as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"deterministic valuation validation failed: {exc}",
                blocking=True,
            )

        return StageExecutionResult(
            StageStatus.PASS,
            "registered deterministic evaluators and SOTP aggregation completed",
            {
                "generic_valuation_result": result,
                "valuation_hash": result.valuation_hash,
                "intrinsic_scenario_values": result.scenarios,
                "expected_value_per_share": result.expected_value_per_share,
            },
        )

    return run
