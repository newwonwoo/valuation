from __future__ import annotations

from dataclasses import dataclass, replace

from .generic_llm_staff import GenericBridgeAnalyst
from .generic_live_providers import required_assumption_keys
from .generic_valuation_plan import (
    DILUTED_SHARES_KEY,
    EV_ADJUSTMENT_KEY,
    OWNERSHIP_KEY,
    GenericValuationPlanError,
    SUPPORTED_EXECUTION_FAMILIES,
    composed_generic_registry_loader,
    conventional_valuation_plan_inputs_loader,
    family_prototype,
    generic_backlog_dcf_fingerprint_loader,
)
from .kr_opendart_provider import KRLiveRuntimeFactory
from .llm_staff import LLMStaffContext
from .method_capabilities import (
    MethodCapability,
    MethodCapabilityRegistry,
    MethodKind,
    MethodRuntimeStatus,
    load_default_method_capability_registry,
)
from .module_plan import ModuleRequirementPlan, SegmentModuleRequirementPlan
from .orchestrator import OrchestratorContext
from .scenario_binding import BoundScenarioSet
from .valuation_method_intent import ValuationMethodIntent
from .valuation_plan_compiler import SegmentMethodChoice


AUTO_METHOD_ROUTING_FLAG = "auto_valuation_method_routing"
AUTO_METHOD_ROUTING_FORECAST_YEARS = "auto_valuation_forecast_years"


class AutoMethodRoutingError(GenericValuationPlanError):
    """Raised when deterministic Industry-DNA routing cannot prepare a safe path."""


def _segment_candidates(
    segment: SegmentModuleRequirementPlan,
    registry: MethodCapabilityRegistry,
) -> tuple[MethodCapability, ...]:
    allowed = set(segment.allowed_valuation_methods)
    archetypes = set(segment.archetypes)
    candidates = tuple(
        item
        for item in registry.capabilities
        if item.kind is MethodKind.SEGMENT_EVALUATOR
        and item.runtime_status is not MethodRuntimeStatus.NOT_IMPLEMENTED
        and item.archetype in archetypes
        and item.method in allowed
    )
    if not candidates:
        raise AutoMethodRoutingError(
            f"segment {segment.segment_id} has no implemented allowed segment evaluator"
        )
    unsupported = tuple(
        sorted(
            {
                f"{item.archetype}/{item.method}:{item.execution_family}"
                for item in candidates
                if item.execution_family not in SUPPORTED_EXECUTION_FAMILIES
            }
        )
    )
    if unsupported:
        raise AutoMethodRoutingError(
            "generic auto routing cannot construct evaluator families: "
            + ", ".join(unsupported)
        )
    return candidates


def _prototype_keys(capability: MethodCapability, forecast_years: int) -> tuple[str, ...]:
    prototype = family_prototype(capability.execution_family, forecast_years)
    if prototype is None:
        raise AutoMethodRoutingError(
            "generic auto routing has no evaluator prototype for "
            f"{capability.execution_family}"
        )
    return tuple(prototype.required_assumption_keys)


def _economic_signature(
    capability: MethodCapability,
    *,
    forecast_years: int,
) -> tuple[object, ...]:
    """Identity of an economic method independent of an overlapping archetype label."""
    return (
        capability.method,
        capability.execution_family,
        capability.output_kind,
        capability.requires_beta,
        capability.requires_wacc,
        _prototype_keys(capability, forecast_years),
    )


def _collapse_equivalent_candidates(
    segment: SegmentModuleRequirementPlan,
    candidates: tuple[MethodCapability, ...],
    *,
    forecast_years: int,
) -> tuple[MethodCapability, ...]:
    """Collapse only exact economic duplicates created by multi-archetype routing.

    A company may legitimately route to both ``commodity_price_taker`` and
    ``process_spread``. If both archetypes expose the same method, runtime
    family, risk requirements, output kind and evaluator assumptions, that is
    one economic method with two registry bindings rather than two competing
    valuation methods. The canonical archetype order breaks only that binding
    tie. Distinct methods or execution families remain distinct and therefore
    still require a user decision when more than one survives.
    """
    archetype_rank = {name: index for index, name in enumerate(segment.archetypes)}
    grouped: dict[tuple[object, ...], list[MethodCapability]] = {}
    for capability in candidates:
        grouped.setdefault(
            _economic_signature(capability, forecast_years=forecast_years), []
        ).append(capability)
    collapsed: list[MethodCapability] = []
    for group in grouped.values():
        collapsed.append(
            min(
                group,
                key=lambda item: (
                    archetype_rank.get(item.archetype, len(archetype_rank)),
                    item.archetype,
                    item.method,
                ),
            )
        )
    return tuple(collapsed)


