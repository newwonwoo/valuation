"""The cold-start factory: a full LIVE_PRIMARY provider set with no company code.

``build_generic_kr_runtime_factory`` is the function whose existence answers the
question ``stage_capability.probe_cold_start`` asks. Every seat in
:class:`~.live_runtime.LivePrimaryProviders` is filled by a company-neutral
implementation from this repository; the only injected pieces are deployment
facts — the OpenDART network transport and the LLM proposal transport — which
are identical for every company.

What the caller declares per run, and why that is not company code:

- ``method_choices`` — which registered valuation method to apply. Choosing a
  method is analyst intent the runtime already demands explicitly
  (``VALUATION_METHOD_INTENT`` refuses ambiguity); writing it down here is the
  declaration, not an implementation.
- ``as_of`` — the knowledge-time cutoff for the run.

Everything else — identity, filings, facts, archetype, scanners, hypotheses,
bridges, plan, evaluators — is derived by the providers at run time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .generic_funding import generic_ledger_funding_scanner
from .generic_kr_industry import (
    CachedCompanyProfileFetcher,
    DEFAULT_CLASSIFICATION_MAP_PATH,
    classified_industry_dna_router,
    classified_segment_decomposer,
    filing_cadence_freshness_loader,
    load_kr_industry_classification,
    opendart_filing_snapshot_loader,
)
from .generic_llm_staff import (
    GenericBridgeAnalyst,
    GenericIntelligenceOfficer,
    GenericRedTeamOfficer,
)
from .generic_scanners import generic_scanner_runners
from .generic_valuation_plan import (
    DILUTED_SHARES_KEY,
    EV_ADJUSTMENT_KEY,
    OWNERSHIP_KEY,
    GenericValuationPlanError,
    composed_generic_registry_loader,
    conventional_valuation_plan_inputs_loader,
)
from .kr_filing_kpi_collector import filing_kpi_collector_provider
from .kr_opendart_provider import (
    KRLiveProviderExtensions,
    KRLiveRuntimeFactory,
    OpenDartFilingSelection,
    OpenDartNetwork,
)
from .llm_transport import ProposalTransport
from .method_capabilities import (
    MethodCapabilityRegistry,
    load_default_method_capability_registry,
)
from .scenario_binding import ScenarioBindingSpec
from .valuation_plan_compiler import SegmentMethodChoice


#: Assumption keys each supported execution family requires, before the shared
#: value-binding conventions. Forecast-length keys expand per ``forecast_years``.
_FAMILY_KEY_TEMPLATES: dict[str, tuple[str, ...]] = {
    "normalized_multiple": ("normalized_ebitda", "normalized_multiple"),
    "explicit_fcff_dcf": ("fcff_year_{year}", "terminal_growth", "terminal_roic"),
    "contracted_backlog_dcf": (
        "opening_backlog",
        "opening_revenue",
        "new_orders_year_{year}",
        "backlog_burn_rate_year_{year}",
        "operating_margin_year_{year}",
        "operating_tax_rate",
        "depreciation_rate_of_revenue",
        "maintenance_capex_rate_of_revenue",
        "incremental_working_capital_rate",
        "terminal_growth",
        "terminal_roic",
    ),
}

_VALUE_BINDING_KEYS = (OWNERSHIP_KEY, EV_ADJUSTMENT_KEY, DILUTED_SHARES_KEY)


def required_assumption_keys(
    *,
    method_choices: tuple[SegmentMethodChoice, ...],
    forecast_years: int,
    capability_registry: MethodCapabilityRegistry | None = None,
) -> tuple[str, ...]:
    """The exact key set the compiler will demand for these method choices."""
    registry = capability_registry or load_default_method_capability_registry()
    keys: list[str] = []
    for choice in method_choices:
        family = registry.get(choice.archetype, choice.method).execution_family
        template = _FAMILY_KEY_TEMPLATES.get(family)
        if template is None:
            raise GenericValuationPlanError(
                f"execution family {family} has no generic assumption-key template"
            )
        for item in template:
            if "{year}" in item:
                keys.extend(
                    item.format(year=year) for year in range(1, forecast_years + 1)
                )
            else:
                keys.append(item)
    keys.extend(_VALUE_BINDING_KEYS)
    return tuple(dict.fromkeys(keys))


@dataclass(frozen=True)
class GenericKRRuntimeSpec:
    """Per-run declarations for a cold start. Deployment facts arrive separately."""

    as_of: str
    scenario_ids: tuple[str, ...]
    method_choices: tuple[SegmentMethodChoice, ...]
    filing: OpenDartFilingSelection
    reporting_unit: str = "KRW"
    forecast_years: int = 5
    classification_map_path: str | Path = DEFAULT_CLASSIFICATION_MAP_PATH
    freshness_max_age_days: int = 120

    def validate(self) -> None:
        if not self.as_of or not self.scenario_ids or not self.method_choices:
            raise GenericValuationPlanError(
                "generic runtime spec requires as_of, scenario_ids and method_choices"
            )
        if len(self.scenario_ids) != len(set(self.scenario_ids)):
            raise GenericValuationPlanError("scenario_ids must be unique")
        for choice in self.method_choices:
            choice.validate()
        self.filing.validate()


def build_generic_kr_runtime_factory(
    *,
    network: OpenDartNetwork,
    transport: ProposalTransport,
    spec: GenericKRRuntimeSpec,
    capability_registry: MethodCapabilityRegistry | None = None,
) -> KRLiveRuntimeFactory:
    """Assemble the complete cold-start factory for an unseen KR company."""
    spec.validate()
    network.validate()
    classification = load_kr_industry_classification(spec.classification_map_path)
    profile_fetcher = CachedCompanyProfileFetcher(
        fetch_text=network.fetch_text,
        api_key=network.api_key,
    )
    keys = required_assumption_keys(
        method_choices=spec.method_choices,
        forecast_years=spec.forecast_years,
        capability_registry=capability_registry,
    )
    extensions = KRLiveProviderExtensions(
        additional_collectors=(
            filing_kpi_collector_provider(
                network,
                as_of=spec.as_of,
                segment_id=spec.filing.segment_id,
            ),
        ),
        industry_snapshot_loader=opendart_filing_snapshot_loader(
            fetch_text=network.fetch_text,
            as_of=spec.as_of,
            api_key=network.api_key,
        ),
        freshness_loader=filing_cadence_freshness_loader(
            as_of=spec.as_of,
            max_age_days=spec.freshness_max_age_days,
        ),
        segment_decomposer=classified_segment_decomposer(
            profile_fetcher=profile_fetcher,
            classification=classification,
        ),
        industry_dna_router=classified_industry_dna_router(
            profile_fetcher=profile_fetcher,
            classification=classification,
        ),
        scanner_runners=generic_scanner_runners(),
        funding_scanner=generic_ledger_funding_scanner,
        intelligence_officer=GenericIntelligenceOfficer(transport=transport),
        red_team_officer=GenericRedTeamOfficer(transport=transport),
        bridge_analyst=GenericBridgeAnalyst(
            transport=transport,
            scenario_ids=spec.scenario_ids,
            required_keys=keys,
        ),
        evaluator_registry_loader=composed_generic_registry_loader(
            method_choices=spec.method_choices,
            forecast_years=spec.forecast_years,
            capability_registry=capability_registry,
        ),
        valuation_plan_inputs_loader=conventional_valuation_plan_inputs_loader(
            reporting_unit=spec.reporting_unit,
        ),
    )
    return KRLiveRuntimeFactory(
        network=network,
        filing=spec.filing,
        extensions=extensions,
        scenario_binding_spec=ScenarioBindingSpec(
            scenario_ids=spec.scenario_ids,
            required_keys=keys,
        ),
        method_choices=spec.method_choices,
        capability_registry=capability_registry,
    )
