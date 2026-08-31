from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from hashlib import sha256
import json

from .evaluator_registry import EvaluatorRegistry, ModelKey
from .method_capabilities import (
    MethodCapability,
    MethodCapabilityRegistry,
    MethodKind,
    MethodRuntimeStatus,
)
from .module_plan import ModuleRequirementPlan, SegmentModuleRequirementPlan
from .scenario_binding import BoundScenarioSet
from .valuation_execution import (
    CompanyValuationPlan,
    ParentAdjustmentPlan,
    SegmentValuationPlan,
)


class ValuationPlanStatus(str, Enum):
    READY = "READY"
    METHOD_CHOICE_REQUIRED = "METHOD_CHOICE_REQUIRED"
    ASSUMPTION_GAP = "ASSUMPTION_GAP"
    CAPABILITY_GAP = "CAPABILITY_GAP"


def valuation_module_plan_hash(plan: ModuleRequirementPlan) -> str:
    """Hash the complete canonical Module Requirement Plan consumed by valuation."""
    if not isinstance(plan, ModuleRequirementPlan):
        raise TypeError("valuation module-plan identity requires ModuleRequirementPlan")
    plan.validate()
    return _stable_contract_hash(
        {
            "contract": "valuation_module_plan/v1",
            "plan": asdict(plan),
        }
    )


def valuation_capability_registry_hash(
    registry: MethodCapabilityRegistry,
) -> str:
    """Hash exact method roles/statuses used by intent and final plan compilation."""
    if not isinstance(registry, MethodCapabilityRegistry):
        raise TypeError(
            "valuation capability identity requires MethodCapabilityRegistry"
        )
    if not registry.families or not registry.capabilities:
        raise ValueError("valuation capability registry cannot be empty")

    family_names = tuple(item.family for item in registry.families)
    if len(family_names) != len(set(family_names)):
        raise ValueError("valuation capability registry has duplicate families")
    identity_pairs = tuple(item.identity for item in registry.capabilities)
    if len(identity_pairs) != len(set(identity_pairs)):
        raise ValueError(
            "valuation capability registry has duplicate archetype/method bindings"
        )
    for family in registry.families:
        family.validate()
    for capability in registry.capabilities:
        capability.validate()
        family = registry.family(capability.execution_family)
        if (
            capability.kind is not family.kind
            or capability.runtime_status is not family.runtime_status
            or capability.requires_beta != family.requires_beta
            or capability.requires_wacc != family.requires_wacc
            or capability.stage != family.stage
            or capability.canonical_refs != family.canonical_refs
        ):
            raise ValueError(
                f"method capability {capability.identity!r} drifted from "
                f"execution family {family.family}"
            )

    return _stable_contract_hash(
        {
            "contract": "valuation_method_capabilities/v1",
            "families": [
                asdict(item)
                for item in sorted(
                    registry.families,
                    key=lambda item: item.family,
                )
            ],
            "capabilities": [
                asdict(item)
                for item in sorted(
                    registry.capabilities,
                    key=lambda item: item.identity,
                )
            ],
        }
    )


def valuation_evaluator_registry_hash(
    registry: EvaluatorRegistry,
) -> str:
    """Hash exact evaluator keys and declared assumption-input contracts."""
    if not isinstance(registry, EvaluatorRegistry):
        raise TypeError(
            "valuation evaluator identity requires EvaluatorRegistry"
        )
    if not registry.has_scoped_registrations():
        rows: list[dict[str, object]] = []
        for key in registry.keys():
            evaluator = registry.get(key)
            required = tuple(evaluator.required_assumption_keys)
            if not required or not all(
                isinstance(item, str) and item for item in required
            ):
                raise ValueError(
                    f"evaluator {key!r} has an invalid required-assumption contract"
                )
            if len(required) != len(set(required)):
                raise ValueError(
                    f"evaluator {key!r} declares duplicate required assumptions"
                )
            rows.append(
                {
                    "archetype": key.archetype,
                    "method": key.method,
                    "version": key.version,
                    "required_assumption_keys": required,
                }
            )
        return _stable_contract_hash(
            {
                "contract": "valuation_evaluator_registry/v1",
                "evaluators": rows,
            }
        )

    scoped_rows: list[dict[str, object]] = []
    for segment_id, key, evaluator in registry.registration_items():
        required = tuple(evaluator.required_assumption_keys)
        if not required or not all(
            isinstance(item, str) and item for item in required
        ):
            raise ValueError(
                f"evaluator {key!r} has an invalid required-assumption contract"
            )
        if len(required) != len(set(required)):
            raise ValueError(
                f"evaluator {key!r} declares duplicate required assumptions"
            )
        scoped_rows.append(
            {
                "segment_id": segment_id,
                "archetype": key.archetype,
                "method": key.method,
                "version": key.version,
                "required_assumption_keys": required,
            }
        )
    return _stable_contract_hash(
        {
            "contract": "valuation_evaluator_registry/v2",
            "evaluators": scoped_rows,
        }
    )