def auto_bridge_required_assumption_keys(
    plan: ModuleRequirementPlan,
    *,
    evidence_metrics: frozenset[str],
    forecast_years: int,
    capability_registry: MethodCapabilityRegistry | None = None,
) -> tuple[str, ...]:
    """Prepare assumptions only for evidence-complete candidate methods.

    Filtering a method for missing source inputs is not an economic-method
    decision. The formal VALUATION_METHOD_INTENT stage later owns selection from
    the candidates whose assumptions survived deterministic compilation.
    """
    plan.validate()
    if forecast_years < 1 or forecast_years > 30:
        raise AutoMethodRoutingError("forecast_years must be in [1, 30]")
    registry = capability_registry or load_default_method_capability_registry()
    keys: list[str] = []
    needs_ev_adjustment = False
    for segment in plan.segments:
        feasible: list[MethodCapability] = []
        missing_by_candidate: list[str] = []
        for capability in _segment_candidates(segment, registry):
            required = _prototype_keys(capability, forecast_years)
            missing = tuple(key for key in required if key not in evidence_metrics)
            if not missing:
                feasible.append(capability)
                keys.extend(required)
                if capability.output_kind == "enterprise_value":
                    needs_ev_adjustment = True
            else:
                missing_by_candidate.append(
                    f"{capability.archetype}/{capability.method}=" + ",".join(missing)
                )
        if not feasible:
            raise AutoMethodRoutingError(
                f"segment {segment.segment_id} has no evidence-complete valuation candidate; "
                + "; ".join(missing_by_candidate)
            )
    keys.append(OWNERSHIP_KEY)
    if needs_ev_adjustment:
        keys.append(EV_ADJUSTMENT_KEY)
    keys.append(DILUTED_SHARES_KEY)
    return tuple(dict.fromkeys(keys))


def auto_feasible_method_choices(
    plan: ModuleRequirementPlan,
    scenarios: BoundScenarioSet,
    *,
    forecast_years: int,
    capability_registry: MethodCapabilityRegistry | None = None,
) -> tuple[SegmentMethodChoice, ...]:
    """Return choices only when one economic candidate remains per segment."""
    plan.validate()
    registry = capability_registry or load_default_method_capability_registry()
    scenario_keys = tuple(
        {item.key for item in scenario.assumptions}
        for scenario in scenarios.scenarios
    )
    if not scenario_keys:
        return ()
    choices: list[SegmentMethodChoice] = []
    for segment in plan.segments:
        feasible = tuple(
            capability
            for capability in _segment_candidates(segment, registry)
            if all(
                set(_prototype_keys(capability, forecast_years)).issubset(keys)
                for keys in scenario_keys
            )
        )
        economic_candidates = _collapse_equivalent_candidates(
            segment,
            feasible,
            forecast_years=forecast_years,
        )
        if len(economic_candidates) != 1:
            return ()
        selected = economic_candidates[0]
        choices.append(
            SegmentMethodChoice(
                segment.segment_id,
                selected.archetype,
                selected.method,
            )
        )
    return tuple(choices)


@dataclass(frozen=True)
class AutoRoutingBridgeAnalyst:
    transport: object
    scenario_ids: tuple[str, ...]
    forecast_years: int
    capability_registry: MethodCapabilityRegistry
    max_attempts: int = 2

    def __call__(self, context: LLMStaffContext, hypotheses, red_team):
        plan = context.module_requirement_plan
        if not isinstance(plan, ModuleRequirementPlan):
            raise AutoMethodRoutingError(
                "ModuleRequirementPlan is required before auto-routing Bridge analysis"
            )
        evidence_metrics = frozenset(
            item.metric for item in context.ledger.active()
        )
        keys = auto_bridge_required_assumption_keys(
            plan,
            evidence_metrics=evidence_metrics,
            forecast_years=self.forecast_years,
            capability_registry=self.capability_registry,
        )
        return GenericBridgeAnalyst(
            transport=self.transport,
            scenario_ids=self.scenario_ids,
            required_keys=keys,
            max_attempts=self.max_attempts,
        )(context, hypotheses, red_team)


def auto_evaluator_registry_loader(
    *,
    forecast_years: int,
    capability_registry: MethodCapabilityRegistry,
):
    def load(context: OrchestratorContext):
        intent = context.data.get("valuation_method_intent")
        if not isinstance(intent, ValuationMethodIntent) or not intent.ready:
            raise AutoMethodRoutingError(
                "resolved ValuationMethodIntent is required before evaluator registry composition"
            )
        return composed_generic_registry_loader(
            method_choices=intent.method_choices(),
            forecast_years=forecast_years,
            capability_registry=capability_registry,
        )(context)

    return load


