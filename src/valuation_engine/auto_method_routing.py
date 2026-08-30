from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

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
from .scenario_binding import ScenarioBindingSpec
from .valuation_method_intent import ValuationMethodIntent


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


def auto_required_assumption_keys(
    plan: ModuleRequirementPlan,
    *,
    forecast_years: int,
    capability_registry: MethodCapabilityRegistry | None = None,
) -> tuple[str, ...]:
    """Return the deterministic union of assumptions for all allowed candidates.

    This function prepares evidence and Bridge cells only. It does not select an
    economic method. The canonical VALUATION_METHOD_INTENT stage keeps that
    authority and will auto-select only when Industry DNA leaves one method.
    """
    plan.validate()
    if forecast_years < 1 or forecast_years > 30:
        raise AutoMethodRoutingError("forecast_years must be in [1, 30]")
    registry = capability_registry or load_default_method_capability_registry()
    keys: list[str] = []
    needs_ev_adjustment = False
    for segment in plan.segments:
        for capability in _segment_candidates(segment, registry):
            prototype = family_prototype(capability.execution_family, forecast_years)
            if prototype is None:
                raise AutoMethodRoutingError(
                    "generic auto routing has no evaluator prototype for "
                    f"{capability.execution_family}"
                )
            keys.extend(prototype.required_assumption_keys)
            if capability.output_kind == "enterprise_value":
                needs_ev_adjustment = True
    keys.append(OWNERSHIP_KEY)
    if needs_ev_adjustment:
        keys.append(EV_ADJUSTMENT_KEY)
    keys.append(DILUTED_SHARES_KEY)
    return tuple(dict.fromkeys(keys))


def auto_required_evidence_map(
    plan: ModuleRequirementPlan,
    *,
    forecast_years: int,
    capability_registry: MethodCapabilityRegistry | None = None,
) -> dict[str, tuple[str, ...]]:
    """Declare candidate-method Evidence after Industry DNA, before collection."""
    plan.validate()
    registry = capability_registry or load_default_method_capability_registry()
    result: dict[str, tuple[str, ...]] = {}
    for index, segment in enumerate(plan.segments):
        keys: list[str] = []
        needs_ev_adjustment = False
        for capability in _segment_candidates(segment, registry):
            prototype = family_prototype(capability.execution_family, forecast_years)
            if prototype is None:
                raise AutoMethodRoutingError(
                    "generic auto routing has no evaluator prototype for "
                    f"{capability.execution_family}"
                )
            keys.extend(prototype.required_assumption_keys)
            if capability.output_kind == "enterprise_value":
                needs_ev_adjustment = True
        keys.append(OWNERSHIP_KEY)
        if needs_ev_adjustment:
            keys.append(EV_ADJUSTMENT_KEY)
        if index == 0:
            keys.append(DILUTED_SHARES_KEY)
        result[segment.segment_id] = tuple(dict.fromkeys(keys))
    return result


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
        keys = auto_required_assumption_keys(
            plan,
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
        retained = tuple(metric for metric in metrics if metric not in placeholder_keys)
        if retained:
            retained_required[segment_id] = retained

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
