"""Company-neutral valuation plan inputs and evaluator registry composition.

The valuation math already exists in generic evaluator families; what was
missing was the glue a cold start needs. Two pieces:

- ``conventional_valuation_plan_inputs_loader`` binds each decomposed segment
  under fixed key conventions (``ownership``, ``ev_adjustment``,
  ``diluted_shares``). The keys are conventions the Bridge Analyst is told to
  propose against; the plan compiler still refuses a plan whose keys the
  compiled scenarios do not carry, so a convention is a contract, not a
  loophole.
- ``composed_generic_registry_loader`` builds the evaluator registry from the
  run's declared method choices. It composes existing families only; an
  execution family this module does not know is a fail-closed error naming the
  family, never a silent fallback to some default evaluator.
"""

from __future__ import annotations

from decimal import Decimal
from math import isfinite

from .backlog_evaluators import BacklogBurnDCFEvaluator
from .dcf_evaluators import ExplicitFCFFDCFEvaluator
from .evaluator_registry import EvaluatorRegistry, NormalizedMultipleEvaluator
from .live_primary_adapters import SegmentDescriptor
from .method_capabilities import (
    MethodCapabilityRegistry,
    load_default_method_capability_registry,
)
from .orchestrator import OrchestratorContext
from .risk_adapters import LiveWACCStageResult
from .valuation_plan_compiler import (
    CompanyValuationPlanInputs,
    SegmentMethodChoice,
    SegmentValueBinding,
)


OWNERSHIP_KEY = "ownership"
EV_ADJUSTMENT_KEY = "ev_adjustment"
DILUTED_SHARES_KEY = "diluted_shares"

#: Execution families this composer knows how to instantiate. Anything else is
#: an explicit gap, reported as such — the registry never falls back.
_WACC_FREE_FAMILIES = frozenset({"normalized_multiple"})
_WACC_BOUND_FAMILIES = frozenset({"explicit_fcff_dcf", "contracted_backlog_dcf"})
SUPPORTED_EXECUTION_FAMILIES = _WACC_FREE_FAMILIES | _WACC_BOUND_FAMILIES


class GenericValuationPlanError(ValueError):
    """Raised when the generic composition cannot honour a method choice."""


def conventional_valuation_plan_inputs_loader(*, reporting_unit: str):
    """ValuationPlanInputsLoader bound to the fixed assumption-key conventions."""
    if not reporting_unit:
        raise GenericValuationPlanError("reporting_unit is required")

    def load(context: OrchestratorContext) -> CompanyValuationPlanInputs:
        segments = context.data.get("segment_descriptors")
        if not isinstance(segments, tuple) or not segments or not all(
            isinstance(item, SegmentDescriptor) for item in segments
        ):
            raise GenericValuationPlanError(
                "segment descriptors are required before valuation plan inputs"
            )
        return CompanyValuationPlanInputs(
            reporting_unit=reporting_unit,
            diluted_shares_key=DILUTED_SHARES_KEY,
            segment_bindings=tuple(
                SegmentValueBinding(
                    segment_id=item.segment_id,
                    asset_id=item.segment_id,
                    ownership_key=OWNERSHIP_KEY,
                    ev_to_equity_adjustment_key=EV_ADJUSTMENT_KEY,
                )
                for item in segments
            ),
        )

    return load


def _discount_rate(context: OrchestratorContext) -> Decimal:
    wacc_result = context.data.get("live_wacc_result")
    if not isinstance(wacc_result, LiveWACCStageResult):
        raise GenericValuationPlanError(
            "a WACC-bound execution family requires LiveWACCStageResult in context"
        )
    rate = wacc_result.wacc_result.wacc
    if not isfinite(rate) or rate <= 0:
        raise GenericValuationPlanError("live WACC must be finite and positive")
    return Decimal(str(rate))


def composed_generic_registry_loader(
    *,
    method_choices: tuple[SegmentMethodChoice, ...],
    forecast_years: int,
    capability_registry: MethodCapabilityRegistry | None = None,
):
    """RegistryLoader composing existing evaluator families from method choices.

    The discount rate for WACC-bound families comes from the run's own
    ``live_wacc_result``, so this loader carries no rate of its own; the
    ``discount_rate_path_id`` records that lineage.
    """
    if not method_choices:
        raise GenericValuationPlanError(
            "generic registry composition requires explicit method choices"
        )
    if forecast_years < 1 or forecast_years > 30:
        raise GenericValuationPlanError("forecast_years must be in [1, 30]")
    registry = capability_registry or load_default_method_capability_registry()
    resolved: list[tuple[SegmentMethodChoice, str, str]] = []
    for choice in method_choices:
        choice.validate()
        capability = registry.get(choice.archetype, choice.method)
        family = capability.execution_family
        if family not in SUPPORTED_EXECUTION_FAMILIES:
            raise GenericValuationPlanError(
                f"execution family {family} ({choice.archetype}/{choice.method}) is not "
                "supported by the generic registry composer; register a provider "
                "for it instead of widening this list silently"
            )
        resolved.append((choice, family, choice.version or "1"))

    def load(context: OrchestratorContext) -> EvaluatorRegistry:
        evaluator_registry = EvaluatorRegistry()
        needs_wacc = any(family in _WACC_BOUND_FAMILIES for _, family, _ in resolved)
        rate: Decimal | None = _discount_rate(context) if needs_wacc else None
        rate_path = "wacc:live_wacc_result"
        seen: set[tuple[str, str, str]] = set()
        for choice, family, version in resolved:
            key = (choice.archetype, choice.method, version)
            if key in seen:
                continue
            seen.add(key)
            if family == "normalized_multiple":
                evaluator_registry.register(
                    NormalizedMultipleEvaluator(choice.archetype, version=version)
                )
            elif family == "explicit_fcff_dcf":
                evaluator_registry.register(
                    ExplicitFCFFDCFEvaluator(
                        archetype=choice.archetype,
                        method=choice.method,
                        version=version,
                        forecast_years=forecast_years,
                        discount_rate=rate,
                        discount_rate_path_id=rate_path,
                    )
                )
            elif family == "contracted_backlog_dcf":
                evaluator_registry.register(
                    BacklogBurnDCFEvaluator(
                        archetype=choice.archetype,
                        method=choice.method,
                        version=version,
                        forecast_years=forecast_years,
                        discount_rate=rate,
                        discount_rate_path_id=rate_path,
                    )
                )
        return evaluator_registry

    return load
