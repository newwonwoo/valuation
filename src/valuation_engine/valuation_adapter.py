from __future__ import annotations

from hashlib import sha256

from .control_plane import StageStatus
from .evaluator_registry import EvaluatorRegistry, ValuationRuntimeInputs
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .scenario_binding import BoundScenarioSet
from .valuation_execution import CompanyValuationPlan, execute_company_valuation


def _plan_identity(plan: CompanyValuationPlan) -> tuple[tuple[str, ...], str]:
    selected_methods = tuple(
        f"{item.model_key.archetype}/{item.model_key.method}/{item.model_key.version}"
        for item in plan.segments
    )
    serialized = "\n".join(
        [plan.reporting_unit, plan.diluted_shares_key]
        + [
            f"{item.asset_id}|{item.segment_id}|{item.model_key.archetype}|{item.model_key.method}|{item.model_key.version}|{item.ownership_key}|{item.ev_to_equity_adjustment_key or ''}"
            for item in plan.segments
        ]
        + [f"PARENT|{item.asset_id}|{item.assumption_key}" for item in plan.parent_adjustments]
    )
    return selected_methods, sha256(serialized.encode("utf-8")).hexdigest()


def deterministic_valuation_adapter(
    *,
    registry: EvaluatorRegistry,
    plan: CompanyValuationPlan,
) -> StageAdapter:
    selected_methods, route_hash = _plan_identity(plan)

    def run(context: OrchestratorContext) -> StageExecutionResult:
        scenario_set = context.data.get("bound_scenario_set")
        if not isinstance(scenario_set, BoundScenarioSet):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "BoundScenarioSet is missing; SCENARIO_BUILD must complete before valuation",
                blocking=True,
            )
        runtime_inputs = context.data.get("valuation_runtime_inputs", ValuationRuntimeInputs())
        if not isinstance(runtime_inputs, ValuationRuntimeInputs):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "valuation_runtime_inputs must be a typed ValuationRuntimeInputs object",
                blocking=True,
            )
        try:
            result = execute_company_valuation(
                scenario_set,
                plan=plan,
                registry=registry,
                runtime_inputs=runtime_inputs,
            )
        except KeyError as exc:
            return StageExecutionResult(
                StageStatus.NOT_IMPLEMENTED,
                f"exact evaluator or compiled/runtime input is unavailable: {exc}",
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
            "registered deterministic evaluators and SOTP aggregation completed; declared runtime risk inputs were consumed and traced where required",
            {
                "generic_valuation_result": result,
                "valuation_hash": result.valuation_hash,
                "intrinsic_scenario_values": result.scenarios,
                "expected_value_per_share": result.expected_value_per_share,
                "selected_methods": selected_methods,
                "route_hash": route_hash,
                "valuation_runtime_input_keys": tuple(item.key for item in runtime_inputs.inputs),
            },
        )

    return run
