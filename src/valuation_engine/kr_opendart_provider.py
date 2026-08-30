from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from io import BytesIO
from pathlib import Path
from struct import error as StructError, unpack_from
from typing import Callable, Mapping
from zipfile import BadZipFile, ZipFile

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
    SegmentDescriptor,
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
_DEFAULT_MAX_CORP_ARCHIVE_MEMBERS = 8
_DEFAULT_MAX_CORP_ARCHIVE_UNCOMPRESSED_BYTES = 32_000_000
_ARCHIVE_READ_CHUNK_BYTES = 64 * 1024
_EOCD_SIGNATURE = b"PK\x05\x06"
_CENTRAL_DIRECTORY_SIGNATURE = b"PK\x01\x02"
_EOCD_FIXED_SIZE = 22
_MAX_ZIP_COMMENT_BYTES = 65_535
_CENTRAL_DIRECTORY_FIXED_SIZE = 46
_ZIP16_SENTINEL = 0xFFFF
_ZIP32_SENTINEL = 0xFFFFFFFF

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


def _locate_eocd(
    payload: bytes,
) -> tuple[int, int, int, int]:
    if len(payload) < _EOCD_FIXED_SIZE:
        raise ValueError("OpenDART corp-code archive is shorter than a ZIP EOCD")
    search_start = max(
        0,
        len(payload) - (_EOCD_FIXED_SIZE + _MAX_ZIP_COMMENT_BYTES),
    )
    search_end = len(payload)
    while search_end > search_start:
        offset = payload.rfind(
            _EOCD_SIGNATURE,
            search_start,
            search_end,
        )
        if offset < 0:
            break
        if offset + _EOCD_FIXED_SIZE <= len(payload):
            try:
                (
                    signature,
                    disk_number,
                    central_directory_disk,
                    entries_on_disk,
                    total_entries,
                    central_directory_size,
                    central_directory_offset,
                    comment_length,
                ) = unpack_from("<4s4H2LH", payload, offset)
            except StructError:
                pass
            else:
                expected_end = offset + _EOCD_FIXED_SIZE + comment_length
                if signature == _EOCD_SIGNATURE and expected_end == len(payload):
                    if disk_number != 0 or central_directory_disk != 0:
                        raise ValueError(
                            "OpenDART corp-code archive must not span multiple disks"
                        )
                    if entries_on_disk != total_entries:
                        raise ValueError(
                            "OpenDART corp-code archive has inconsistent entry counts"
                        )
                    if (
                        total_entries == _ZIP16_SENTINEL
                        or central_directory_size == _ZIP32_SENTINEL
                        or central_directory_offset == _ZIP32_SENTINEL
                    ):
                        raise ValueError(
                            "OpenDART corp-code archive must not use ZIP64 metadata"
                        )
                    if total_entries <= 0:
                        raise ValueError(
                            "OpenDART corp-code archive contains no members"
                        )
                    return (
                        offset,
                        total_entries,
                        central_directory_offset,
                        central_directory_size,
                    )
        search_end = offset
    raise ValueError("OpenDART corp-code archive has no valid ZIP EOCD")


def _preflight_central_directory(
    payload: bytes,
    *,
    max_members: int,
) -> int:
    (
        eocd_offset,
        total_entries,
        central_directory_offset,
        central_directory_size,
    ) = _locate_eocd(payload)
    if total_entries > max_members:
        raise ValueError(
            "OpenDART corp-code archive exceeds the configured member limit"
        )
    central_directory_end = (
        central_directory_offset + central_directory_size
    )
    if (
        central_directory_offset < 0
        or central_directory_size < 0
        or central_directory_end != eocd_offset
        or central_directory_end > len(payload)
    ):
        raise ValueError(
            "OpenDART corp-code archive has invalid central-directory bounds"
        )

    cursor = central_directory_offset
    parsed_entries = 0
    while cursor < central_directory_end:
        if (
            central_directory_end - cursor
            < _CENTRAL_DIRECTORY_FIXED_SIZE
        ):
            raise ValueError(
                "OpenDART corp-code central directory is truncated"
            )
        if (
            payload[cursor : cursor + 4]
            != _CENTRAL_DIRECTORY_SIGNATURE
        ):
            raise ValueError(
                "OpenDART corp-code central directory has an invalid entry signature"
            )
        try:
            compressed_size = unpack_from("<L", payload, cursor + 20)[0]
            uncompressed_size = unpack_from("<L", payload, cursor + 24)[0]
            (
                filename_length,
                extra_length,
                comment_length,
            ) = unpack_from("<HHH", payload, cursor + 28)
            disk_start = unpack_from("<H", payload, cursor + 34)[0]
            local_header_offset = unpack_from("<L", payload, cursor + 42)[0]
        except StructError as exc:
            raise ValueError(
                "OpenDART corp-code central directory is malformed"
            ) from exc
        if disk_start != 0:
            raise ValueError(
                "OpenDART corp-code archive must not span multiple disks"
            )
        if (
            compressed_size == _ZIP32_SENTINEL
            or uncompressed_size == _ZIP32_SENTINEL
            or local_header_offset == _ZIP32_SENTINEL
        ):
            raise ValueError(
                "OpenDART corp-code archive must not use ZIP64 entries"
            )
        entry_size = (
            _CENTRAL_DIRECTORY_FIXED_SIZE
            + filename_length
            + extra_length
            + comment_length
        )
        if entry_size > central_directory_end - cursor:
            raise ValueError(
                "OpenDART corp-code central-directory entry exceeds its bounds"
            )
        parsed_entries += 1
        if parsed_entries > max_members:
            raise ValueError(
                "OpenDART corp-code archive exceeds the configured member limit"
            )
        cursor += entry_size

    if cursor != central_directory_end or parsed_entries != total_entries:
        raise ValueError(
            "OpenDART corp-code central-directory count does not match its EOCD"
        )
    return parsed_entries


