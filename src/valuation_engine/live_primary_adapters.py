from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from io import BytesIO
from typing import Callable
from urllib.parse import urlencode
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .control_plane import StageStatus
from .industry_dna import IndustryDNAProfile
from .live_indexers import require_env_credential
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .source_watch import WatchFinding, WatchStatus, requires_revalidation


@dataclass(frozen=True)
class CompanyResolutionRequest:
    query: str
    jurisdiction: str | None = None

    def validate(self) -> None:
        if not self.query.strip():
            raise ValueError("company resolution query is required")


@dataclass(frozen=True)
class ResolvedCompanyIdentity:
    target_id: str
    legal_name: str
    ticker: str
    jurisdiction: str
    external_ids: tuple[tuple[str, str], ...]
    source_refs: tuple[str, ...]

    def validate(self) -> None:
        if not all((self.target_id, self.legal_name, self.jurisdiction)):
            raise ValueError("resolved company requires target_id, legal_name and jurisdiction")
        if not self.external_ids or not self.source_refs:
            raise ValueError("resolved company requires external IDs and source references")
        keys = tuple(key for key, value in self.external_ids if key and value)
        if len(keys) != len(self.external_ids) or len(keys) != len(set(keys)):
            raise ValueError("resolved company external IDs must be unique non-empty key/value pairs")


CompanyResolver = Callable[[CompanyResolutionRequest], ResolvedCompanyIdentity]


@dataclass(frozen=True)
class IndustryKnowledgeSnapshot:
    as_of: str
    source_ids: tuple[str, ...]
    document_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    content_hashes: tuple[str, ...]
    snapshot_hash: str

    def expected_hash(self) -> str:
        payload = "\n".join(
            (
                self.as_of,
                *sorted(self.source_ids),
                *sorted(self.document_ids),
                *sorted(self.evidence_ids),
                *sorted(self.content_hashes),
            )
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def validate(self) -> None:
        date.fromisoformat(self.as_of[:10])
        if not self.source_ids or not self.content_hashes:
            raise ValueError("industry snapshot requires source IDs and content hashes")
        if self.snapshot_hash != self.expected_hash():
            raise ValueError("industry snapshot hash mismatch")

    @classmethod
    def build(
        cls,
        *,
        as_of: str,
        source_ids: tuple[str, ...],
        document_ids: tuple[str, ...] = (),
        evidence_ids: tuple[str, ...] = (),
        content_hashes: tuple[str, ...],
    ) -> "IndustryKnowledgeSnapshot":
        provisional = cls(as_of, source_ids, document_ids, evidence_ids, content_hashes, "")
        return cls(as_of, source_ids, document_ids, evidence_ids, content_hashes, provisional.expected_hash())


IndustrySnapshotLoader = Callable[[ResolvedCompanyIdentity], IndustryKnowledgeSnapshot]


@dataclass(frozen=True)
class LiveFreshnessAssessment:
    checked_at: str
    findings: tuple[WatchFinding, ...]
    source_snapshot_hash: str

    def validate(self) -> None:
        date.fromisoformat(self.checked_at[:10])
        if not self.source_snapshot_hash:
            raise ValueError("freshness assessment requires source_snapshot_hash")
        if not self.findings:
            raise ValueError("freshness assessment requires at least one source finding")

    @property
    def blocking_findings(self) -> tuple[WatchFinding, ...]:
        return tuple(
            finding
            for finding in self.findings
            if finding.status is WatchStatus.SOURCE_FAILURE
            or finding.blocks_automatic_promotion
            or requires_revalidation(finding)
        )

    @property
    def warning_findings(self) -> tuple[WatchFinding, ...]:
        blocking = set(self.blocking_findings)
        return tuple(finding for finding in self.findings if finding not in blocking and finding.status is not WatchStatus.CLEAN)


FreshnessLoader = Callable[[ResolvedCompanyIdentity, IndustryKnowledgeSnapshot], LiveFreshnessAssessment]


@dataclass(frozen=True)
class SegmentDescriptor:
    segment_id: str
    name: str
    revenue_recognition: str
    price_formation: str
    asset_ownership: str
    capital_intensity: str
    regulation_intensity: str
    customer_structure: str
    reinvestment_model: str
    cashflow_duration: str
    evidence_ids: tuple[str, ...]

    def validate(self) -> None:
        fields = (
            self.segment_id,
            self.name,
            self.revenue_recognition,
            self.price_formation,
            self.asset_ownership,
            self.capital_intensity,
            self.regulation_intensity,
            self.customer_structure,
            self.reinvestment_model,
            self.cashflow_duration,
        )
        if any(not value.strip() for value in fields):
            raise ValueError("segment descriptor requires complete economic-structure fields")
        if not self.evidence_ids:
            raise ValueError(f"segment {self.segment_id} requires evidence IDs")


SegmentDecomposer = Callable[
    [ResolvedCompanyIdentity, IndustryKnowledgeSnapshot],
    tuple[SegmentDescriptor, ...],
]
IndustryDNARouter = Callable[
    [ResolvedCompanyIdentity, tuple[SegmentDescriptor, ...], IndustryKnowledgeSnapshot],
    tuple[IndustryDNAProfile, ...],
]


def live_company_resolution_adapter(
    *,
    resolver: CompanyResolver,
    request: CompanyResolutionRequest,
) -> StageAdapter:
    request.validate()

    def run(_: OrchestratorContext) -> StageExecutionResult:
        try:
            identity = resolver(request)
            identity.validate()
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                f"live company resolution failed: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "company identity resolved from a declared live resolver contract",
            {
                "resolved_company_identity": identity,
                "company": identity.legal_name,
                "ticker": identity.ticker,
                "target_id": identity.target_id,
                "jurisdiction": identity.jurisdiction,
                "company_external_ids": identity.external_ids,
                "company_resolution_source_refs": identity.source_refs,
            },
        )

    return run