@dataclass(frozen=True)
class SegmentValueBinding:
    segment_id: str
    asset_id: str
    ownership_key: str
    ev_to_equity_adjustment_key: str | None

    def validate(self) -> None:
        if not self.segment_id or not self.asset_id or not self.ownership_key:
            raise ValueError(
                "segment value binding requires segment, asset and ownership key"
            )


@dataclass(frozen=True)
class CompanyValuationPlanInputs:
    reporting_unit: str
    diluted_shares_key: str
    segment_bindings: tuple[SegmentValueBinding, ...]
    parent_adjustments: tuple[ParentAdjustmentPlan, ...] = ()

    def validate(self, *, expected_segment_ids: tuple[str, ...]) -> None:
        if (
            not self.reporting_unit
            or not self.diluted_shares_key
            or not self.segment_bindings
        ):
            raise ValueError(
                "valuation plan inputs require reporting unit, diluted shares "
                "and segment bindings"
            )
        for item in self.segment_bindings:
            item.validate()
        ids = tuple(item.segment_id for item in self.segment_bindings)
        if len(ids) != len(set(ids)):
            raise ValueError(
                "valuation plan inputs contain duplicate segment bindings"
            )
        if set(ids) != set(expected_segment_ids):
            raise ValueError(
                "valuation plan binding coverage mismatch: "
                f"expected={sorted(expected_segment_ids)}, got={sorted(ids)}"
            )

        asset_ids = tuple(
            item.asset_id for item in self.segment_bindings
        ) + tuple(item.asset_id for item in self.parent_adjustments)
        duplicate_assets = _duplicates(asset_ids)
        if duplicate_assets:
            raise ValueError(
                "valuation plan inputs reuse asset IDs: "
                + ", ".join(duplicate_assets)
            )

        adjustment_keys = tuple(
            item.ev_to_equity_adjustment_key
            for item in self.segment_bindings
            if item.ev_to_equity_adjustment_key is not None
        ) + tuple(item.assumption_key for item in self.parent_adjustments)
        duplicate_adjustments = _duplicates(adjustment_keys)
        if duplicate_adjustments:
            raise ValueError(
                "valuation plan inputs reuse EV-to-equity/parent adjustment "
                "assumption keys: "
                + ", ".join(duplicate_adjustments)
            )

    def binding_for(self, segment_id: str) -> SegmentValueBinding:
        for item in self.segment_bindings:
            if item.segment_id == segment_id:
                return item
        raise KeyError(segment_id)


@dataclass(frozen=True)
class SegmentMethodChoice:
    segment_id: str
    archetype: str
    method: str
    version: str | None = None

    def validate(self) -> None:
        if not self.segment_id or not self.archetype or not self.method:
            raise ValueError(
                "segment method choice requires segment, archetype and method"
            )
        if self.version is not None and not self.version:
            raise ValueError("segment method choice version cannot be blank")


def valuation_method_choices_hash(
    choices: tuple[SegmentMethodChoice, ...],
) -> str:
    """Hash the exact pre-risk segment method/version decisions."""
    if not isinstance(choices, tuple) or not all(
        isinstance(item, SegmentMethodChoice) for item in choices
    ):
        raise TypeError(
            "valuation method-choice identity requires a SegmentMethodChoice tuple"
        )
    for item in choices:
        item.validate()
    segment_ids = tuple(item.segment_id for item in choices)
    if len(segment_ids) != len(set(segment_ids)):
        raise ValueError("valuation method choices contain duplicate segments")
    return _stable_contract_hash(
        {
            "contract": "valuation_method_choices/v1",
            "choices": [
                asdict(item)
                for item in sorted(
                    choices,
                    key=lambda item: item.segment_id,
                )
            ],
        }
    )


