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
from .per import EconomicAssumptionFingerprint
from .per_adapters import LivePERInputs, PERApplicability
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


def _live_wacc(context: OrchestratorContext) -> LiveWACCStageResult:
    wacc_result = context.data.get("live_wacc_result")
    if not isinstance(wacc_result, LiveWACCStageResult):
        raise GenericValuationPlanError(
            "a WACC-bound execution family requires LiveWACCStageResult in context"
        )
    rate = wacc_result.wacc_result.wacc
    if not isfinite(rate) or rate <= 0:
        raise GenericValuationPlanError("live WACC must be finite and positive")
    return wacc_result


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
        rate: Decimal | None = None
        rate_path = ""
        beta_path = None
        if needs_wacc:
            # The audit demands every DCF scenario carry hash-bound Beta→WACC
            # economic paths, so the lineage is the stage result's own snapshot
            # hash — a symbolic label would not replay.
            wacc_result = _live_wacc(context)
            rate = Decimal(str(wacc_result.wacc_result.wacc))
            rate_path = f"wacc:{wacc_result.snapshot_hash}"
            beta_path = (
                f"beta:{wacc_result.beta_result.snapshot_hash}"
                if wacc_result.beta_result is not None
                else None
            )
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
                        beta_path_id=beta_path,
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
                        beta_path_id=beta_path,
                    )
                )
        return evaluator_registry

    return load


def generic_backlog_dcf_fingerprint_loader(
    *,
    scenario_id: str,
    forecast_years: int,
):
    """DCFConsistencyFingerprintLoader derived from the compiled backlog scenario.

    The Warranted-PER cross-check binds a fingerprint of the DCF's economics so
    a PER route can never quietly assume different growth than the DCF it is
    checked against. For the backlog family the fingerprint is fully determined
    by the compiled assumptions — the same roll-forward the evaluator runs
    (revenue_y = opening_backlog_y x burn_y; backlog carries forward with new
    orders) — so this loader re-derives it deterministically and cites nothing
    the compiler did not already seal.
    """
    if forecast_years < 1:
        raise GenericValuationPlanError("forecast_years must be positive")

    def load(context: OrchestratorContext) -> EconomicAssumptionFingerprint:
        compiled = context.data.get("compiled_assumption_set")
        if compiled is None:
            raise GenericValuationPlanError(
                "compiled_assumption_set is required before the DCF fingerprint"
            )

        def amount(key: str) -> Decimal:
            return compiled.get(key, scenario_id).measure.amount

        backlog = amount("opening_backlog")
        prior_revenue = amount("opening_revenue")
        revenues: list[Decimal] = []
        reinvestment: list[float] = []
        capex_rate = amount("maintenance_capex_rate_of_revenue")
        wc_rate = amount("incremental_working_capital_rate")
        zero = Decimal("0")
        for year in range(1, forecast_years + 1):
            burn = amount(f"backlog_burn_rate_year_{year}")
            orders = amount(f"new_orders_year_{year}")
            revenue = backlog * burn
            if revenue <= 0:
                raise GenericValuationPlanError(
                    f"backlog fingerprint requires positive revenue in year {year}"
                )
            working_capital = max(zero, revenue - prior_revenue) * wc_rate
            reinvestment.append(float((capex_rate * revenue + working_capital) / revenue))
            revenues.append(revenue)
            backlog = backlog + orders - revenue
            prior_revenue = revenue
        growth: list[float] = []
        previous = amount("opening_revenue")
        for revenue in revenues:
            if previous <= 0:
                raise GenericValuationPlanError(
                    "backlog fingerprint requires positive prior revenue"
                )
            growth.append(float(revenue / previous - 1))
            previous = revenue
        margins = tuple(
            float(amount(f"operating_margin_year_{year}"))
            for year in range(1, forecast_years + 1)
        )
        return EconomicAssumptionFingerprint(
            growth_rates=tuple(growth),
            margin_path=margins,
            reinvestment_path=tuple(reinvestment),
            growth_duration_years=forecast_years,
        )

    return load


def withheld_per_loader():
    """PERInputsLoader that withholds PER rather than approximating one.

    An honest Warranted-PER requires an authorized same-as-of Economic-Twin
    residual PER pack. The generic cold start ships none, and fabricating a
    peer PER table would be exactly the invented number this engine refuses —
    so the cross-check is declared NOT_APPLICABLE with its reason, the stage
    records the withholding, and the primary method stands alone. Declaring a
    real PER pack is a future operator input, not a default.
    """

    def load(context: OrchestratorContext) -> LivePERInputs:
        identity = context.data.get("resolved_company_identity")
        target_id = getattr(identity, "target_id", "")
        if not target_id:
            raise GenericValuationPlanError(
                "resolved company identity is required before PER applicability"
            )
        return LivePERInputs(
            target_id=target_id,
            applicability=PERApplicability.NOT_APPLICABLE,
            applicability_rationale=(
                "no authorized same-as-of Economic-Twin residual PER pack is "
                "declared for this run; PER is withheld rather than approximated"
            ),
        )

    return load
