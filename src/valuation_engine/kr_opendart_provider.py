from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Mapping

from .cli_runtime import LiveAnalysisRequest
from .collection_plan import CollectorCapability, normalize_jurisdiction
from .dart_facts import (
    DEFAULT_CORE_FACT_SPECS,
    DartFactMetricSpec,
    build_opendart_full_financials_url,
    live_opendart_fact_collector,
)
from .dcf_evaluators import RegistryLoader
from .evidence_collection import (
    EvidenceCollectionBatch,
    EvidenceCollectionRequest,
    EvidenceCollector,
)
from .funding_adapter import FundingScanner
from .impact_adapter import GenericDecisionImpactConfig
from .live_indexers import HttpTransport
from .live_primary_adapters import (
    CompanyResolutionRequest,
    CompanyResolver,
    FreshnessLoader,
    IndustryDNARouter,
    IndustrySnapshotLoader,
    SegmentDecomposer,
    live_opendart_company_resolver,
)
from .live_runtime import (
    LiveCollectorProvider,
    LivePrimaryProviders,
    LivePrimaryRuntimeConfig,
    ValuationPlanInputsLoader,
)
from .llm_staff import BridgeAnalyst, IntelligenceOfficer, RedTeamOfficer
from .method_capabilities import MethodCapabilityRegistry
from .orchestrator import StageAdapter
from .per_adapters import PERInputsLoader
from .post_freeze_adapters import MarketLoader, StreetLoader
from .probability_adapter import CalibrationSnapshotLoader
from .risk_adapters import BetaUniverseLoader, WACCInputsLoader
from .runtime_support_adapters import DCFConsistencyFingerprintLoader
from .scanner_runtime import ScannerRunner
from .scenario_binding import ScenarioBindingSpec
from .valuation_plan_compiler import SegmentMethodChoice


_KR_JURISDICTION = "KR"
_OPENDART_SOURCE_ID = "KR_OPENDART"
_OPENDART_TARGET_PREFIX = "KR:DART:"

FetchText = Callable[[str], str]
FetchBytes = Callable[[str], bytes]

__all__ = [
    "OpenDartNetwork",
    "OpenDartFilingSelection",
    "KRLiveProviderExtensions",
    "KRLiveRuntimeFactory",
    "opendart_corp_code_from_target_id",
    "request_scoped_opendart_fact_collector",
]


@dataclass(frozen=True)
class OpenDartNetwork:
    fetch_text: FetchText
    fetch_bytes: FetchBytes
    api_key: str | None = field(default=None, repr=False)

    def validate(self) -> None:
        if not callable(self.fetch_text) or not callable(self.fetch_bytes):
            raise TypeError(
                "OpenDartNetwork requires callable text and binary transports"
            )
        if self.api_key is not None:
            if not isinstance(self.api_key, str) or not self.api_key.strip():
                raise ValueError("OpenDART api_key must be a non-blank string")

    @classmethod
    def from_http_transport(
        cls,
        transport: HttpTransport,
        *,
        api_key: str | None = None,
    ) -> "OpenDartNetwork":
        if not isinstance(transport, HttpTransport):
            raise TypeError("transport must be HttpTransport")
        return cls(
            fetch_text=lambda url: transport.get_text(url).text,
            fetch_bytes=lambda url: transport.get_bytes(url).content,
            api_key=api_key,
        )