def _validated_corp_archive_payload(
    payload: bytes,
    *,
    max_members: int,
    max_uncompressed_bytes: int,
) -> bytes:
    """Validate a corp-code ZIP before the canonical resolver decompresses it.

    The HTTP transport bounds only the compressed response body. EOCD and central-directory
    metadata are therefore preflighted directly from immutable bytes before Python constructs
    any ``ZipInfo`` objects. The archive is then checked again for declared and streamed
    uncompressed size before the same payload is passed to the canonical resolver.
    """
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("OpenDART corp-code archive must be non-empty bytes")
    if max_members <= 0 or max_uncompressed_bytes <= 0:
        raise ValueError("OpenDART corp archive limits must be positive")
    _preflight_central_directory(
        payload,
        max_members=max_members,
    )
    try:
        with ZipFile(BytesIO(payload)) as archive:
            members = archive.infolist()
            if not members:
                raise ValueError(
                    "OpenDART corp-code archive contains no members"
                )
            if len(members) > max_members:
                raise ValueError(
                    "OpenDART corp-code archive exceeds the configured member limit"
                )
            declared_total = sum(
                max(0, member.file_size) for member in members
            )
            if declared_total > max_uncompressed_bytes:
                raise ValueError(
                    "OpenDART corp-code archive exceeds the configured uncompressed-size limit"
                )
            xml_members = tuple(
                member
                for member in members
                if member.filename.lower().endswith(".xml")
            )
            if len(xml_members) != 1:
                raise ValueError(
                    "OpenDART corp-code archive must contain exactly one XML member"
                )
            xml_member = xml_members[0]
            if xml_member.flag_bits & 0x1:
                raise ValueError(
                    "OpenDART corp-code XML member must not be encrypted"
                )
            streamed_bytes = 0
            with archive.open(xml_member, "r") as source:
                while True:
                    remaining = (
                        max_uncompressed_bytes - streamed_bytes
                    )
                    chunk = source.read(
                        min(
                            _ARCHIVE_READ_CHUNK_BYTES,
                            remaining + 1,
                        )
                    )
                    if not chunk:
                        break
                    streamed_bytes += len(chunk)
                    if streamed_bytes > max_uncompressed_bytes:
                        raise ValueError(
                            "OpenDART corp-code XML exceeds the configured uncompressed-size limit"
                        )
    except BadZipFile as exc:
        raise ValueError(
            "OpenDART corp-code response is not a valid ZIP archive"
        ) from exc
    except RuntimeError as exc:
        raise ValueError(
            "OpenDART corp-code archive cannot be opened safely"
        ) from exc
    return payload


def _single_segment_decomposer(
    decomposer: SegmentDecomposer,
    *,
    expected_segment_id: str,
) -> SegmentDecomposer:
    if not callable(decomposer):
        raise TypeError("segment_decomposer must be callable")
    if not expected_segment_id:
        raise ValueError("expected_segment_id is required")

    def decompose(identity, snapshot) -> tuple[SegmentDescriptor, ...]:
        try:
            segments = tuple(decomposer(identity, snapshot))
        except TypeError as exc:
            raise TypeError(
                "KR OpenDART segment_decomposer must return an iterable of SegmentDescriptor"
            ) from exc
        if len(segments) != 1:
            raise ValueError(
                "KR OpenDART provider foundation supports exactly one segment; "
                "multi-segment companies require note-scoped collectors"
            )
        segment = segments[0]
        if not isinstance(segment, SegmentDescriptor):
            raise TypeError(
                "KR OpenDART segment_decomposer must return SegmentDescriptor"
            )
        if segment.segment_id != expected_segment_id:
            raise ValueError(
                "KR OpenDART segment ID does not match the filing collector scope: "
                f"expected {expected_segment_id}, got {segment.segment_id}"
            )
        return segments

    return decompose


