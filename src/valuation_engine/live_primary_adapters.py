from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
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
            raise ValueError(
                "resolved company requires target_id, legal_name and jurisdiction"
            )
        if not self.external_ids or not self.source_refs:
            raise ValueError(
                "resolved company requires external IDs and source references"
            )
        keys = tuple(key for key, value in self.external_ids if key and value)
        if len(keys) != len(self.external_ids) or len(keys) != len(set(keys)):
            raise ValueError(
                "resolved company external IDs must be unique non-empty key/value pairs"
            )


CompanyResolver = Callable[[CompanyResolutionRequest], ResolvedCompanyIdentity]


def _parse_lineage_datetime(value: str, *, label: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed


def _snapshot_cutoff(value: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    if len(text) == 10:
        try:
            day = date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("industry snapshot as_of must be ISO date/timestamp") from exc
        return datetime.combine(day, time.max, tzinfo=timezone.utc)
    return _parse_lineage_datetime(text, label="industry snapshot as_of")


@dataclass(frozen=True)
class AuthoritativeEvidenceLineage:
    """Frozen Evidence identity and chronology used by segment decomposition.

    Economic/event dates are kept separate from knowledge-time fields so a backfilled
    filing or revision cannot enter a historical snapshot merely because its economic
    date precedes that snapshot.
    """

    evidence_id: str
    target_id: str
    source_id: str
    observed_date: str
    content_hash: str
    event_date: str
    effective_date: str
    published_at: str
    first_seen_at: str
    revision_id: str
    revision_at: str
    active: bool = True

    def validate(self) -> None:
        if not all(
            (
                self.evidence_id,
                self.target_id,
                self.source_id,
                self.observed_date,
                self.content_hash,
                self.event_date,
                self.effective_date,
                self.published_at,
                self.first_seen_at,
                self.revision_id,
                self.revision_at,
            )
        ):
            raise ValueError("authoritative Evidence lineage is incomplete")
        date.fromisoformat(self.observed_date[:10])
        date.fromisoformat(self.event_date[:10])
        date.fromisoformat(self.effective_date[:10])
        published = _parse_lineage_datetime(
            self.published_at, label="Evidence published_at"
        )
        first_seen = _parse_lineage_datetime(
            self.first_seen_at, label="Evidence first_seen_at"
        )
        revision = _parse_lineage_datetime(
            self.revision_at, label="Evidence revision_at"
        )
        if published > first_seen:
            raise ValueError("Evidence first_seen_at cannot precede published_at")
        if revision > first_seen:
            raise ValueError("Evidence first_seen_at cannot precede revision_at")

    @property
    def knowledge_time(self) -> datetime:
        self.validate()
        return _parse_lineage_datetime(
            self.first_seen_at, label="Evidence first_seen_at"
        )

    @property
    def fingerprint(self) -> str:
        return "|".join(
            (
                self.evidence_id,
                self.target_id,
                self.source_id,
                self.observed_date,
                self.content_hash,
                self.event_date,
                self.effective_date,
                self.published_at,
                self.first_seen_at,
                self.revision_id,
                self.revision_at,
                "active" if self.active else "inactive",
            )
        )


@dataclass(frozen=True)
class IndustryKnowledgeSnapshot:
    as_of: str
    source_ids: tuple[str, ...]
    document_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    content_hashes: tuple[str, ...]
    snapshot_hash: str
    evidence_lineage: tuple[AuthoritativeEvidenceLineage, ...] = ()

    def expected_hash(self) -> str:
        payload = "\n".join(
            (
                self.as_of,
                *sorted(self.source_ids),
                *sorted(self.document_ids),
                *sorted(self.evidence_ids),
                *sorted(self.content_hashes),
                *(
                    item.fingerprint
                    for item in sorted(
                        self.evidence_lineage,
                        key=lambda value: value.evidence_id,
                    )
                ),
            )
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def validate(self) -> None:
        cutoff = _snapshot_cutoff(self.as_of)
        if not self.source_ids or not self.content_hashes:
            raise ValueError(
                "industry snapshot requires source IDs and content hashes"
            )
        ids: list[str] = []
        for lineage in self.evidence_lineage:
            lineage.validate()
            ids.append(lineage.evidence_id)
            if lineage.evidence_id not in self.evidence_ids:
                raise ValueError(
                    f"lineage Evidence ID is not declared by snapshot: {lineage.evidence_id}"
                )
            if lineage.source_id not in self.source_ids:
                raise ValueError(
                    f"lineage source is outside snapshot source set: {lineage.source_id}"
                )
            if lineage.content_hash not in self.content_hashes:
                raise ValueError(
                    f"lineage content hash is outside snapshot hash set: {lineage.evidence_id}"
                )
            if lineage.knowledge_time > cutoff:
                raise ValueError(
                    f"lineage Evidence was first seen after snapshot as_of: {lineage.evidence_id}"
                )
        if len(ids) != len(set(ids)):
            raise ValueError("industry snapshot has duplicate Evidence lineage IDs")
        if self.snapshot_hash != self.expected_hash():
            raise ValueError("industry snapshot hash mismatch")

    @property
    def lineage_by_id(self) -> dict[str, AuthoritativeEvidenceLineage]:
        return {item.evidence_id: item for item in self.evidence_lineage}

    @classmethod
    def build(
        cls,
        *,
        as_of: str,
        source_ids: tuple[str, ...],
        document_ids: tuple[str, ...] = (),
        evidence_ids: tuple[str, ...] = (),
        content_hashes: tuple[str, ...],
        evidence_lineage: tuple[AuthoritativeEvidenceLineage, ...] = (),
    ) -> "IndustryKnowledgeSnapshot":
        provisional = cls(
            as_of,
            source_ids,
            document_ids,
            evidence_ids,
            content_hashes,
            "",
            evidence_lineage,
        )
        completed = cls(
            as_of,
            source_ids,
            document_ids,
            evidence_ids,
            content_hashes,
            provisional.expected_hash(),
            evidence_lineage,
        )
        completed.validate()
        return completed


IndustrySnapshotLoader = Callable[
    [ResolvedCompanyIdentity], IndustryKnowledgeSnapshot
]


@dataclass(frozen=True)
class LiveFreshnessAssessment:
    checked_at: str
    findings: tuple[WatchFinding, ...]
    source_snapshot_hash: str

    def validate(self) -> None:
        date.fromisoformat(self.checked_at[:10])
        if not self.source_snapshot_hash:
            raise ValueError(
                "freshness assessment requires source_snapshot_hash"
            )
        if not self.findings:
            raise ValueError(
                "freshness assessment requires at least one source finding"
            )

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
        return tuple(
            finding
            for finding in self.findings
            if finding not in blocking
            and finding.status is not WatchStatus.CLEAN
        )


FreshnessLoader = Callable[
    [ResolvedCompanyIdentity, IndustryKnowledgeSnapshot],
    LiveFreshnessAssessment,
]


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
            raise ValueError(
                "segment descriptor requires complete economic-structure fields"
            )
        if not self.evidence_ids:
            raise ValueError(f"segment {self.segment_id} requires evidence IDs")


SegmentDecomposer = Callable[
    [ResolvedCompanyIdentity, IndustryKnowledgeSnapshot],
    tuple[SegmentDescriptor, ...],
]
IndustryDNARouter = Callable[
    [
        ResolvedCompanyIdentity,
        tuple[SegmentDescriptor, ...],
        IndustryKnowledgeSnapshot,
    ],
    tuple[IndustryDNAProfile, ...],
]


def _validate_segment_evidence_lineage(
    identity: ResolvedCompanyIdentity,
    snapshot: IndustryKnowledgeSnapshot,
    segments: tuple[SegmentDescriptor, ...],
) -> str:
    lineage_by_id = snapshot.lineage_by_id
    used: dict[str, AuthoritativeEvidenceLineage] = {}
    cutoff = _snapshot_cutoff(snapshot.as_of)
    for segment in segments:
        for evidence_id in segment.evidence_ids:
            if evidence_id not in snapshot.evidence_ids:
                raise ValueError(
                    f"segment {segment.segment_id} references Evidence outside the authoritative snapshot: {evidence_id}"
                )
            lineage = lineage_by_id.get(evidence_id)
            if lineage is None:
                raise ValueError(
                    f"segment {segment.segment_id} Evidence lacks authoritative lineage: {evidence_id}"
                )
            if lineage.target_id != identity.target_id:
                raise ValueError(
                    f"segment {segment.segment_id} Evidence target mismatch: {evidence_id}"
                )
            if lineage.source_id not in snapshot.source_ids:
                raise ValueError(
                    f"segment {segment.segment_id} Evidence source mismatch: {evidence_id}"
                )
            if lineage.content_hash not in snapshot.content_hashes:
                raise ValueError(
                    f"segment {segment.segment_id} Evidence content hash mismatch: {evidence_id}"
                )
            if lineage.knowledge_time > cutoff:
                raise ValueError(
                    f"segment {segment.segment_id} Evidence was not knowable at snapshot cutoff: {evidence_id}"
                )
            if not lineage.active:
                raise ValueError(
                    f"segment {segment.segment_id} Evidence is not active: {evidence_id}"
                )
            used[evidence_id] = lineage
    payload = "\n".join(
        used[evidence_id].fingerprint for evidence_id in sorted(used)
    )
    return sha256(payload.encode("utf-8")).hexdigest()


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


def live_industry_snapshot_adapter(
    *, loader: IndustrySnapshotLoader
) -> StageAdapter:
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
        if not isinstance(identity, ResolvedCompanyIdentity) or not isinstance(
            snapshot, IndustryKnowledgeSnapshot
        ):
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
                "source changes must be incorporated/reviewed before valuation: "
                + detail,
                {
                    "source_freshness_assessment": assessment,
                    "source_snapshot_hash": assessment.source_snapshot_hash,
                },
                blocking=True,
            )
        status = (
            StageStatus.WARNING
            if assessment.warning_findings
            else StageStatus.PASS
        )
        return StageExecutionResult(
            status,
            (
                "live source-watch precheck passed"
                if status is StageStatus.PASS
                else "live source-watch precheck passed with non-blocking warnings"
            ),
            {
                "source_freshness_assessment": assessment,
                "source_snapshot_hash": assessment.source_snapshot_hash,
            },
        )

    return run


def live_segment_decomposition_adapter(
    *, decomposer: SegmentDecomposer
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        identity = context.data.get("resolved_company_identity")
        snapshot = context.data.get("industry_knowledge_snapshot")
        if not isinstance(identity, ResolvedCompanyIdentity) or not isinstance(
            snapshot, IndustryKnowledgeSnapshot
        ):
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
                raise ValueError(
                    "segment decomposer returned duplicate segment IDs"
                )
            for segment in segments:
                segment.validate()
            lineage_hash = _validate_segment_evidence_lineage(
                identity, snapshot, segments
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                f"live segment decomposition failed: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "authoritative-lineage-backed segment decomposition completed",
            {
                "segment_descriptors": segments,
                "segment_ids": ids,
                "segment_evidence_lineage_hash": lineage_hash,
            },
        )

    return run


def live_industry_dna_route_adapter(*, router: IndustryDNARouter) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        identity = context.data.get("resolved_company_identity")
        snapshot = context.data.get("industry_knowledge_snapshot")
        segments = context.data.get("segment_descriptors")
        if not isinstance(identity, ResolvedCompanyIdentity) or not isinstance(
            snapshot, IndustryKnowledgeSnapshot
        ):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "company identity and Industry Knowledge snapshot are required before Industry DNA routing",
                blocking=True,
            )
        if (
            not isinstance(segments, tuple)
            or not segments
            or not all(isinstance(item, SegmentDescriptor) for item in segments)
        ):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "typed segment descriptors are required before Industry DNA routing",
                blocking=True,
            )
        if not context.data.get("segment_evidence_lineage_hash"):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "authoritative segment Evidence lineage must be verified before Industry DNA routing",
                blocking=True,
            )
        try:
            profiles = tuple(router(identity, segments, snapshot))
            if not profiles:
                raise ValueError("Industry DNA router returned no profiles")
            segment_ids = {item.segment_id for item in segments}
            profile_ids = [item.segment_id for item in profiles]
            if len(profile_ids) != len(set(profile_ids)):
                raise ValueError(
                    "Industry DNA router returned duplicate segment profiles"
                )
            if set(profile_ids) != segment_ids:
                raise ValueError(
                    "Industry DNA profiles must cover every decomposed segment exactly once"
                )
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
        if self.stock_code and (
            len(self.stock_code) != 6 or not self.stock_code.isdigit()
        ):
            raise ValueError("OpenDART stock_code must be blank or 6 digits")