@dataclass(frozen=True)
class SegmentMethodCandidate:
    archetype: str
    method: str
    runtime_status: MethodRuntimeStatus
    output_kind: str
    registered_model_keys: tuple[ModelKey, ...]
    assumption_ready_model_keys: tuple[ModelKey, ...]
    missing_assumptions: tuple[str, ...]
    missing_assumptions_by_model_key: tuple[
        tuple[ModelKey, tuple[str, ...]],
        ...,
    ]

    @property
    def selectable(self) -> bool:
        return bool(self.assumption_ready_model_keys)

    def missing_for(self, model_key: ModelKey) -> tuple[str, ...]:
        for key, missing in self.missing_assumptions_by_model_key:
            if key == model_key:
                return missing
        raise KeyError(model_key)


@dataclass(frozen=True)
class SegmentPlanResolution:
    segment_id: str
    status: ValuationPlanStatus
    candidates: tuple[SegmentMethodCandidate, ...]
    selected_model_key: ModelKey | None
    rationale: str
    missing_assumptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValuationPlanCompilation:
    status: ValuationPlanStatus
    plan: CompanyValuationPlan | None
    scenario_set_hash: str
    module_plan_hash: str
    capability_registry_hash: str
    evaluator_registry_hash: str
    method_choices_hash: str
    segment_resolutions: tuple[SegmentPlanResolution, ...]
    warranted_per_segments: tuple[str, ...]
    aggregator_bindings: tuple[str, ...]
    missing_assumptions: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.status is ValuationPlanStatus.READY and self.plan is not None


