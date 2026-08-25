from __future__ import annotations

from hashlib import sha256
from typing import Callable

from .control_plane import StageStatus
from .evaluator_registry import EvaluatorRegistry
from .module_plan import ModuleRequirementPlan
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .partial_valuation import partial_plan_executable
from .scenario_binding import BoundScenarioSet
from .valuation_execution import (
    CompanyValuationPlan,
    IntrinsicValuationScope,
    execute_company_valuation,
)
from .valuation_plan_compiler import (
    SegmentMethodChoice,
    ValuationPlanCompilation,
    ValuationPlanStatus,
    valuation_evaluator_registry_hash,
    valuation_method_choices_hash,
    valuation_module_plan_hash,
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
        [
            plan.reporting_unit,
            plan.diluted_shares_key,
            f"scope={plan.scope.value}",
        ]
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
            (
                f"UNVALUED|{item.asset_id}|{item.segment_id}|{item.status.value}|"
                f"{item.resolution_status}|{item.rationale}|"
                f"{','.join(item.missing_assumptions)}"
            )
            for item in plan.unvalued_segments
        ]
        + [
            f"PARENT|{item.asset_id}|{item.assumption_key}"
            for item in plan.parent_adjustments
        ]
    )
    return selected_methods, sha256(serialized.encode("utf-8")).hexdigest()


