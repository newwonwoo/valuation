from __future__ import annotations

from hashlib import sha256
from typing import Callable

from .control_plane import StageStatus
from .evaluator_registry import EvaluatorRegistry
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .scenario_binding import BoundScenarioSet
from .valuation_execution import CompanyValuationPlan, execute_company_valuation
from .valuation_plan_compiler import (
    ValuationPlanCompilation,
    ValuationPlanStatus,
)


RegistryLoader = Callable[[OrchestratorContext], EvaluatorRegistry]
ValuationPlanLoader = Callable[
    [OrchestratorContext, EvaluatorRegistry],
    ValuationPlanCompilation,
]


def _plan_identity(
    plan: CompanyValuationPlan,
) -> tuple[tuple[str, ...], str]:
    selected_methods = tuple(
        f"{item.model_key.archetype}/{item.model_key.method}/{item.model_key.version}"
        for item in plan.segments
    )
    serialized = "\n".join(
        [plan.reporting_unit, plan.diluted_shares_key]
        + [
            (
                f"{item.asset_id}|{item.segment_id}|"
                f"{item.model_key.archetype}|{item.model_key.method}|"
                f"{item.model_key.version}|{item.ownership_key}|"
                f"{item.ev_to_equity_adjustment_key or ''}"
            )
            for item in plan.segments
        ]
        + [
            f"PARENT|{item.asset_id}|{item.assumption_key}"
            for item in plan.parent_adjustments
        ]
    )
    return selected_methods, sha256(
        serialized.encode("utf-8")
    ).hexdigest()


def deterministic_valuation_adapter(
    *,
    plan: CompanyValuationPlan | None = None,
    plan_loader: ValuationPlanLoader | None = None,
    registry: EvaluatorRegistry | None = None,
    registry_loader: RegistryLoader | None = None,
) -> StageAdapter:
    if (registry is None) == (registry_loader is None):
        raise ValueError(
            "supply exactly one of registry or registry_loader"
        )
    if (plan is None) == (plan_loader is None):
        raise ValueError("supply exactly one of plan or plan_loader")

    def run(context: OrchestratorContext) -> StageExecutionResult:
        scenario_set = context.data.get("bound_scenario_set")
        if not isinstance(scenario_set, BoundScenarioSet):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "BoundScenarioSet is missing; SCENARIO_BUILD must complete "
                "before valuation",
                blocking=True,
            )

        try:
            effective_registry = (
                registry if registry is not None else registry_loader(context)
            )
            if not isinstance(effective_registry, EvaluatorRegistry):
                raise TypeError(
                    "registry_loader must return EvaluatorRegistry"
                )
        except KeyError as exc:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                f"valuation registry loader is missing upstream context: {exc}",
                blocking=True,
            )
        except (ValueError, TypeError, PermissionError) as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"valuation registry loading failed: {exc}",
                blocking=True,
            )

        compilation: ValuationPlanCompilation | None = None
        effective_plan = plan
        if plan_loader is not None:
            try:
                compilation = plan_loader(context, effective_registry)
                if not isinstance(compilation, ValuationPlanCompilation):
                    raise TypeError(
                        "plan_loader must return ValuationPlanCompilation"
                    )
            except KeyError as exc:
                return StageExecutionResult(
                    StageStatus.RECOVERY_REQUIRED,
                    f"valuation plan loader is missing upstream context: {exc}",
                    blocking=True,
                )
            except (ValueError, TypeError, PermissionError) as exc:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    f"valuation plan loading failed: {exc}",
                    blocking=True,
                )

            if (
                compilation.scenario_set_hash
                != scenario_set.scenario_set_hash
            ):
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "valuation plan compilation scenario-set hash does not "
                    "match the current BoundScenarioSet",
                    {
                        "valuation_plan_compilation": compilation,
                        "current_scenario_set_hash": (
                            scenario_set.scenario_set_hash
                        ),
                    },
                    blocking=True,
                )
            if not compilation.ready:
                status = (
                    StageStatus.NOT_IMPLEMENTED
                    if compilation.status
                    is ValuationPlanStatus.CAPABILITY_GAP
                    else StageStatus.RECOVERY_REQUIRED
                )
                return StageExecutionResult(
                    status,
                    "valuation plan compilation did not resolve: "
                    f"{compilation.status.value}",
                    {"valuation_plan_compilation": compilation},
                    blocking=True,
                )

            pre_risk_per_segments = context.data.get("warranted_per_segments")
            if pre_risk_per_segments is not None:
                if not isinstance(pre_risk_per_segments, tuple) or not all(
                    isinstance(item, str) and item
                    for item in pre_risk_per_segments
                ):
                    return StageExecutionResult(
                        StageStatus.BLOCKED,
                        "pre-risk warranted_per_segments must be a tuple of non-empty strings",
                        {"valuation_plan_compilation": compilation},
                        blocking=True,
                    )
                if pre_risk_per_segments != compilation.warranted_per_segments:
                    return StageExecutionResult(
                        StageStatus.BLOCKED,
                        (
                            "pre-risk Warranted PER routing drifted from the compiled "
                            "valuation plan"
                        ),
                        {
                            "valuation_plan_compilation": compilation,
                            "pre_risk_warranted_per_segments": pre_risk_per_segments,
                            "valuation_plan_warranted_per_segments": (
                                compilation.warranted_per_segments
                            ),
                        },
                        blocking=True,
                    )
            effective_plan = compilation.plan

        if not isinstance(effective_plan, CompanyValuationPlan):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "resolved valuation plan must be CompanyValuationPlan",
                (
                    {"valuation_plan_compilation": compilation}
                    if compilation is not None
                    else {}
                ),
                blocking=True,
            )

        try:
            selected_methods, route_hash = _plan_identity(effective_plan)
            result = execute_company_valuation(
                scenario_set,
                plan=effective_plan,
                registry=effective_registry,
            )
        except KeyError as exc:
            return StageExecutionResult(
                StageStatus.NOT_IMPLEMENTED,
                "exact evaluator is unavailable during valuation execution: "
                f"{exc}",
                (
                    {"valuation_plan_compilation": compilation}
                    if compilation is not None
                    else {}
                ),
                blocking=True,
            )
        except (ValueError, TypeError, PermissionError) as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"deterministic valuation validation failed: {exc}",
                (
                    {"valuation_plan_compilation": compilation}
                    if compilation is not None
                    else {}
                ),
                blocking=True,
            )

        outputs = {
            "generic_valuation_result": result,
            "valuation_hash": result.valuation_hash,
            "intrinsic_scenario_values": result.scenarios,
            "expected_value_per_share": result.expected_value_per_share,
            "selected_methods": selected_methods,
            "route_hash": route_hash,
        }
        if compilation is not None:
            outputs["valuation_plan_compilation"] = compilation
            outputs["valuation_plan_scenario_set_hash"] = (
                compilation.scenario_set_hash
            )
            outputs["valuation_plan_warranted_per_segments"] = (
                compilation.warranted_per_segments
            )
            if "warranted_per_segments" not in context.data:
                outputs["warranted_per_segments"] = (
                    compilation.warranted_per_segments
                )
            outputs["valuation_aggregator_bindings"] = (
                compilation.aggregator_bindings
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "registered deterministic evaluators and SOTP aggregation completed",
            outputs,
        )

    return run