def compile_company_valuation_plan(
    module_plan: ModuleRequirementPlan,
    scenario_set: BoundScenarioSet,
    *,
    evaluator_registry: EvaluatorRegistry,
    capability_registry: MethodCapabilityRegistry,
    inputs: CompanyValuationPlanInputs,
    method_choices: tuple[SegmentMethodChoice, ...] = (),
) -> ValuationPlanCompilation:
    module_plan.validate()
    if not scenario_set.scenarios or not scenario_set.scenario_set_hash:
        raise ValueError(
            "valuation plan compilation requires a non-empty hashed scenario set"
        )
    module_hash = valuation_module_plan_hash(module_plan)
    capability_hash = valuation_capability_registry_hash(capability_registry)
    evaluator_hash = valuation_evaluator_registry_hash(evaluator_registry)
    expected_segment_ids = tuple(
        item.segment_id for item in module_plan.segments
    )
    inputs.validate(expected_segment_ids=expected_segment_ids)
    choices = _validate_choices(method_choices, expected_segment_ids)
    method_choice_hash = valuation_method_choices_hash(method_choices)

    warranted_per_segments: list[str] = []
    aggregator_bindings: list[str] = []
    resolutions: list[SegmentPlanResolution] = []
    compiled_segments: list[SegmentValuationPlan] = []

    for segment in module_plan.segments:
        capabilities = _segment_capabilities(segment, capability_registry)
        if any(
            item.kind is MethodKind.CROSS_METHOD_ENGINE
            and item.method == "warranted_per"
            and item.runtime_status is not MethodRuntimeStatus.NOT_IMPLEMENTED
            for item in capabilities
        ):
            warranted_per_segments.append(segment.segment_id)
        aggregator_bindings.extend(
            f"{segment.segment_id}:{item.archetype}/{item.method}"
            for item in capabilities
            if item.kind is MethodKind.AGGREGATOR
        )

        candidates = tuple(
            _candidate_for(
                capability,
                scenario_set=scenario_set,
                evaluator_registry=evaluator_registry,
                segment_id=segment.segment_id,
            )
            for capability in capabilities
            if capability.kind is MethodKind.SEGMENT_EVALUATOR
        )
        choice = choices.get(segment.segment_id)
        resolution = _resolve_segment(segment, candidates, choice)
        resolutions.append(resolution)
        if (
            resolution.status is not ValuationPlanStatus.READY
            or resolution.selected_model_key is None
        ):
            continue

        binding = inputs.binding_for(segment.segment_id)
        capability = capability_registry.get(
            resolution.selected_model_key.archetype,
            resolution.selected_model_key.method,
        )
        ev_adjustment = binding.ev_to_equity_adjustment_key
        if capability.output_kind == "enterprise_value" and not ev_adjustment:
            missing = (
                f"PLAN/{segment.segment_id}/ev_to_equity_adjustment_key",
            )
            resolutions[-1] = SegmentPlanResolution(
                segment_id=segment.segment_id,
                status=ValuationPlanStatus.ASSUMPTION_GAP,
                candidates=candidates,
                selected_model_key=None,
                rationale=(
                    "enterprise-value evaluator requires an explicit "
                    "EV-to-equity adjustment assumption key"
                ),
                missing_assumptions=missing,
            )
            continue
        if capability.output_kind == "equity_value" and ev_adjustment is not None:
            missing = (
                f"PLAN/{segment.segment_id}/remove_ev_to_equity_adjustment_key",
            )
            resolutions[-1] = SegmentPlanResolution(
                segment_id=segment.segment_id,
                status=ValuationPlanStatus.ASSUMPTION_GAP,
                candidates=candidates,
                selected_model_key=None,
                rationale=(
                    "equity-value evaluator must not apply a second "
                    "EV-to-equity adjustment"
                ),
                missing_assumptions=missing,
            )
            continue
        compiled_segments.append(
            SegmentValuationPlan(
                asset_id=binding.asset_id,
                segment_id=binding.segment_id,
                model_key=resolution.selected_model_key,
                ownership_key=binding.ownership_key,
                ev_to_equity_adjustment_key=ev_adjustment,
            )
        )

    missing_global = _missing_plan_assumptions(
        scenario_set,
        inputs=inputs,
        selected_segments=tuple(compiled_segments),
    )
    resolution_missing = tuple(
        value
        for resolution in resolutions
        for value in resolution.missing_assumptions
    )
    missing_all = tuple(dict.fromkeys((*missing_global, *resolution_missing)))
    overall = _overall_status(tuple(resolutions), missing_global)
    plan: CompanyValuationPlan | None = None
    if overall is ValuationPlanStatus.READY:
        plan = CompanyValuationPlan(
            segments=tuple(compiled_segments),
            reporting_unit=inputs.reporting_unit,
            diluted_shares_key=inputs.diluted_shares_key,
            parent_adjustments=inputs.parent_adjustments,
        )
        plan.validate()

    return ValuationPlanCompilation(
        status=overall,
        plan=plan,
        scenario_set_hash=scenario_set.scenario_set_hash,
        module_plan_hash=module_hash,
        capability_registry_hash=capability_hash,
        evaluator_registry_hash=evaluator_hash,
        method_choices_hash=method_choice_hash,
        segment_resolutions=tuple(resolutions),
        warranted_per_segments=tuple(dict.fromkeys(warranted_per_segments)),
        aggregator_bindings=tuple(dict.fromkeys(aggregator_bindings)),
        missing_assumptions=missing_all,
    )