FetchBytes = Callable[[str], bytes]


def build_opendart_corp_code_url(*, api_key: str | None = None) -> str:
    key = api_key or require_env_credential("DART_API_KEY")
    return "https://opendart.fss.or.kr/api/corpCode.xml?" + urlencode(
        {"crtfc_key": key}
    )


def parse_opendart_corp_code_archive(
    payload: bytes,
) -> tuple[OpenDartCorpRecord, ...]:
    if not payload:
        raise ValueError("OpenDART corp-code archive is empty")
    try:
        with ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            xml_name = next(
                (name for name in names if name.lower().endswith(".xml")),
                None,
            )
            if xml_name is None:
                raise ValueError(
                    "OpenDART corp-code archive contains no XML"
                )
            xml_bytes = archive.read(xml_name)
    except BadZipFile as exc:
        raise ValueError(
            "OpenDART corp-code response is not a ZIP archive"
        ) from exc
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
            raise ValueError(
                f"duplicate OpenDART corp_code: {record.corp_code}"
            )
        seen_codes.add(record.corp_code)
        records.append(record)
    if not records:
        raise ValueError(
            "OpenDART corp-code archive contains no company records"
        )
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
    if request.jurisdiction and request.jurisdiction.upper() not in {
        "KR",
        "KOR",
        "KOREA",
        "SOUTH_KOREA",
    }:
        raise ValueError("OpenDART resolver supports Korean entities only")
    if query.isdigit() and len(query) == 6:
        matches = tuple(
            record for record in records if record.stock_code == query
        )
    elif query.isdigit() and len(query) == 8:
        matches = tuple(
            record for record in records if record.corp_code == query
        )
    else:
        normalized = _normalize_company_name(query)
        matches = tuple(
            record
            for record in records
            if _normalize_company_name(record.corp_name) == normalized
        )
    if not matches:
        raise ValueError(f"OpenDART company not found for query: {query}")
    if len(matches) != 1:
        raise ValueError(
            f"OpenDART company resolution is ambiguous for query: {query}"
        )
    record = matches[0]
    return ResolvedCompanyIdentity(
        target_id=f"KR:DART:{record.corp_code}",
        legal_name=record.corp_name,
        ticker=record.stock_code,
        jurisdiction="KR",
        external_ids=(
            ("opendart_corp_code", record.corp_code),
            ("krx_stock_code", record.stock_code or "UNLISTED"),
        ),
        source_refs=(source_ref,),
    )


def live_opendart_company_resolver(
    fetch_bytes: FetchBytes,
    *,
    api_key: str | None = None,
) -> CompanyResolver:
    def resolve(
        request: CompanyResolutionRequest,
    ) -> ResolvedCompanyIdentity:
        url = build_opendart_corp_code_url(api_key=api_key)
        records = parse_opendart_corp_code_archive(fetch_bytes(url))
        return resolve_opendart_identity(
            records,
            request,
            source_ref=url.split("?", 1)[0],
        )

    return resolve
