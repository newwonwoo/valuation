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

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from .declared_risk_pack import (
    BETA_SELECTION_METRICS,
    declared_risk_beta_loader,
    declared_risk_provider,
    declared_risk_wacc_loader,
    load_declared_risk_pack,
)
from .generic_funding import generic_ledger_funding_scanner
from .generic_underwriting import declared_underwriting_provider
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
    family_prototype,
    generic_backlog_dcf_fingerprint_loader,
    withheld_per_loader,
)
from .industry_series_collector import (
    DEFAULT_SERIES_REGISTRY_PATH,
    industry_series_collector_providers,
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


def required_assumption_keys_by_segment(
    *,
    method_choices: tuple[SegmentMethodChoice, ...],
    forecast_years: int,
    capability_registry: MethodCapabilityRegistry | None = None,
) -> dict[str, tuple[str, ...]]:
    """The exact key set the compiler will demand, per segment.

    Multi-segment runs namespace every method-specific and per-segment binding
    key as ``<segment_id>_<key>`` (the same prefix the composed registry
    builds its evaluators with), so two segments can run the same execution
    family without sharing an assumption. The company-level diluted-shares
    key stays unprefixed and lives with the first declared segment.
    Single-segment runs keep the historical unprefixed names byte-identical.
    """
    registry = capability_registry or load_default_method_capability_registry()
    multi = len({choice.segment_id for choice in method_choices}) > 1
    by_segment: dict[str, list[str]] = {}
    ev_segments: set[str] = set()
    for choice in method_choices:
        capability = registry.get(choice.archetype, choice.method)
        prefix = f"{choice.segment_id}_" if multi else ""
        prototype = family_prototype(
            capability.execution_family, forecast_years, prefix
        )
        if prototype is None:
            raise GenericValuationPlanError(
                f"execution family {capability.execution_family} has no "
                "generic evaluator prototype"
            )
        # The keys are the evaluator's own declaration; a hand-kept template
        # could drift from the math it describes.
        by_segment.setdefault(choice.segment_id, []).extend(
            prototype.required_assumption_keys
        )
        if capability.output_kind == "enterprise_value":
            ev_segments.add(choice.segment_id)
    for segment_id, keys in by_segment.items():
        prefix = f"{segment_id}_" if multi else ""
        keys.append(f"{prefix}{OWNERSHIP_KEY}")
        # The compiler refuses an EV-to-equity adjustment binding on an
        # equity-output evaluator (it would double-bridge), so the adjustment
        # key is demanded exactly where some chosen method emits enterprise
        # value.
        if segment_id in ev_segments:
            keys.append(f"{prefix}{EV_ADJUSTMENT_KEY}")
    first_segment = method_choices[0].segment_id
    by_segment[first_segment].append(DILUTED_SHARES_KEY)
    return {
        segment_id: tuple(dict.fromkeys(keys))
        for segment_id, keys in by_segment.items()
    }


def required_assumption_keys(
    *,
    method_choices: tuple[SegmentMethodChoice, ...],
    forecast_years: int,
    capability_registry: MethodCapabilityRegistry | None = None,
) -> tuple[str, ...]:
    """The union of every segment's demanded keys, in declaration order."""
    by_segment = required_assumption_keys_by_segment(
        method_choices=method_choices,
        forecast_years=forecast_years,
        capability_registry=capability_registry,
    )
    keys: list[str] = []
    for choice in method_choices:
        keys.extend(by_segment[choice.segment_id])
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
    industry_series_registry_path: str | Path = DEFAULT_SERIES_REGISTRY_PATH
    freshness_max_age_days: int = 120
    #: Optional per-run operator inputs. The underwriting file carries the
    #: analyst's declared judgments (ANALYST_UNDERWRITING layer, rationale
    #: required); market/street paths feed the post-freeze comparison stages
    #: and never exist pre-freeze in the providers that build intrinsic value.
    declared_underwriting_path: str | Path | None = None
    #: Operator-declared risk pack (L1→L4 Beta peers, ECOS risk-free rate,
    #: Damodaran ERP/CRP, marginal-debt benchmark). Required for any method
    #: whose execution family needs a Beta/WACC; without it those stages stay
    #: honestly NOT_IMPLEMENTED and only the beta-free families can complete.
    declared_risk_path: str | Path | None = None
    #: Operator-declared reportable-segment map (declarations/segments.yaml).
    #: Which segments exist is evidence — the IFRS 8 note names them — but
    #: what each one economically *is* cannot come from the company-level
    #: KSIC code, so every disclosed segment carries a declared classification
    #: here. Required for any issuer whose filing discloses multiple
    #: reportable segments; forbidden for one that reports itself whole.
    declared_segments_path: str | Path | None = None
    #: Extra evidence metrics this run requires beyond the method's assumption
    #: keys — the door multi-scenario runs use for scenario-qualified inputs
    #: (down_normalized_ebitda, bull_normalized_multiple, …): declaring them
    #: here routes them through the collection plan so the underwriting
    #: collector may serve them and coverage still fails closed when absent.
    extra_required_evidence: tuple[str, ...] = ()
    #: The probability route: a loader returning a sealed calibration snapshot
    #: (e.g. the continuous financial-path snapshot the artifact factory's
    #: output produces) plus the cohort it must belong to. When set, the
    #: SCENARIO_BUILD chain verifies the certificate and binds the snapshot's
    #: probabilities to the scenarios — the only door to numeric weighting.
    calibration_snapshot_loader: object | None = None
    calibration_cohort_key: str | None = None
    external_probability_source: str | None = None
    market_config_path: str | Path | None = None
    street_export_path: str | Path | None = None
    market_currency: str | None = None

    def validate(self) -> None:
        if not self.as_of or not self.scenario_ids or not self.method_choices:
            raise GenericValuationPlanError(
                "generic runtime spec requires as_of, scenario_ids and method_choices"
            )
        if self.calibration_snapshot_loader is not None and not (
            self.calibration_cohort_key and self.external_probability_source
        ):
            raise GenericValuationPlanError(
                "a calibration snapshot loader requires calibration_cohort_key "
                "and external_probability_source"
            )
        if self.market_config_path is not None and not self.market_currency:
            raise GenericValuationPlanError(
                "market_currency is required with a market config"
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
    capability_registry = (
        capability_registry or load_default_method_capability_registry()
    )
    classification = load_kr_industry_classification(spec.classification_map_path)
    declared_segments = None
    if spec.declared_segments_path is not None:
        from .declared_segments import load_declared_segments

        declared_segments = load_declared_segments(spec.declared_segments_path)
    profile_fetcher = CachedCompanyProfileFetcher(
        fetch_text=network.fetch_text,
        api_key=network.api_key,
    )
    keys_by_segment = required_assumption_keys_by_segment(
        method_choices=spec.method_choices,
        forecast_years=spec.forecast_years,
        capability_registry=capability_registry,
    )
    keys = tuple(
        dict.fromkeys(
            key
            for choice in spec.method_choices
            for key in keys_by_segment[choice.segment_id]
        )
    )
    multi_segment = len(keys_by_segment) > 1
    if multi_segment and spec.filing.segment_id not in keys_by_segment:
        raise GenericValuationPlanError(
            f"filing.segment_id {spec.filing.segment_id!r} is not one of the "
            "declared method segments; the filing KPI extraction and "
            "company-level evidence must bind to a real segment"
        )
    extensions = KRLiveProviderExtensions(
        additional_collectors=(
            filing_kpi_collector_provider(
                network,
                as_of=spec.as_of,
                segment_id=spec.filing.segment_id,
                transport=transport,
            ),
            *industry_series_collector_providers(
                network.fetch_text,
                as_of=spec.as_of,
                segment_id=spec.filing.segment_id,
                registry_path=spec.industry_series_registry_path,
            ),
            *(
                (
                    declared_underwriting_provider(
                        spec.declared_underwriting_path,
                        run_as_of=spec.as_of,
                    ),
                )
                if spec.declared_underwriting_path is not None
                else ()
            ),
        ),
        industry_snapshot_loader=opendart_filing_snapshot_loader(
            fetch_text=network.fetch_text,
            fetch_bytes=network.fetch_bytes,
            as_of=spec.as_of,
            api_key=network.api_key,
            declared_segments=declared_segments,
        ),
        freshness_loader=filing_cadence_freshness_loader(
            as_of=spec.as_of,
            max_age_days=spec.freshness_max_age_days,
        ),
        segment_decomposer=classified_segment_decomposer(
            profile_fetcher=profile_fetcher,
            classification=classification,
            declared_segments=declared_segments,
        ),
        industry_dna_router=classified_industry_dna_router(
            profile_fetcher=profile_fetcher,
            classification=classification,
            declared_segments=declared_segments,
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
            segment_scoped_keys=multi_segment,
            ev_adjustment_segments=frozenset(
                choice.segment_id
                for choice in spec.method_choices
                if capability_registry.get(
                    choice.archetype, choice.method
                ).output_kind == "enterprise_value"
            ),
        ),
        # An archetype that registers a Warranted-PER cross-check makes the
        # method intent demand a DCF fingerprint and a PER applicability
        # answer. The generic run answers honestly: the fingerprint is derived
        # deterministically from the compiled scenario (backlog family), and
        # PER itself is withheld — NOT_APPLICABLE with its reason — because no
        # authorized Economic-Twin residual PER pack exists in a cold start.
        per_loader=withheld_per_loader(),
    )
    method_registry = capability_registry
    families = {
        method_registry.get(choice.archetype, choice.method).execution_family
        for choice in spec.method_choices
    }
    if "contracted_backlog_dcf" in families:
        extensions = replace(
            extensions,
            dcf_fingerprint_loader=generic_backlog_dcf_fingerprint_loader(
                scenario_id=spec.scenario_ids[0],
                forecast_years=spec.forecast_years,
            ),
        )
    if spec.market_config_path is not None:
        from .workflow import market_loader_from_config

        declared_market_loader = market_loader_from_config(spec.market_config_path)
        run_cutoff = date.fromisoformat(spec.as_of[:10])

        def cutoff_market_loader():
            # This wrapper is invoked only by the post-freeze market stage. It
            # preserves price isolation while refusing to admit a quote from
            # beyond the intrinsic run's knowledge-time boundary.
            market_observation = declared_market_loader()
            market_date = date.fromisoformat(market_observation.as_of[:10])
            if market_date > run_cutoff:
                raise GenericValuationPlanError(
                    f"market observation {market_date.isoformat()} is after run cutoff "
                    f"{run_cutoff.isoformat()}; future post-freeze price is inadmissible"
                )
            return market_observation

        extensions = replace(
            extensions,
            market_loader=cutoff_market_loader,
        )
    if spec.street_export_path is not None:
        from .official_market_data import street_loader_from_authorized_export

        declared_street_loader = street_loader_from_authorized_export(
            spec.street_export_path
        )
        run_cutoff = date.fromisoformat(spec.as_of[:10])

        def cutoff_street_loader():
            # Street remains inaccessible until its post-freeze stage. At that
            # boundary, reject any report the intrinsic cutoff could not know.
            reports = declared_street_loader()
            future_dates = tuple(
                sorted(
                    {
                        report.published_date[:10]
                        for report in reports
                        if date.fromisoformat(report.published_date[:10]) > run_cutoff
                    }
                )
            )
            if future_dates:
                raise GenericValuationPlanError(
                    "Street report publication date is after run cutoff "
                    f"{run_cutoff.isoformat()}: {', '.join(future_dates)}"
                )
            return reports

        extensions = replace(
            extensions,
            street_loader=cutoff_street_loader,
        )
    if spec.calibration_snapshot_loader is not None:
        extensions = replace(
            extensions,
            calibration_loader=spec.calibration_snapshot_loader,
        )
    if spec.declared_risk_path is not None:
        declared_risk = load_declared_risk_pack(
            spec.declared_risk_path, run_as_of=spec.as_of
        )
        extensions = replace(
            extensions,
            additional_collectors=(
                *extensions.additional_collectors,
                declared_risk_provider(
                    declared_risk, segment_id=spec.filing.segment_id
                ),
            ),
            beta_loader=declared_risk_beta_loader(declared_risk),
            wacc_loader=declared_risk_wacc_loader(declared_risk),
        )
    # The valuation assumption keys must exist as Evidence before the Bridge can
    # cite them. Keys the filing/series collectors do not produce arrive as the
    # operator's declared underwriting, so they are declared here as additional
    # required evidence for the core segment. A declared risk pack additionally
    # requires its four peer-selection judgments in the ledger, so the Beta
    # stage's evidence-ID validation has real records to bind to.
    additional_required: dict[str, tuple[str, ...]] = {
        segment_id: segment_keys
        for segment_id, segment_keys in keys_by_segment.items()
    }
    # Extras route to the segment whose namespace prefixes them (multi-segment
    # scenario variants like steel_down_fcff_year_1); anything unprefixed —
    # every single-segment extra — binds to the filing segment as before.
    for extra in spec.extra_required_evidence:
        owner = next(
            (
                segment_id
                for segment_id in keys_by_segment
                if multi_segment and extra.startswith(f"{segment_id}_")
            ),
            spec.filing.segment_id,
        )
        additional_required[owner] = tuple(
            dict.fromkeys((*additional_required[owner], extra))
        )
    if spec.declared_risk_path is not None:
        # The risk pack is company-level; its peer-selection judgments bind to
        # the filing segment's ledger.
        additional_required[spec.filing.segment_id] = tuple(
            dict.fromkeys(
                (
                    *additional_required[spec.filing.segment_id],
                    *BETA_SELECTION_METRICS,
                )
            )
        )
    return KRLiveRuntimeFactory(
        network=network,
        filing=spec.filing,
        extensions=extensions,
        additional_required_evidence=additional_required,
        market_currency=spec.market_currency,
        scenario_binding_spec=ScenarioBindingSpec(
            scenario_ids=spec.scenario_ids,
            required_keys=keys,
            calibration_cohort_key=spec.calibration_cohort_key,
            external_probability_source=spec.external_probability_source,
        ),
        method_choices=spec.method_choices,
        capability_registry=capability_registry,
    )
