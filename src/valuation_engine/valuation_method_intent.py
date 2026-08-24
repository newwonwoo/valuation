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

    @property
    def ready(self) -> bool:
        return (
            self.status is ValuationPlanStatus.READY
            and self.selected_archetype is not None
            and self.selected_method is not None
        )


@dataclass(frozen=True)
class ValuationMethodIntent:
    status: ValuationPlanStatus
    segments: tuple[SegmentMethodIntent, ...]
    warranted_per_segments: tuple[str, ...]
    requires_beta: bool
    requires_wacc: bool
    module_plan_hash: str
    capability_registry_hash: str

    @property
    def ready(self) -> bool:
        return self.status is ValuationPlanStatus.READY and all(
            item.ready for item in self.segments
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
        )


def resolve_valuation_method_intent(
    plan: ModuleRequirementPlan,
    *,
    capability_registry: MethodCapabilityRegistry,
    method_choices: tuple[SegmentMethodChoice, ...] = (),
) -> ValuationMethodIntent:
    """Resolve economic method identity before Beta/WACC.

    Exact evaluator version and assumption readiness remain deterministic stage-19
    responsibilities, but both stages carry the same Module Plan and capability identities.
    """
    plan.validate()
    module_hash = valuation_module_plan_hash(plan)
    capability_hash = valuation_capability_registry_hash(capability_registry)
    expected_segments = tuple(segment.segment_id for segment in plan.segments)
    choices = _choice_map(method_choices, expected_segments)
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
            if len(matched) != 1:
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
                            f"{explicit.method} is not an implemented allowed "
                            "segment evaluator"
                        ),
                    )
                )
                continue
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
                        "explicit economic method intent validated against "
                        "Industry DNA and capability role"
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
                        "multiple implemented economic methods remain; choose "
                        "the primary method before Beta/WACC"
                    ),
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
                        "selected Industry DNA has no implemented "
                        "segment-evaluator method capability"
                    ),
                )
            )

    overall = _overall_status(tuple(resolutions))
    risk_caps = (
        (*selected_capabilities, *cross_method_capabilities)
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
        try:
            intent = resolve_valuation_method_intent(
                plan,
                capability_registry=capability_registry,
                method_choices=method_choices,
            )
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
        return StageExecutionResult(
            StageStatus.PASS,
            "economic valuation-method intent resolved before Beta/WACC; "
            "exact evaluator construction remains downstream",
            {
                **common_outputs,
                "planned_method_choices": intent.method_choices(),
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
) -> ValuationPlanStatus:
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