def live_industry_snapshot_adapter(*, loader: IndustrySnapshotLoader) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        identity = context.data.get("resolved_company_identity")
        if not isinstance(identity, ResolvedCompanyIdentity):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "resolved company identity is required before loading Industry Knowledge",
                blocking=True,
            )
        try:
            snapshot = loader(identity)
            snapshot.validate()
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                f"live Industry Knowledge snapshot load failed: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "versioned Industry Knowledge snapshot loaded and hash-verified",
            {
                "industry_knowledge_snapshot": snapshot,
                "industry_snapshot_hash": snapshot.snapshot_hash,
                "industry_snapshot_source_ids": snapshot.source_ids,
                "industry_snapshot_evidence_ids": snapshot.evidence_ids,
            },
        )

    return run


def live_source_freshness_adapter(*, loader: FreshnessLoader) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        identity = context.data.get("resolved_company_identity")
        snapshot = context.data.get("industry_knowledge_snapshot")
        if not isinstance(identity, ResolvedCompanyIdentity) or not isinstance(snapshot, IndustryKnowledgeSnapshot):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "company identity and Industry Knowledge snapshot are required before freshness precheck",
                blocking=True,
            )
        try:
            assessment = loader(identity, snapshot)
            assessment.validate()
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                f"live source freshness check failed: {exc}",
                blocking=True,
            )
        if assessment.blocking_findings:
            detail = "; ".join(
                f"{item.series_id}:{item.status.value}:{item.reason}"
                for item in assessment.blocking_findings
            )
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "source changes must be incorporated/reviewed before valuation: " + detail,
                {
                    "source_freshness_assessment": assessment,
                    "source_snapshot_hash": assessment.source_snapshot_hash,
                },
                blocking=True,
            )
        status = StageStatus.WARNING if assessment.warning_findings else StageStatus.PASS
        return StageExecutionResult(
            status,
            "live source-watch precheck passed" if status is StageStatus.PASS else "live source-watch precheck passed with non-blocking warnings",
            {
                "source_freshness_assessment": assessment,
                "source_snapshot_hash": assessment.source_snapshot_hash,
            },
        )

    return run


def live_segment_decomposition_adapter(*, decomposer: SegmentDecomposer) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        identity = context.data.get("resolved_company_identity")
        snapshot = context.data.get("industry_knowledge_snapshot")
        if not isinstance(identity, ResolvedCompanyIdentity) or not isinstance(snapshot, IndustryKnowledgeSnapshot):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "company identity and Industry Knowledge snapshot are required before segment decomposition",
                blocking=True,
            )
        try:
            segments = tuple(decomposer(identity, snapshot))
            if not segments:
                raise ValueError("segment decomposer returned no segments")
            ids = tuple(segment.segment_id for segment in segments)
            if len(ids) != len(set(ids)):
                raise ValueError("segment decomposer returned duplicate segment IDs")
            for segment in segments:
                segment.validate()
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                f"live segment decomposition failed: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "evidence-backed segment decomposition completed",
            {"segment_descriptors": segments, "segment_ids": ids},
        )

    return run


def live_industry_dna_route_adapter(*, router: IndustryDNARouter) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        identity = context.data.get("resolved_company_identity")
        snapshot = context.data.get("industry_knowledge_snapshot")
        segments = context.data.get("segment_descriptors")
        if not isinstance(identity, ResolvedCompanyIdentity) or not isinstance(snapshot, IndustryKnowledgeSnapshot):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "company identity and Industry Knowledge snapshot are required before Industry DNA routing",
                blocking=True,
            )
        if not isinstance(segments, tuple) or not segments or not all(isinstance(item, SegmentDescriptor) for item in segments):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "typed segment descriptors are required before Industry DNA routing",
                blocking=True,
            )
        try:
            profiles = tuple(router(identity, segments, snapshot))
            if not profiles:
                raise ValueError("Industry DNA router returned no profiles")
            segment_ids = {item.segment_id for item in segments}
            profile_ids = [item.segment_id for item in profiles]
            if len(profile_ids) != len(set(profile_ids)):
                raise ValueError("Industry DNA router returned duplicate segment profiles")
            if set(profile_ids) != segment_ids:
                raise ValueError("Industry DNA profiles must cover every decomposed segment exactly once")
            known_evidence = set(snapshot.evidence_ids)
            for segment in segments:
                known_evidence.update(segment.evidence_ids)
            for profile in profiles:
                profile.validate()
                unknown = set(profile.evidence_keys).difference(known_evidence)
                if unknown:
                    raise ValueError(
                        f"Industry DNA profile {profile.segment_id} references unknown evidence IDs: {sorted(unknown)}"
                    )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                f"live Industry DNA routing failed: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "all decomposed segments routed to evidence-backed multi-label Industry DNA profiles",
            {"industry_dna_profiles": profiles},
        )

    return run