@dataclass(frozen=True)
class OpenDartNetwork:
    fetch_text: FetchText
    fetch_bytes: FetchBytes
    api_key: str | None = field(default=None, repr=False)
    max_corp_archive_members: int = _DEFAULT_MAX_CORP_ARCHIVE_MEMBERS
    max_corp_archive_uncompressed_bytes: int = (
        _DEFAULT_MAX_CORP_ARCHIVE_UNCOMPRESSED_BYTES
    )

    def validate(self) -> None:
        if not callable(self.fetch_text) or not callable(self.fetch_bytes):
            raise TypeError(
                "OpenDartNetwork requires callable text and binary transports"
            )
        if self.api_key is not None:
            if not isinstance(self.api_key, str) or not self.api_key.strip():
                raise ValueError(
                    "OpenDART api_key must be a non-blank string"
                )
        if self.max_corp_archive_members <= 0:
            raise ValueError(
                "max_corp_archive_members must be positive"
            )
        if self.max_corp_archive_uncompressed_bytes <= 0:
            raise ValueError(
                "max_corp_archive_uncompressed_bytes must be positive"
            )

    def fetch_validated_corp_archive(self, url: str) -> bytes:
        self.validate()
        return _validated_corp_archive_payload(
            self.fetch_bytes(url),
            max_members=self.max_corp_archive_members,
            max_uncompressed_bytes=(
                self.max_corp_archive_uncompressed_bytes
            ),
        )

    @classmethod
    def from_http_transport(
        cls,
        transport: HttpTransport,
        *,
        api_key: str | None = None,
        max_corp_archive_members: int = (
            _DEFAULT_MAX_CORP_ARCHIVE_MEMBERS
        ),
        max_corp_archive_uncompressed_bytes: int = (
            _DEFAULT_MAX_CORP_ARCHIVE_UNCOMPRESSED_BYTES
        ),
    ) -> "OpenDartNetwork":
        if not isinstance(transport, HttpTransport):
            raise TypeError("transport must be HttpTransport")
        return cls(
            fetch_text=lambda url: transport.get_text(url).text,
            fetch_bytes=lambda url: transport.get_bytes(url).content,
            api_key=api_key,
            max_corp_archive_members=max_corp_archive_members,
            max_corp_archive_uncompressed_bytes=(
                max_corp_archive_uncompressed_bytes
            ),
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
            raise ValueError(
                "fiscal_period_end cannot be after checked_at"
            )
        if not self.segment_id or not self.collector_id:
            raise ValueError(
                "OpenDART filing selection requires segment and collector IDs"
            )
        if self.source_id != _OPENDART_SOURCE_ID:
            raise ValueError(
                "KR OpenDART provider must use canonical source_id KR_OPENDART"
            )
        if not self.specs:
            raise ValueError(
                "OpenDART filing selection requires at least one metric spec"
            )
        metrics: list[str] = []
        for spec in self.specs:
            spec.validate()
            metrics.append(spec.metric)
        if len(metrics) != len(set(metrics)):
            raise ValueError(
                "OpenDART filing selection has duplicate metric specs"
            )
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
            raise ValueError(
                "OpenDART collector task contains no supported metric"
            )
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
    additional_required_evidence: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )
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
        if (
            self.extensions.market_loader is not None
            and not self.market_currency
        ):
            raise ValueError(
                "market_currency is required with a market loader"
            )

    def __call__(
        self,
        request: LiveAnalysisRequest,
    ) -> LivePrimaryRuntimeConfig:
        if not isinstance(request, LiveAnalysisRequest):
            raise TypeError(
                "KRLiveRuntimeFactory requires LiveAnalysisRequest"
            )
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
            self.network.fetch_validated_corp_archive,
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
        scoped_extensions = replace(
            self.extensions,
            segment_decomposer=_single_segment_decomposer(
                self.extensions.segment_decomposer,
                expected_segment_id=self.filing.segment_id,
            ),
        )
        providers = scoped_extensions.build_providers(
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
            additional_required_evidence=dict(self.additional_required_evidence),
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
        raise ValueError(
            "target_id is not a KR OpenDART identity"
        )
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
        corp_code = opendart_corp_code_from_target_id(
            request.target_id
        )
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
        emitted_metrics = {
            record.metric for record in batch.records
        }
        unauthorized = tuple(
            sorted(
                emitted_metrics - set(request.required_metrics)
            )
        )
        if unauthorized:
            raise ValueError(
                "OpenDART collector emitted metrics outside the current task: "
                + ", ".join(unauthorized)
            )
        return batch

    return collect