@dataclass(frozen=True)
class OpenDartFilingSelection:
    business_year: str
    report_code: str
    fiscal_period_end: str
    checked_at: str
    fs_div: str = "CFS"
    specs: tuple[DartFactMetricSpec, ...] = DEFAULT_CORE_FACT_SPECS
    segment_id: str = "company"
    source_id: str = _OPENDART_SOURCE_ID
    collector_id: str = "kr-opendart-core-financials"

    def validate(self) -> None:
        checked = date.fromisoformat(self.checked_at[:10])
        period_end = date.fromisoformat(self.fiscal_period_end)
        if period_end > checked:
            raise ValueError("fiscal_period_end cannot be after checked_at")
        if not self.segment_id or not self.collector_id:
            raise ValueError(
                "OpenDART filing selection requires segment and collector IDs"
            )
        if self.source_id != _OPENDART_SOURCE_ID:
            raise ValueError(
                "KR OpenDART provider must use canonical source_id KR_OPENDART"
            )
        if not self.specs:
            raise ValueError("OpenDART filing selection requires at least one metric spec")
        metrics: list[str] = []
        for spec in self.specs:
            spec.validate()
            metrics.append(spec.metric)
        if len(metrics) != len(set(metrics)):
            raise ValueError("OpenDART filing selection has duplicate metric specs")
        # Reuse the endpoint contract as the canonical validation for year/report/fs_div.
        build_opendart_full_financials_url(
            corp_code="00000000",
            business_year=self.business_year,
            report_code=self.report_code,
            fs_div=self.fs_div,
            api_key="VALIDATION_ONLY",
        )

    @property
    def supported_metrics(self) -> tuple[str, ...]:
        return tuple(spec.metric for spec in self.specs)

    def specs_for(
        self,
        metrics: tuple[str, ...],
    ) -> tuple[DartFactMetricSpec, ...]:
        requested = set(metrics)
        supported = set(self.supported_metrics)
        unsupported = tuple(sorted(requested - supported))
        if unsupported:
            raise ValueError(
                "OpenDART collector received metrics outside its declared capability: "
                + ", ".join(unsupported)
            )
        selected = tuple(
            spec for spec in self.specs if spec.metric in requested
        )
        if not selected:
            raise ValueError("OpenDART collector task contains no supported metric")
        return selected


@dataclass(frozen=True)
class KRLiveProviderExtensions:
    industry_snapshot_loader: IndustrySnapshotLoader
    freshness_loader: FreshnessLoader
    segment_decomposer: SegmentDecomposer
    industry_dna_router: IndustryDNARouter
    scanner_runners: Mapping[str, ScannerRunner]
    intelligence_officer: IntelligenceOfficer
    red_team_officer: RedTeamOfficer
    bridge_analyst: BridgeAnalyst
    evaluator_registry_loader: RegistryLoader
    valuation_plan_inputs_loader: ValuationPlanInputsLoader
    additional_collectors: tuple[LiveCollectorProvider, ...] = ()
    funding_scanner: FundingScanner | None = None
    research_recovery_adapter: StageAdapter | None = None
    beta_loader: BetaUniverseLoader | None = None
    wacc_loader: WACCInputsLoader | None = None
    dcf_fingerprint_loader: DCFConsistencyFingerprintLoader | None = None
    per_loader: PERInputsLoader | None = None
    calibration_loader: CalibrationSnapshotLoader | None = None
    street_loader: StreetLoader | None = None
    market_loader: MarketLoader | None = None

    def build_providers(
        self,
        *,
        company_resolver: CompanyResolver,
        core_collector: LiveCollectorProvider,
    ) -> LivePrimaryProviders:
        providers = LivePrimaryProviders(
            company_resolver=company_resolver,
            industry_snapshot_loader=self.industry_snapshot_loader,
            freshness_loader=self.freshness_loader,
            segment_decomposer=self.segment_decomposer,
            industry_dna_router=self.industry_dna_router,
            collectors=(core_collector, *self.additional_collectors),
            scanner_runners=dict(self.scanner_runners),
            intelligence_officer=self.intelligence_officer,
            red_team_officer=self.red_team_officer,
            bridge_analyst=self.bridge_analyst,
            evaluator_registry_loader=self.evaluator_registry_loader,
            valuation_plan_inputs_loader=self.valuation_plan_inputs_loader,
            funding_scanner=self.funding_scanner,
            research_recovery_adapter=self.research_recovery_adapter,
            beta_loader=self.beta_loader,
            wacc_loader=self.wacc_loader,
            dcf_fingerprint_loader=self.dcf_fingerprint_loader,
            per_loader=self.per_loader,
            calibration_loader=self.calibration_loader,
            street_loader=self.street_loader,
            market_loader=self.market_loader,
        )
        providers.validate()
        return providers