def deterministic_valuation_adapter(
    *,
    plan: CompanyValuationPlan | None = None,
    plan_loader: ValuationPlanLoader | None = None,
    registry: EvaluatorRegistry | None = None,
    registry_loader: RegistryLoader | None = None,
) -> StageAdapter:
    if (registry is None) == (registry_loader is None):
        raise ValueError("supply exactly one of registry or registry_loader")
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
                raise TypeError("registry_loader must return EvaluatorRegistry")
            current_evaluator_registry_hash = (
                valuation_evaluator_registry_hash(effective_registry)
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
            current_module_plan = context.data.get("module_requirement_plan")
            current_intent_module_hash = context.data.get(
                "valuation_module_plan_hash"
            )
            current_capability_hash = context.data.get(
                "valuation_capability_registry_hash"
            )
            current_method_choices = context.data.get(
                "planned_method_choices"
            )
            current_intent_method_choices_hash = context.data.get(
                "valuation_method_choices_hash"
            )
            if not isinstance(current_module_plan, ModuleRequirementPlan):
                return StageExecutionResult(
                    StageStatus.RECOVERY_REQUIRED,
                    "current ModuleRequirementPlan is required before dynamic "
                    "valuation-plan loading",
                    blocking=True,
                )
            if (
                not isinstance(current_intent_module_hash, str)
                or not current_intent_module_hash
                or not isinstance(current_capability_hash, str)
                or not current_capability_hash
                or not isinstance(current_method_choices, tuple)
                or not all(
                    isinstance(item, SegmentMethodChoice)
                    for item in current_method_choices
                )
                or not isinstance(current_intent_method_choices_hash, str)
                or not current_intent_method_choices_hash
            ):
                return StageExecutionResult(
                    StageStatus.RECOVERY_REQUIRED,
                    "current pre-risk valuation method identities are required "
                    "before dynamic valuation-plan loading",
                    blocking=True,
                )
            try:
                current_module_hash = valuation_module_plan_hash(
                    current_module_plan
                )
                current_method_choices_hash = valuation_method_choices_hash(
                    current_method_choices
                )
            except (TypeError, ValueError) as exc:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    f"current valuation module-plan identity failed: {exc}",
                    blocking=True,
                )
            if current_intent_module_hash != current_module_hash:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "pre-risk valuation method intent is stale relative to the "
                    "current ModuleRequirementPlan",
                    {
                        "current_module_plan_hash": current_module_hash,
                        "intent_module_plan_hash": current_intent_module_hash,
                    },
                    blocking=True,
                )
            if (
                current_intent_method_choices_hash
                != current_method_choices_hash
            ):
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "pre-risk valuation method-choice identity is stale "
                    "relative to the current planned choices",
                    {
                        "current_method_choices_hash": (
                            current_method_choices_hash
                        ),
                        "intent_method_choices_hash": (
                            current_intent_method_choices_hash
                        ),
                    },
                    blocking=True,
                )

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

            identity_outputs = {
                "valuation_plan_compilation": compilation,
                "current_scenario_set_hash": scenario_set.scenario_set_hash,
                "current_module_plan_hash": current_module_hash,
                "current_capability_registry_hash": current_capability_hash,
                "current_evaluator_registry_hash": (
                    current_evaluator_registry_hash
                ),
                "current_method_choices_hash": current_method_choices_hash,
            }
            if compilation.scenario_set_hash != scenario_set.scenario_set_hash:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "valuation plan compilation scenario-set hash does not "
                    "match the current BoundScenarioSet",
                    identity_outputs,
                    blocking=True,
                )
            if compilation.module_plan_hash != current_module_hash:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "valuation plan compilation module-plan hash does not "
                    "match the current ModuleRequirementPlan",
                    identity_outputs,
                    blocking=True,
                )
            if compilation.capability_registry_hash != current_capability_hash:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "valuation plan compilation capability-registry hash does "
                    "not match the current pre-risk capability contract",
                    identity_outputs,
                    blocking=True,
                )
            if (
                compilation.evaluator_registry_hash
                != current_evaluator_registry_hash
            ):
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "valuation plan compilation evaluator-registry hash does "
                    "not match the current exact evaluator contract",
                    identity_outputs,
                    blocking=True,
                )
            if compilation.method_choices_hash != current_method_choices_hash:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "valuation plan compilation method-choice hash does not "
                    "match the current pre-risk method intent",
                    identity_outputs,
                    blocking=True,
                )
            current_warranted_per_segments = context.data.get(
                "warranted_per_segments"
            )
            if current_warranted_per_segments is not None:
                if not isinstance(current_warranted_per_segments, tuple) or not all(
                    isinstance(item, str) and item
                    for item in current_warranted_per_segments
                ):
                    return StageExecutionResult(
                        StageStatus.BLOCKED,
                        "pre-risk warranted-PER segment intent is invalid",
                        identity_outputs,
                        blocking=True,
                    )
                if (
                    compilation.warranted_per_segments
                    != current_warranted_per_segments
                ):
                    return StageExecutionResult(
                        StageStatus.BLOCKED,
                        "pre-risk Warranted PER routing drifted from the "
                        "compiled valuation plan",
                        {
                            **identity_outputs,
                            "pre_risk_warranted_per_segments": (
                                current_warranted_per_segments
                            ),
                            "valuation_plan_warranted_per_segments": (
                                compilation.warranted_per_segments
                            ),
                        },
                        blocking=True,
                    )
            if not compilation.ready and not partial_plan_executable(compilation):
                status = (
                    StageStatus.NOT_IMPLEMENTED
                    if compilation.status is ValuationPlanStatus.CAPABILITY_GAP
                    else StageStatus.RECOVERY_REQUIRED
                )
                return StageExecutionResult(
                    status,
                    "valuation plan compilation did not resolve: "
                    f"{compilation.status.value}",
                    {"valuation_plan_compilation": compilation},
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
            "valuation_scope": result.scope,
            "unvalued_segments": result.unvalued_segments,
            "full_company_intrinsic_available": (
                result.full_company_intrinsic_available
            ),
        }
        if compilation is not None:
            outputs["valuation_plan_compilation"] = compilation
            outputs["valuation_plan_scenario_set_hash"] = (
                compilation.scenario_set_hash
            )
            outputs["valuation_plan_module_plan_hash"] = (
                compilation.module_plan_hash
            )
            outputs["valuation_plan_capability_registry_hash"] = (
                compilation.capability_registry_hash
            )
            outputs["valuation_plan_warranted_per_segments"] = (
                compilation.warranted_per_segments
            )
            outputs["valuation_plan_evaluator_registry_hash"] = (
                compilation.evaluator_registry_hash
            )
            outputs["valuation_plan_method_choices_hash"] = (
                compilation.method_choices_hash
            )
            if "warranted_per_segments" not in context.data:
                outputs["warranted_per_segments"] = (
                    compilation.warranted_per_segments
                )
            outputs["valuation_aggregator_bindings"] = (
                compilation.aggregator_bindings
            )
        partial = result.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC
        return StageExecutionResult(
            StageStatus.WARNING if partial else StageStatus.PASS,
            (
                "PARTIAL_INTRINSIC completed for valued segments; unresolved segments are preserved as UNVALUED_NOT_ZERO"
                if partial
                else "registered deterministic evaluators and SOTP aggregation completed"
            ),
            outputs,
        )

    return run