def _segment_capabilities(
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


def _candidate_for(
    capability: MethodCapability,
    *,
    scenario_set: BoundScenarioSet,
    evaluator_registry: EvaluatorRegistry,
    segment_id: str,
) -> SegmentMethodCandidate:
    registered = tuple(
        sorted(
            (
                key
                for key in evaluator_registry.keys()
                if key.archetype == capability.archetype
                and key.method == capability.method
            ),
            key=lambda key: (key.archetype, key.method, key.version),
        )
    )
    ready: list[ModelKey] = []
    missing_union: list[str] = []
    missing_by_key: list[tuple[ModelKey, tuple[str, ...]]] = []
    for key in registered:
        evaluator = evaluator_registry.get(key, segment_id=segment_id)
        key_missing: list[str] = []
        for scenario in scenario_set.scenarios:
            for assumption_key in evaluator.required_assumption_keys:
                if not _scenario_has(scenario, assumption_key):
                    key_missing.append(
                        f"{scenario.scenario_id}/{assumption_key}"
                    )
        exact_missing = tuple(dict.fromkeys(key_missing))
        missing_by_key.append((key, exact_missing))
        if exact_missing:
            missing_union.extend(exact_missing)
        else:
            ready.append(key)
    return SegmentMethodCandidate(
        archetype=capability.archetype,
        method=capability.method,
        runtime_status=capability.runtime_status,
        output_kind=capability.output_kind,
        registered_model_keys=registered,
        assumption_ready_model_keys=tuple(ready),
        missing_assumptions=tuple(dict.fromkeys(missing_union)),
        missing_assumptions_by_model_key=tuple(missing_by_key),
    )


def _resolution(
    *,
    segment: SegmentModuleRequirementPlan,
    status: ValuationPlanStatus,
    candidates: tuple[SegmentMethodCandidate, ...],
    selected_model_key: ModelKey | None,
    rationale: str,
    missing_assumptions: tuple[str, ...] = (),
) -> SegmentPlanResolution:
    return SegmentPlanResolution(
        segment_id=segment.segment_id,
        status=status,
        candidates=candidates,
        selected_model_key=selected_model_key,
        rationale=rationale,
        missing_assumptions=missing_assumptions,
    )


def _resolve_segment(
    segment: SegmentModuleRequirementPlan,
    candidates: tuple[SegmentMethodCandidate, ...],
    choice: SegmentMethodChoice | None,
) -> SegmentPlanResolution:
    if choice is not None:
        matches = tuple(
            candidate
            for candidate in candidates
            if candidate.archetype == choice.archetype
            and candidate.method == choice.method
        )
        if not matches:
            return _resolution(
                segment=segment,
                status=ValuationPlanStatus.CAPABILITY_GAP,
                candidates=candidates,
                selected_model_key=None,
                rationale=(
                    f"requested method {choice.archetype}/{choice.method} "
                    "is not an allowed segment evaluator"
                ),
            )
        candidate = matches[0]
        if candidate.runtime_status is MethodRuntimeStatus.NOT_IMPLEMENTED:
            return _resolution(
                segment=segment,
                status=ValuationPlanStatus.CAPABILITY_GAP,
                candidates=candidates,
                selected_model_key=None,
                rationale=(
                    f"requested method {choice.archetype}/{choice.method} "
                    "is not implemented"
                ),
            )

        if choice.version is not None:
            registered_version = tuple(
                key
                for key in candidate.registered_model_keys
                if key.version == choice.version
            )
            if len(registered_version) != 1:
                return _resolution(
                    segment=segment,
                    status=ValuationPlanStatus.CAPABILITY_GAP,
                    candidates=candidates,
                    selected_model_key=None,
                    rationale=(
                        "requested exact evaluator "
                        f"{choice.archetype}/{choice.method}/{choice.version} "
                        "is not registered"
                    ),
                )
            exact_key = registered_version[0]
            exact_missing = candidate.missing_for(exact_key)
            if not exact_missing:
                return _resolution(
                    segment=segment,
                    status=ValuationPlanStatus.READY,
                    candidates=candidates,
                    selected_model_key=exact_key,
                    rationale=(
                        "explicit method/version choice validated against "
                        "Industry DNA, capability, registry and assumptions"
                    ),
                )
            return _resolution(
                segment=segment,
                status=ValuationPlanStatus.ASSUMPTION_GAP,
                candidates=candidates,
                selected_model_key=None,
                rationale=(
                    "requested exact evaluator version is registered but "
                    "required compiled assumptions are missing: "
                    + ", ".join(exact_missing)
                ),
                missing_assumptions=exact_missing,
            )

        eligible = candidate.assumption_ready_model_keys
        if len(eligible) == 1:
            return _resolution(
                segment=segment,
                status=ValuationPlanStatus.READY,
                candidates=candidates,
                selected_model_key=eligible[0],
                rationale=(
                    "explicit method choice validated against Industry DNA, "
                    "capability, registry and assumptions"
                ),
            )
        if not candidate.registered_model_keys:
            return _resolution(
                segment=segment,
                status=ValuationPlanStatus.CAPABILITY_GAP,
                candidates=candidates,
                selected_model_key=None,
                rationale="requested method has no exact evaluator registration",
            )
        if not eligible and candidate.missing_assumptions:
            return _resolution(
                segment=segment,
                status=ValuationPlanStatus.ASSUMPTION_GAP,
                candidates=candidates,
                selected_model_key=None,
                rationale=(
                    "requested method is missing compiled assumptions: "
                    + ", ".join(candidate.missing_assumptions)
                ),
                missing_assumptions=candidate.missing_assumptions,
            )
        return _resolution(
            segment=segment,
            status=ValuationPlanStatus.METHOD_CHOICE_REQUIRED,
            candidates=candidates,
            selected_model_key=None,
            rationale=(
                "requested method has multiple eligible evaluator versions; "
                "specify version"
            ),
        )

    implemented = tuple(
        candidate
        for candidate in candidates
        if candidate.runtime_status is not MethodRuntimeStatus.NOT_IMPLEMENTED
    )
    eligible = tuple(
        key
        for candidate in implemented
        for key in candidate.assumption_ready_model_keys
    )
    if len(eligible) == 1:
        return _resolution(
            segment=segment,
            status=ValuationPlanStatus.READY,
            candidates=candidates,
            selected_model_key=eligible[0],
            rationale=(
                "only one exact allowed evaluator is executable from the "
                "compiled scenario assumptions"
            ),
        )
    if len(eligible) > 1:
        return _resolution(
            segment=segment,
            status=ValuationPlanStatus.METHOD_CHOICE_REQUIRED,
            candidates=candidates,
            selected_model_key=None,
            rationale=(
                "multiple exact allowed evaluators are executable; economic "
                "method selection must be proposed explicitly"
            ),
        )
    registered = tuple(
        key
        for candidate in implemented
        for key in candidate.registered_model_keys
    )
    missing = tuple(
        dict.fromkeys(
            value
            for candidate in implemented
            for value in candidate.missing_assumptions
        )
    )
    if registered and missing:
        return _resolution(
            segment=segment,
            status=ValuationPlanStatus.ASSUMPTION_GAP,
            candidates=candidates,
            selected_model_key=None,
            rationale=(
                "registered allowed evaluators lack compiled assumptions: "
                + ", ".join(missing)
            ),
            missing_assumptions=missing,
        )
    return _resolution(
        segment=segment,
        status=ValuationPlanStatus.CAPABILITY_GAP,
        candidates=candidates,
        selected_model_key=None,
        rationale=(
            "no exact implemented segment evaluator is registered for the "
            "selected Industry DNA"
        ),
    )


def _validate_choices(
    choices: tuple[SegmentMethodChoice, ...],
    expected_segment_ids: tuple[str, ...],
) -> dict[str, SegmentMethodChoice]:
    result: dict[str, SegmentMethodChoice] = {}
    allowed_segments = set(expected_segment_ids)
    for item in choices:
        item.validate()
        if item.segment_id not in allowed_segments:
            raise ValueError(
                f"method choice references unknown segment {item.segment_id}"
            )
        if item.segment_id in result:
            raise ValueError(
                f"duplicate method choice for segment {item.segment_id}"
            )
        result[item.segment_id] = item
    return result


def _missing_plan_assumptions(
    scenario_set: BoundScenarioSet,
    *,
    inputs: CompanyValuationPlanInputs,
    selected_segments: tuple[SegmentValuationPlan, ...],
) -> tuple[str, ...]:
    required: list[str] = [inputs.diluted_shares_key]
    required.extend(item.ownership_key for item in inputs.segment_bindings)
    for segment in selected_segments:
        if segment.ev_to_equity_adjustment_key:
            required.append(segment.ev_to_equity_adjustment_key)
    required.extend(item.assumption_key for item in inputs.parent_adjustments)
    missing: list[str] = []
    for scenario in scenario_set.scenarios:
        for key in dict.fromkeys(required):
            if not _scenario_has(scenario, key):
                missing.append(f"{scenario.scenario_id}/{key}")
    return tuple(dict.fromkeys(missing))


def _overall_status(
    resolutions: tuple[SegmentPlanResolution, ...],
    missing_global: tuple[str, ...],
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
    if missing_global or any(
        item.status is ValuationPlanStatus.ASSUMPTION_GAP
        for item in resolutions
    ):
        return ValuationPlanStatus.ASSUMPTION_GAP
    if not resolutions or any(
        item.status is not ValuationPlanStatus.READY
        for item in resolutions
    ):
        return ValuationPlanStatus.CAPABILITY_GAP
    return ValuationPlanStatus.READY


def _scenario_has(scenario, key: str) -> bool:
    try:
        scenario.get(key)
        return True
    except KeyError:
        return False


def _duplicates(values: tuple[str, ...]) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return tuple(sorted(value for value, count in counts.items() if count > 1))


def _stable_contract_hash(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda item: (
                item.value if isinstance(item, Enum) else str(item)
            ),
        ).encode("utf-8")
    ).hexdigest()
