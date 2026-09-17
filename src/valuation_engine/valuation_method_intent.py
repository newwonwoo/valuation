from __future__ import annotations

from dataclasses import dataclass

from .control_plane import StageStatus
from .method_capabilities import (
    MethodCapability,
    MethodCapabilityRegistry,
    MethodKind,
    MethodRuntimeStatus,
)
from .module_plan import ModuleRequirementPlan, SegmentModuleRequirementPlan
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .valuation_plan_compiler import (
    SegmentMethodChoice,
    ValuationPlanStatus,
    valuation_capability_registry_hash,
    valuation_method_choices_hash,
    valuation_module_plan_hash,
)


@dataclass(frozen=True)
class SegmentMethodIntent:
    segment_id: str
    status: ValuationPlanStatus
    selected_archetype: str | None
    selected_method: str | None
    requested_version: str | None
    candidate_bindings: tuple[str, ...]
    rationale: str
    delegated_to_primary_aggregator: bool = False

    @property
    def ready(self) -> bool:
        if self.status is not ValuationPlanStatus.READY:
            return False
        if self.delegated_to_primary_aggregator:
            return self.selected_archetype is None and self.selected_method is None
        return self.selected_archetype is not None and self.selected_method is not None


@dataclass(frozen=True)
class PrimaryAggregatorIntent:
    status: ValuationPlanStatus
    selected_archetype: str | None
    selected_method: str | None
    execution_family: str | None
    covered_segment_ids: tuple[str, ...]
    candidate_bindings: tuple[str, ...]
    rationale: str

    @property
    def ready(self) -> bool:
        return (
            self.status is ValuationPlanStatus.READY
            and self.selected_archetype is not None
            and self.selected_method is not None
            and self.execution_family is not None
            and bool(self.covered_segment_ids)
        )

    @property
    def binding(self) -> str | None:
        if not self.ready:
            return None
        return f"{self.selected_archetype}/{self.selected_method}"


@dataclass(frozen=True)
class ValuationMethodIntent:
    status: ValuationPlanStatus
    segments: tuple[SegmentMethodIntent, ...]
    warranted_per_segments: tuple[str, ...]
    requires_beta: bool
    requires_wacc: bool
    module_plan_hash: str
    capability_registry_hash: str
    primary_aggregator: PrimaryAggregatorIntent | None = None

    @property
    def ready(self) -> bool:
        return self.status is ValuationPlanStatus.READY and all(
            item.ready for item in self.segments
        ) and (
            self.primary_aggregator is None or self.primary_aggregator.ready
        )

    def method_choices(self) -> tuple[SegmentMethodChoice, ...]:
        if not self.ready:
            raise ValueError(
                "unresolved valuation method intent cannot produce exact method choices"
            )
        return tuple(
            SegmentMethodChoice(
                segment_id=item.segment_id,
                archetype=str(item.selected_archetype),
                method=str(item.selected_method),
                version=item.requested_version,
            )
            for item in self.segments
            if not item.delegated_to_primary_aggregator
        )


def _primary_aggregator_candidates(
    plan: ModuleRequirementPlan,
    registry: MethodCapabilityRegistry,
) -> tuple[tuple[MethodCapability, tuple[str, ...]], ...]:
    """Return company-level aggregators that produce a primary intrinsic value.

    SOTP remains the internal combiner for segment evaluators and therefore has
    output_kind=aggregation.  A company-level primary aggregator has a concrete
    intrinsic output such as equity_value_distribution.
    """

    coverage: dict[tuple[str, str], list[str]] = {}
    capabilities: dict[tuple[str, str], MethodCapability] = {}
    for segment in plan.segments:
        for item in _capabilities_for_segment(segment, registry):
            if (
                item.kind is MethodKind.AGGREGATOR
                and item.runtime_status is not MethodRuntimeStatus.NOT_IMPLEMENTED
                and item.output_kind != "aggregation"
            ):
                capabilities[item.identity] = item
                coverage.setdefault(item.identity, []).append(segment.segment_id)
    return tuple(
        (capabilities[identity], tuple(dict.fromkeys(coverage[identity])))
        for identity in sorted(capabilities)
    )


def _resolve_primary_aggregator(
    plan: ModuleRequirementPlan,
    registry: MethodCapabilityRegistry,
) -> PrimaryAggregatorIntent | None:
    candidates = _primary_aggregator_candidates(plan, registry)
    if not candidates:
        return None
    names = tuple(f"{item.archetype}/{item.method}" for item, _ in candidates)
    if len(candidates) > 1:
        return PrimaryAggregatorIntent(
            status=ValuationPlanStatus.METHOD_CHOICE_REQUIRED,
            selected_archetype=None,
            selected_method=None,
            execution_family=None,
            covered_segment_ids=(),
            candidate_bindings=names,
            rationale=(
                "multiple company-level primary aggregators remain; an exact "
                "economic aggregator must be selected before risk stages"
            ),
        )
    selected, covered = candidates[0]
    return PrimaryAggregatorIntent(
        status=ValuationPlanStatus.READY,
        selected_archetype=selected.archetype,
        selected_method=selected.method,
        execution_family=selected.execution_family,
        covered_segment_ids=covered,
        candidate_bindings=names,
        rationale=(
            "one implemented company-level primary aggregator is implied by "
            "the evidence-backed Industry DNA"
        ),
    )