@dataclass(frozen=True)
class OpenDartCorpRecord:
    corp_code: str
    corp_name: str
    stock_code: str
    modify_date: str

    def validate(self) -> None:
        if len(self.corp_code) != 8 or not self.corp_code.isdigit():
            raise ValueError("OpenDART corp_code must be 8 digits")
        if not self.corp_name:
            raise ValueError("OpenDART corp_name is required")
        if self.stock_code and (len(self.stock_code) != 6 or not self.stock_code.isdigit()):
            raise ValueError("OpenDART stock_code must be blank or 6 digits")


FetchBytes = Callable[[str], bytes]


def build_opendart_corp_code_url(*, api_key: str | None = None) -> str:
    key = api_key or require_env_credential("DART_API_KEY")
    return "https://opendart.fss.or.kr/api/corpCode.xml?" + urlencode({"crtfc_key": key})


def parse_opendart_corp_code_archive(payload: bytes) -> tuple[OpenDartCorpRecord, ...]:
    if not payload:
        raise ValueError("OpenDART corp-code archive is empty")
    try:
        with ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            xml_name = next((name for name in names if name.lower().endswith(".xml")), None)
            if xml_name is None:
                raise ValueError("OpenDART corp-code archive contains no XML")
            xml_bytes = archive.read(xml_name)
    except BadZipFile as exc:
        raise ValueError("OpenDART corp-code response is not a ZIP archive") from exc
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError("OpenDART corp-code XML is invalid") from exc
    records: list[OpenDartCorpRecord] = []
    seen_codes: set[str] = set()
    for node in root.findall(".//list"):
        record = OpenDartCorpRecord(
            corp_code=(node.findtext("corp_code") or "").strip(),
            corp_name=(node.findtext("corp_name") or "").strip(),
            stock_code=(node.findtext("stock_code") or "").strip(),
            modify_date=(node.findtext("modify_date") or "").strip(),
        )
        record.validate()
        if record.corp_code in seen_codes:
            raise ValueError(f"duplicate OpenDART corp_code: {record.corp_code}")
        seen_codes.add(record.corp_code)
        records.append(record)
    if not records:
        raise ValueError("OpenDART corp-code archive contains no company records")
    return tuple(records)


def _normalize_company_name(value: str) -> str:
    return "".join(value.casefold().split())


def resolve_opendart_identity(
    records: tuple[OpenDartCorpRecord, ...],
    request: CompanyResolutionRequest,
    *,
    source_ref: str = "https://opendart.fss.or.kr/api/corpCode.xml",
) -> ResolvedCompanyIdentity:
    request.validate()
    query = request.query.strip()
    if request.jurisdiction and request.jurisdiction.upper() not in {"KR", "KOR", "KOREA", "SOUTH_KOREA"}:
        raise ValueError("OpenDART resolver supports Korean entities only")
    if query.isdigit() and len(query) == 6:
        matches = tuple(record for record in records if record.stock_code == query)
    elif query.isdigit() and len(query) == 8:
        matches = tuple(record for record in records if record.corp_code == query)
    else:
        normalized = _normalize_company_name(query)
        matches = tuple(record for record in records if _normalize_company_name(record.corp_name) == normalized)
    if not matches:
        raise ValueError(f"OpenDART company not found for query: {query}")
    if len(matches) != 1:
        raise ValueError(f"OpenDART company resolution is ambiguous for query: {query}")
    record = matches[0]
    return ResolvedCompanyIdentity(
        target_id=f"KR:DART:{record.corp_code}",
        legal_name=record.corp_name,
        ticker=record.stock_code,
        jurisdiction="KR",
        external_ids=(("opendart_corp_code", record.corp_code), ("krx_stock_code", record.stock_code or "UNLISTED")),
        source_refs=(source_ref,),
    )


def live_opendart_company_resolver(
    fetch_bytes: FetchBytes,
    *,
    api_key: str | None = None,
) -> CompanyResolver:
    def resolve(request: CompanyResolutionRequest) -> ResolvedCompanyIdentity:
        url = build_opendart_corp_code_url(api_key=api_key)
        records = parse_opendart_corp_code_archive(fetch_bytes(url))
        return resolve_opendart_identity(records, request, source_ref=url.split("?", 1)[0])

    return resolve