@dataclass(frozen=True)
class KRLiveRuntimeFactory:
    network: OpenDartNetwork
    filing: OpenDartFilingSelection
    extensions: KRLiveProviderExtensions
    scenario_binding_spec: ScenarioBindingSpec
    method_choices: tuple[SegmentMethodChoice, ...] = ()
    market_currency: str | None = None
    capability_registry: MethodCapabilityRegistry | None = None
    impact_config: GenericDecisionImpactConfig | None = None
    initial_data: Mapping[str, object] = field(default_factory=dict)

    def validate(self) -> None:
        self.network.validate()
        self.filing.validate()
        self.scenario_binding_spec.validate()
        for choice in self.method_choices:
            choice.validate()
        if self.extensions.market_loader is not None and not self.market_currency:
            raise ValueError("market_currency is required with a market loader")

    def __call__(
        self,
        request: LiveAnalysisRequest,
    ) -> LivePrimaryRuntimeConfig:
        if not isinstance(request, LiveAnalysisRequest):
            raise TypeError("KRLiveRuntimeFactory requires LiveAnalysisRequest")
        request.validate()
        self.validate()
        jurisdiction = normalize_jurisdiction(
            request.jurisdiction or _KR_JURISDICTION
        )
        if jurisdiction != _KR_JURISDICTION:
            raise ValueError(
                "KR OpenDART provider factory supports Korean companies only"
            )

        resolver = live_opendart_company_resolver(
            self.network.fetch_bytes,
            api_key=self.network.api_key,
        )
        collector = LiveCollectorProvider(
            capability=CollectorCapability(
                collector_id=self.filing.collector_id,
                source_id=self.filing.source_id,
                supported_metrics=self.filing.supported_metrics,
                jurisdictions=(_KR_JURISDICTION,),
                implementation_ref=(
                    "valuation_engine.kr_opendart_provider."
                    "request_scoped_opendart_fact_collector"
                ),
            ),
            collector=request_scoped_opendart_fact_collector(
                self.network,
                self.filing,
            ),
        )
        providers = self.extensions.build_providers(
            company_resolver=resolver,
            core_collector=collector,
        )
        config = LivePrimaryRuntimeConfig(
            run_id=request.run_id,
            state_root=Path(request.state_root),
            company_request=CompanyResolutionRequest(
                request.company_query,
                _KR_JURISDICTION,
            ),
            scenario_binding_spec=self.scenario_binding_spec,
            providers=providers,
            method_choices=self.method_choices,
            market_currency=self.market_currency,
            capability_registry=self.capability_registry,
            impact_config=self.impact_config,
            initial_data=dict(self.initial_data),
        )
        config.validate()
        return config


def opendart_corp_code_from_target_id(target_id: str) -> str:
    if not isinstance(target_id, str) or not target_id.startswith(
        _OPENDART_TARGET_PREFIX
    ):
        raise ValueError("target_id is not a KR OpenDART identity")
    corp_code = target_id[len(_OPENDART_TARGET_PREFIX) :]
    if len(corp_code) != 8 or not corp_code.isdigit():
        raise ValueError(
            "KR OpenDART target_id must end with an 8-digit corp_code"
        )
    return corp_code


def request_scoped_opendart_fact_collector(
    network: OpenDartNetwork,
    filing: OpenDartFilingSelection,
) -> EvidenceCollector:
    network.validate()
    filing.validate()

    def collect(
        request: EvidenceCollectionRequest,
    ) -> EvidenceCollectionBatch:
        corp_code = opendart_corp_code_from_target_id(request.target_id)
        selected_specs = filing.specs_for(request.required_metrics)
        collector = live_opendart_fact_collector(
            network.fetch_text,
            source_id=filing.source_id,
            checked_at=filing.checked_at,
            corp_code=corp_code,
            business_year=filing.business_year,
            report_code=filing.report_code,
            fiscal_period_end=filing.fiscal_period_end,
            fs_div=filing.fs_div,
            api_key=network.api_key,
            specs=selected_specs,
            segment=filing.segment_id,
        )
        batch = collector(request)
        emitted_metrics = {record.metric for record in batch.records}
        unauthorized = tuple(
            sorted(emitted_metrics - set(request.required_metrics))
        )
        if unauthorized:
            raise ValueError(
                "OpenDART collector emitted metrics outside the current task: "
                + ", ".join(unauthorized)
            )
        return batch

    return collect