def resolve_valuation_method_intent(
    plan: ModuleRequirementPlan,
    *,
    capability_registry: MethodCapabilityRegistry,
    method_choices: tuple[SegmentMethodChoice, ...] = (),
) -> ValuationMethodIntent:
    """Resolve segment evaluators and any company-level primary aggregator.

    Segment evaluators remain exact ModelKey-bound calculations. A primary
    aggregator is a separate company-level economic route. Segments that have
    no segment evaluator are valid only when the selected primary aggregator
    explicitly covers them; this prevents aggregator-only archetypes from being
    misclassified as capability gaps while preserving fail-closed behavior for
    genuinely unsupported segments.
    """

    plan.validate()
    module_hash = valuation_module_plan_hash(plan)
    capability_hash = valuation_capability_registry_hash(capability_registry)
    expected_segments = tuple(segment.segment_id for segment in plan.segments)
    choices = _choice_map(method_choices, expected_segments)
    primary_aggregator = _resolve_primary_aggregator(plan, capability_registry)
    aggregator_covered = set(
        primary_aggregator.covered_segment_ids
        if primary_aggregator is not None and primary_aggregator.ready
        else ()
    )
    aggregator_binding = (
        primary_aggregator.binding
        if primary_aggregator is not None and primary_aggregator.ready
        else None
    )

    resolutions: list[SegmentMethodIntent] = []
    warranted_per_segments: list[str] = []
    selected_capabilities: list[MethodCapability] = []
    cross_method_capabilities: list[MethodCapability] = []

    for segment in plan.segments:
        capabilities = _capabilities_for_segment(segment, capability_registry)
        per_caps = tuple(
            item
            for item in capabilities
            if item.kind is MethodKind.CROSS_METHOD_ENGINE
            and item.method == "warranted_per"
            and item.runtime_status is not MethodRuntimeStatus.NOT_IMPLEMENTED
        )
        if per_caps:
            warranted_per_segments.append(segment.segment_id)
            cross_method_capabilities.extend(per_caps)

        primary = tuple(
            item
            for item in capabilities
            if item.kind is MethodKind.SEGMENT_EVALUATOR
            and item.runtime_status is not MethodRuntimeStatus.NOT_IMPLEMENTED
        )
        explicit = choices.get(segment.segment_id)
        if explicit is not None:
            matched = tuple(
                item
                for item in primary
                if item.archetype == explicit.archetype
                and item.method == explicit.method
            )
            if len(matched) == 1:
                selected = matched[0]
                selected_capabilities.append(selected)
                resolutions.append(
                    SegmentMethodIntent(
                        segment_id=segment.segment_id,
                        status=ValuationPlanStatus.READY,
                        selected_archetype=selected.archetype,
                        selected_method=selected.method,
                        requested_version=explicit.version,
                        candidate_bindings=_candidate_names(primary),
                        rationale=(
                            "explicit segment evaluator validated against Industry DNA "
                            "and capability role"
                        ),
                    )
                )
                continue
            if (
                segment.segment_id in aggregator_covered
                and aggregator_binding
                == f"{explicit.archetype}/{explicit.method}"
            ):
                resolutions.append(
                    SegmentMethodIntent(
                        segment_id=segment.segment_id,
                        status=ValuationPlanStatus.READY,
                        selected_archetype=None,
                        selected_method=None,
                        requested_version=explicit.version,
                        candidate_bindings=_candidate_names(primary),
                        rationale=(
                            "explicit company-level aggregator covers this segment; "
                            "segment valuation is delegated to the primary aggregator"
                        ),
                        delegated_to_primary_aggregator=True,
                    )
                )
                continue
            resolutions.append(
                SegmentMethodIntent(
                    segment_id=segment.segment_id,
                    status=ValuationPlanStatus.CAPABILITY_GAP,
                    selected_archetype=None,
                    selected_method=None,
                    requested_version=explicit.version,
                    candidate_bindings=_candidate_names(primary),
                    rationale=(
                        f"requested economic method {explicit.archetype}/"
                        f"{explicit.method} is neither an implemented segment evaluator "
                        "nor the selected company-level primary aggregator"
                    ),
                )
            )
            continue

        if len(primary) == 1:
            selected = primary[0]
            selected_capabilities.append(selected)
            resolutions.append(
                SegmentMethodIntent(
                    segment_id=segment.segment_id,
                    status=ValuationPlanStatus.READY,
                    selected_archetype=selected.archetype,
                    selected_method=selected.method,
                    requested_version=None,
                    candidate_bindings=_candidate_names(primary),
                    rationale=(
                        "only one implemented segment-evaluator method remains "
                        "under the selected Industry DNA"
                    ),
                )
            )
        elif len(primary) > 1:
            resolutions.append(
                SegmentMethodIntent(
                    segment_id=segment.segment_id,
                    status=ValuationPlanStatus.METHOD_CHOICE_REQUIRED,
                    selected_archetype=None,
                    selected_method=None,
                    requested_version=None,
                    candidate_bindings=_candidate_names(primary),
                    rationale=(
                        "multiple implemented segment evaluators remain; choose "
                        "the reference/segment method before Beta/WACC"
                    ),
                )
            )
        elif segment.segment_id in aggregator_covered:
            resolutions.append(
                SegmentMethodIntent(
                    segment_id=segment.segment_id,
                    status=ValuationPlanStatus.READY,
                    selected_archetype=None,
                    selected_method=None,
                    requested_version=None,
                    candidate_bindings=(),
                    rationale=(
                        "no segment evaluator is required because the selected "
                        "company-level primary aggregator explicitly covers this segment"
                    ),
                    delegated_to_primary_aggregator=True,
                )
            )
        else:
            resolutions.append(
                SegmentMethodIntent(
                    segment_id=segment.segment_id,
                    status=ValuationPlanStatus.CAPABILITY_GAP,
                    selected_archetype=None,
                    selected_method=None,
                    requested_version=None,
                    candidate_bindings=(),
                    rationale=(
                        "selected Industry DNA has neither an implemented segment "
                        "evaluator nor a covering primary aggregator"
                    ),
                )
            )

    overall = _overall_status(tuple(resolutions), primary_aggregator)
    aggregator_capabilities = tuple(
        capability_registry.get(
            str(primary_aggregator.selected_archetype),
            str(primary_aggregator.selected_method),
        )
        for _ in (0,)
        if primary_aggregator is not None and primary_aggregator.ready
    )
    risk_caps = (
        (*selected_capabilities, *cross_method_capabilities, *aggregator_capabilities)
        if overall is ValuationPlanStatus.READY
        else ()
    )
    return ValuationMethodIntent(
        status=overall,
        segments=tuple(resolutions),
        warranted_per_segments=tuple(dict.fromkeys(warranted_per_segments)),
        requires_beta=any(item.requires_beta for item in risk_caps),
        requires_wacc=any(item.requires_wacc for item in risk_caps),
        module_plan_hash=module_hash,
        capability_registry_hash=capability_hash,
        primary_aggregator=primary_aggregator,
    )