def auto_valuation_plan_inputs_loader(
    *,
    reporting_unit: str,
    capability_registry: MethodCapabilityRegistry,
):
    def load(context: OrchestratorContext):
        intent = context.data.get("valuation_method_intent")
        if not isinstance(intent, ValuationMethodIntent) or not intent.ready:
            raise AutoMethodRoutingError(
                "resolved ValuationMethodIntent is required before valuation plan inputs"
            )
        enterprise_segments = frozenset(
            choice.segment_id
            for choice in intent.method_choices()
            if capability_registry.get(
                choice.archetype, choice.method
            ).output_kind
            == "enterprise_value"
        )
        return conventional_valuation_plan_inputs_loader(
            reporting_unit=reporting_unit,
            ev_adjustment_segments=enterprise_segments,
        )(context)

    return load


def auto_dcf_fingerprint_loader(
    *,
    scenario_id: str,
    forecast_years: int,
    capability_registry: MethodCapabilityRegistry,
):
    def load(context: OrchestratorContext):
        intent = context.data.get("valuation_method_intent")
        if not isinstance(intent, ValuationMethodIntent) or not intent.ready:
            raise AutoMethodRoutingError(
                "resolved ValuationMethodIntent is required before DCF fingerprint"
            )
        families = tuple(
            dict.fromkeys(
                capability_registry.get(
                    choice.archetype, choice.method
                ).execution_family
                for choice in intent.method_choices()
            )
        )
        if families == ("contracted_backlog_dcf",):
            return generic_backlog_dcf_fingerprint_loader(
                scenario_id=scenario_id,
                forecast_years=forecast_years,
            )(context)
        raise AutoMethodRoutingError(
            "selected method path requires a DCF fingerprint but the generic "
            "auto router has no driver-specific provider for: "
            + ", ".join(families)
        )

    return load


def enable_auto_method_routing(
    factory: KRLiveRuntimeFactory,
    *,
    forecast_years: int,
    scenario_ids: tuple[str, ...],
    capability_registry: MethodCapabilityRegistry | None = None,
) -> KRLiveRuntimeFactory:
    """Convert a generic KR factory from explicit method intent to canonical auto mode."""
    if not isinstance(factory, KRLiveRuntimeFactory):
        raise TypeError("factory must be KRLiveRuntimeFactory")
    if not scenario_ids or not all(scenario_ids):
        raise AutoMethodRoutingError("scenario_ids are required")
    registry = capability_registry or load_default_method_capability_registry()

    placeholder_keys = set(
        required_assumption_keys(
            method_choices=factory.method_choices,
            forecast_years=forecast_years,
            capability_registry=registry,
        )
    )
    retained_required: dict[str, tuple[str, ...]] = {}
    for segment_id, metrics in factory.additional_required_evidence.items():
        retained = [metric for metric in metrics if metric not in placeholder_keys]
        for provider in factory.extensions.additional_collectors:
            if provider.capability.collector_id == "operator-declared-underwriting":
                retained.extend(provider.capability.supported_metrics)
        if retained:
            retained_required[segment_id] = tuple(dict.fromkeys(retained))

    extensions = replace(
        factory.extensions,
        bridge_analyst=AutoRoutingBridgeAnalyst(
            transport=factory.extensions.bridge_analyst.transport,
            scenario_ids=scenario_ids,
            forecast_years=forecast_years,
            capability_registry=registry,
        ),
        evaluator_registry_loader=auto_evaluator_registry_loader(
            forecast_years=forecast_years,
            capability_registry=registry,
        ),
        valuation_plan_inputs_loader=auto_valuation_plan_inputs_loader(
            reporting_unit="KRW",
            capability_registry=registry,
        ),
        dcf_fingerprint_loader=auto_dcf_fingerprint_loader(
            scenario_id=scenario_ids[0],
            forecast_years=forecast_years,
            capability_registry=registry,
        ),
    )
    initial = dict(factory.initial_data)
    initial[AUTO_METHOD_ROUTING_FLAG] = True
    initial[AUTO_METHOD_ROUTING_FORECAST_YEARS] = forecast_years

    binding = replace(
        factory.scenario_binding_spec,
        scenario_ids=scenario_ids,
        required_keys=(OWNERSHIP_KEY, DILUTED_SHARES_KEY),
    )
    return replace(
        factory,
        extensions=extensions,
        scenario_binding_spec=binding,
        method_choices=(),
        additional_required_evidence=retained_required,
        capability_registry=registry,
        initial_data=initial,
    )