def valuation_method_intent_adapter(
    *,
    capability_registry: MethodCapabilityRegistry,
    method_choices: tuple[SegmentMethodChoice, ...] = (),
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        plan = context.data.get("module_requirement_plan")
        if not isinstance(plan, ModuleRequirementPlan):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "ModuleRequirementPlan is required before valuation-method "
                "intent resolution",
                blocking=True,
            )
        auto_resolved = False
        try:
            intent = resolve_valuation_method_intent(
                plan,
                capability_registry=capability_registry,
                method_choices=method_choices,
            )
            if (
                not method_choices
                and intent.status is ValuationPlanStatus.METHOD_CHOICE_REQUIRED
                and intent.primary_aggregator is None
            ):
                from .auto_method_routing import (
                    AUTO_METHOD_ROUTING_FLAG,
                    AUTO_METHOD_ROUTING_FORECAST_YEARS,
                    auto_feasible_method_choices,
                )
                from .scenario_binding import BoundScenarioSet

                enabled = context.data.get(AUTO_METHOD_ROUTING_FLAG, False)
                if enabled not in (False, True):
                    raise TypeError(f"{AUTO_METHOD_ROUTING_FLAG} must be bool")
                if enabled:
                    forecast_years = context.data.get(
                        AUTO_METHOD_ROUTING_FORECAST_YEARS
                    )
                    scenarios = context.data.get("bound_scenario_set")
                    if not isinstance(forecast_years, int) or isinstance(
                        forecast_years, bool
                    ):
                        raise TypeError(
                            f"{AUTO_METHOD_ROUTING_FORECAST_YEARS} must be an integer"
                        )
                    if not isinstance(scenarios, BoundScenarioSet):
                        raise TypeError(
                            "BoundScenarioSet is required for evidence-feasibility method resolution"
                        )
                    feasible_choices = auto_feasible_method_choices(
                        plan,
                        scenarios,
                        forecast_years=forecast_years,
                        capability_registry=capability_registry,
                    )
                    if feasible_choices:
                        intent = resolve_valuation_method_intent(
                            plan,
                            capability_registry=capability_registry,
                            method_choices=feasible_choices,
                        )
                        auto_resolved = intent.ready
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "valuation-method intent resolution failed: "
                f"{type(exc).__name__}: {exc}",
                blocking=True,
            )
        common_outputs = {
            "valuation_method_intent": intent,
            "valuation_module_plan_hash": intent.module_plan_hash,
            "valuation_capability_registry_hash": (
                intent.capability_registry_hash
            ),
        }
        if intent.primary_aggregator is not None:
            common_outputs["primary_aggregator_intent"] = intent.primary_aggregator
            if intent.primary_aggregator.binding is not None:
                common_outputs["primary_aggregator_binding"] = (
                    intent.primary_aggregator.binding
                )
                common_outputs["primary_aggregator_covered_segments"] = (
                    intent.primary_aggregator.covered_segment_ids
                )
        if not intent.ready:
            status = (
                StageStatus.NOT_IMPLEMENTED
                if intent.status is ValuationPlanStatus.CAPABILITY_GAP
                else StageStatus.AWAITING_USER_DECISION
            )
            return StageExecutionResult(
                status,
                "valuation-method intent unresolved before risk stages: "
                f"{intent.status.value}",
                common_outputs,
                blocking=True,
            )
        planned_choices = intent.method_choices()
        rationale = (
            "economic valuation-method intent resolved by deterministic "
            "evidence-feasibility filtering inside the canonical method-intent stage; "
            "exact evaluator construction remains downstream"
            if auto_resolved
            else (
                "company-level primary aggregator and any segment reference methods "
                "resolved before Beta/WACC"
                if intent.primary_aggregator is not None
                else "economic valuation-method intent resolved before Beta/WACC; "
                "exact evaluator construction remains downstream"
            )
        )
        return StageExecutionResult(
            StageStatus.PASS,
            rationale,
            {
                **common_outputs,
                "planned_method_choices": planned_choices,
                "valuation_method_choices_hash": (
                    valuation_method_choices_hash(planned_choices)
                ),
                "warranted_per_segments": intent.warranted_per_segments,
                "risk_chain_requires_beta": intent.requires_beta,
                "risk_chain_requires_wacc": intent.requires_wacc,
            },
        )

    return run


def _capabilities_for_segment(
    segment: SegmentModuleRequirementPlan,
    registry: MethodCapabilityRegistry,
) -> tuple[MethodCapability, ...]:
    allowed = set(segment.allowed_valuation_methods)
    archetypes = set(segment.archetypes)
    return tuple(
        item
        for item in registry.capabilities
        if item.archetype in archetypes and item.method in allowed
    )


def _choice_map(
    choices: tuple[SegmentMethodChoice, ...],
    expected_segments: tuple[str, ...],
) -> dict[str, SegmentMethodChoice]:
    result: dict[str, SegmentMethodChoice] = {}
    allowed = set(expected_segments)
    for choice in choices:
        choice.validate()
        if choice.segment_id not in allowed:
            raise ValueError(
                f"method choice references unknown segment {choice.segment_id}"
            )
        if choice.segment_id in result:
            raise ValueError(
                f"duplicate method choice for segment {choice.segment_id}"
            )
        result[choice.segment_id] = choice
    return result


def _candidate_names(
    capabilities: tuple[MethodCapability, ...],
) -> tuple[str, ...]:
    return tuple(
        f"{item.archetype}/{item.method}" for item in capabilities
    )


def _overall_status(
    resolutions: tuple[SegmentMethodIntent, ...],
    primary_aggregator: PrimaryAggregatorIntent | None,
) -> ValuationPlanStatus:
    if primary_aggregator is not None and not primary_aggregator.ready:
        return primary_aggregator.status
    if any(
        item.status is ValuationPlanStatus.CAPABILITY_GAP
        for item in resolutions
    ):
        return ValuationPlanStatus.CAPABILITY_GAP
    if any(
        item.status is ValuationPlanStatus.METHOD_CHOICE_REQUIRED
        for item in resolutions
    ):
        return ValuationPlanStatus.METHOD_CHOICE_REQUIRED
    if not resolutions or any(
        item.status is not ValuationPlanStatus.READY
        for item in resolutions
    ):
        return ValuationPlanStatus.CAPABILITY_GAP
    return ValuationPlanStatus.READY


__all__ = [
    "PrimaryAggregatorIntent",
    "SegmentMethodIntent",
    "ValuationMethodIntent",
    "resolve_valuation_method_intent",
    "valuation_method_intent_adapter",
]
