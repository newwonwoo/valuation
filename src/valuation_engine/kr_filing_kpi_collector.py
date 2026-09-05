"""Company-neutral collector for the operating KPIs inside DART filing originals.

``dart_kpi.extract_dart_kpi`` — the exact-locator extractor with member hashes,
normalized-text spans and fail-closed ambiguity rules — existed with tests and
no engine caller, the same island pattern the probability engine had. This
module is its entrance: a :class:`CollectorCapability` that pulls the operating
tables of Korean statutory periodic filings (수주총액, 수주잔고, 생산능력,
생산실적, 가동률) into Evidence.

What makes it generic, and where it honestly ends:

- The patterns in ``config/kr_filing_kpi_patterns.yaml`` describe the *filing
  format* — the semi-standard tables of 사업보고서 "II. 사업의 내용" — never a
  company. The same patterns run against every corp code.
- A pattern that does not match a company's filing yields no Evidence for that
  metric, and the collection stage names the gap. Extraction never guesses; an
  ambiguous match (two locations) fails closed by ``dart_kpi``'s
  exactly-one rule and is likewise reported as a gap, not resolved by picking one.
- Every extracted number carries the full receipt: rcept_no, member path,
  member SHA-256, normalized-text SHA-256 and character span. A reviewer can
  reopen the filing and put a finger on the digit.

The intended growth path for coverage is a controlled LLM proposing *locators*
(member path + span) for filings the generic patterns miss, with this same
deterministic extractor re-running the proposal — accept only what re-extracts.
That keeps the one-person-securities-firm doctrine intact: the model may point,
only the extractor may read.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
import re
from pathlib import Path
from typing import Mapping

import yaml

from .collection_plan import CollectorCapability
from .dart_documents import (
    DartDocumentError,
    DartOriginalFilingDocument,
    fetch_indexed_opendart_original_document,
)
from .dart_kpi import (
    DartKPIExtractionError,
    DartKPIExtractionSpec,
    dart_kpi_observation_to_evidence,
    extract_dart_kpi,
    _visible_text,
)
from .evidence_collection import EvidenceCollectionBatch, EvidenceCollectionRequest
from .kr_opendart_provider import OpenDartNetwork, opendart_corp_code_from_target_id
from .filing_table_cells import (
    TableReadingTask,
    load_table_reading_tasks,
    propose_and_verify_table_cells,
    replay_table_cell_observation,
)
from .llm_filing_locators import (
    FilingLocatorTask,
    propose_and_verify_filing_kpis,
    validate_filing_period_context,
)
from .llm_transport import ProposalTransport, TransportError
from .live_indexers import index_opendart_filing_list
from .live_runtime import LiveCollectorProvider
from .proposal_parsing import ProposalParseError
from .runtime_resources import runtime_registry_path
from .source_index import DocumentIndexRecord


DEFAULT_PATTERN_CONFIG_PATH = runtime_registry_path("kr_filing_kpi_patterns.yaml")
COLLECTOR_ID = "kr-dart-filing-kpi"
SOURCE_ID = "KR_OPENDART"

_PERIODIC_REPORT_TOKENS = ("사업보고서", "반기보고서", "분기보고서")
_PERIOD_SUFFIX = re.compile(r"\((?P<year>20\d{2})\.(?P<month>0[1-9]|1[0-2])\)")

#: Member paths inside a DART original-document archive are opaque numbered
#: names; the extractor screens every text member rather than trusting names.
_ANY_MEMBER_PATH = r".+"


class FilingKPICollectorError(ValueError):
    """Raised when the pattern configuration or filing selection is unusable."""


@dataclass(frozen=True)
class FilingKPIPattern:
    metric: str
    locator_label: str
    value_pattern: str
    canonical_unit: str
    source_unit_map: tuple[tuple[str, str], ...]
    anchor_terms: tuple[str, ...] = ()
    critical: bool = False
    require_current_period: bool = False

    def locator_task(self) -> FilingLocatorTask:
        if not self.anchor_terms:
            raise FilingKPICollectorError(
                f"metric {self.metric} declares no anchor terms; the LLM locator "
                "path requires the metric's disclosure vocabulary"
            )
        return FilingLocatorTask(
            metric=self.metric,
            definition=self.locator_label,
            anchor_terms=self.anchor_terms,
            canonical_unit=self.canonical_unit,
            source_unit_map=self.source_unit_map,
            critical=self.critical,
            require_current_period_marker=self.require_current_period,
        )

    def to_spec(self, *, segment: str, effective_date: str) -> DartKPIExtractionSpec:
        return DartKPIExtractionSpec(
            metric=self.metric,
            segment=segment,
            member_path_pattern=_ANY_MEMBER_PATH,
            value_pattern=self.value_pattern,
            canonical_unit=self.canonical_unit,
            effective_date=effective_date,
            locator_label=self.locator_label,
            source_unit_map=self.source_unit_map,
            critical=self.critical,
        )


def load_filing_kpi_patterns(
    path: str | Path = DEFAULT_PATTERN_CONFIG_PATH,
) -> tuple[FilingKPIPattern, ...]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise FilingKPICollectorError("filing KPI pattern config must be a mapping")
    rows = payload.get("patterns")
    if not isinstance(rows, Mapping) or not rows:
        raise FilingKPICollectorError("filing KPI pattern config requires patterns")
    patterns = []
    for metric, row in rows.items():
        if not isinstance(row, Mapping):
            raise FilingKPICollectorError(f"pattern row must be a mapping: {metric}")
        pattern = FilingKPIPattern(
            metric=str(metric),
            locator_label=str(row.get("locator_label", "")),
            value_pattern=str(row.get("value_pattern", "")),
            canonical_unit=str(row.get("canonical_unit", "")),
            source_unit_map=tuple(
                (str(token), str(unit))
                for token, unit in (row.get("source_unit_map") or {}).items()
            ),
            anchor_terms=tuple(
                str(item) for item in (row.get("anchor_terms") or ())
            ),
            critical=bool(row.get("critical", False)),
            require_current_period=bool(row.get("require_current_period", False)),
        )
        # Validate eagerly through the extractor's own spec contract, with a
        # placeholder effective date: a bad regex or unit map fails at load
        # time, not in the middle of a run.
        pattern.to_spec(
            segment="config-validation", effective_date="2000-01-01"
        ).validate()
        patterns.append(pattern)
    metrics = tuple(item.metric for item in patterns)
    if len(metrics) != len(set(metrics)):
        raise FilingKPICollectorError("filing KPI pattern config has duplicate metrics")
    return tuple(patterns)


def _fiscal_period_end(record: DocumentIndexRecord) -> str:
    """The '(YYYY.MM)' suffix of a periodic report names its fiscal period."""
    match = _PERIOD_SUFFIX.search(record.title)
    if match is None:
        raise FilingKPICollectorError(
            f"periodic filing title carries no (YYYY.MM) period: {record.title!r}"
        )
    year = int(match.group("year"))
    month = int(match.group("month"))
    return date(year, month, monthrange(year, month)[1]).isoformat()


def _latest_periodic_filing(
    network: OpenDartNetwork,
    *,
    corp_code: str,
    as_of: date,
    lookback_days: int,
) -> DocumentIndexRecord:
    batch = index_opendart_filing_list(
        network.fetch_text,
        checked_at=as_of,
        corp_code=corp_code,
        begin_date=(as_of - timedelta(days=lookback_days)).strftime("%Y%m%d"),
        end_date=as_of.strftime("%Y%m%d"),
        api_key=network.api_key,
    )
    periodic = [
        record
        for record in batch.records
        if any(token in record.title for token in _PERIODIC_REPORT_TOKENS)
        and record.published_at is not None
        and record.published_at <= as_of
    ]
    if not periodic:
        raise FilingKPICollectorError(
            f"no periodic DART filing for corp {corp_code} within "
            f"{lookback_days} days of {as_of.isoformat()}"
        )
    return max(periodic, key=lambda record: (record.published_at, record.document_id))


def _read_tables(
    *,
    transport: ProposalTransport,
    filing: DartOriginalFilingDocument,
    tasks: tuple[TableReadingTask, ...],
    segment: str,
    effective_date: str,
) -> tuple[DartKPIObservation, ...]:
    """Read coordinates without converting reader failures into missing facts."""
    try:
        return propose_and_verify_table_cells(
            transport=transport,
            filing=filing,
            tasks=tasks,
            segment=segment,
            effective_date=effective_date,
        )
    except (TransportError, TimeoutError, ConnectionError) as error:
        raise FilingKPICollectorError(
            "READER_UNAVAILABLE: filing table reader could not complete"
        ) from error


def request_scoped_filing_kpi_collector(
    network: OpenDartNetwork,
    *,
    as_of: str,
    segment_id: str,
    patterns: tuple[FilingKPIPattern, ...],
    lookback_days: int = 540,
    transport: ProposalTransport | None = None,
    table_cell_receipts: Mapping[str, str] | None = None,
):
    """EvidenceCollector reading the latest periodic filing's operating tables.

    Two passes. The static patterns run first — the fast deterministic path for
    the statutory layouts. Metrics they miss go to the LLM locator analyst when
    a transport is configured: the model points at where the filing discloses
    the metric, and the same deterministic extractor re-reads the document at
    that locator — only what re-extracts becomes Evidence, and its notes say
    the locator was a verified LLM proposal.

    Per-metric misses (static and locator alike) are omitted from the batch —
    the stage-level coverage check then reports exactly which required metrics
    stayed unobserved. A fetch or archive-integrity failure, by contrast,
    raises and blocks: a broken source is not a coverage gap.
    """
    network.validate()
    if not segment_id:
        raise FilingKPICollectorError("segment_id is required")
    cutoff = date.fromisoformat(as_of[:10])
    by_metric = {item.metric: item for item in patterns}
    table_tasks = load_table_reading_tasks()

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        corp_code = opendart_corp_code_from_target_id(request.target_id)
        unsupported = tuple(
            sorted(set(request.required_metrics) - (set(by_metric) | set(table_tasks)))
        )
        if unsupported:
            raise FilingKPICollectorError(
                "filing KPI collector received metrics outside its declared "
                "capability: " + ", ".join(unsupported)
            )
        record = _latest_periodic_filing(
            network,
            corp_code=corp_code,
            as_of=cutoff,
            lookback_days=lookback_days,
        )
        effective_date = _fiscal_period_end(record)
        filing: DartOriginalFilingDocument = fetch_indexed_opendart_original_document(
            network.fetch_bytes,
            record,
            checked_at=cutoff,
            api_key=network.api_key,
        )
        records = []
        replayed_metrics: set[str] = set()
        for metric, receipt in (table_cell_receipts or {}).items():
            if metric not in request.required_metrics or metric not in table_tasks:
                raise ProposalParseError(
                    "EVIDENCE_RECONCILIATION_REQUIRED: receipt names an unrequested metric"
                )
            observation = replay_table_cell_observation(
                filing, receipt, table_tasks[metric],
                segment=segment_id, effective_date=effective_date,
            )
            records.append(dart_kpi_observation_to_evidence(
                observation, target_id=request.target_id, observed_date=as_of[:10],
            ))
            replayed_metrics.add(metric)
        statically_missed: list[str] = []
        for metric in request.required_metrics:
            if metric in replayed_metrics:
                continue
            pattern = by_metric.get(metric)
            if pattern is None:
                statically_missed.append(metric)
                continue
            spec = pattern.to_spec(
                segment=segment_id,
                effective_date=effective_date,
            )
            try:
                observation = extract_dart_kpi(filing, spec)
                member = next(
                    item
                    for item in filing.text_members
                    if item.path == observation.member_path
                )
                normalized = _visible_text(member)
                # The static regex begins at the metric label, while a period
                # marker commonly sits immediately before it in the same row.
                # Validate a tight surrounding span rather than the match alone.
                context = normalized[
                    # Current/prior markers qualify the row label immediately;
                    # a wider prefix can accidentally borrow "당기" from the
                    # preceding metric's row in normalized table text.
                    max(0, observation.text_start - 12):
                    min(len(normalized), observation.text_end + 48)
                ]
                marker_context = normalized[
                    max(0, observation.text_start - 12):observation.text_end
                ]
                validate_filing_period_context(
                    context,
                    metric=metric,
                    effective_date=effective_date,
                    require_current_period_marker=pattern.require_current_period,
                    current_period_text=marker_context,
                )
            except (DartKPIExtractionError, ProposalParseError):
                # Not disclosed in the standard layout, or ambiguous. The LLM
                # locator pass may still find it; otherwise the coverage check
                # downstream names this metric as missing.
                statically_missed.append(metric)
                continue
            records.append(
                dart_kpi_observation_to_evidence(
                    observation,
                    target_id=request.target_id,
                    observed_date=as_of[:10],
                )
            )
        locator_missed = [metric for metric in statically_missed if metric in by_metric]
        supports_role = getattr(transport, "supports_role", None)
        locator_available = transport is not None and (
            supports_role is None or supports_role("filing_locator_analyst")
        )
        if locator_missed and locator_available:
            observations = propose_and_verify_filing_kpis(
                transport=transport,
                filing=filing,
                tasks=tuple(
                    by_metric[metric].locator_task()
                    for metric in locator_missed
                ),
                segment=segment_id,
                effective_date=effective_date,
            )
            for observation in observations:
                records.append(
                    dart_kpi_observation_to_evidence(
                        observation,
                        target_id=request.target_id,
                        observed_date=as_of[:10],
                    )
                )
        # Third pass: a metric the statutory layout and the quoted locator both
        # missed may still sit in a table the reading registry describes. The
        # coordinate path is tried last. Reader and verification failures must
        # not be represented as evidence that the filing omits a metric.
        found = {item.metric for item in records}
        unread = tuple(
            metric
            for metric in statically_missed
            if metric not in found and metric in table_tasks
        )
        if unread and transport is not None:
            for observation in _read_tables(
                transport=transport,
                filing=filing,
                tasks=tuple(table_tasks[metric] for metric in unread),
                segment=segment_id,
                effective_date=effective_date,
            ):
                records.append(
                    dart_kpi_observation_to_evidence(
                        observation,
                        target_id=request.target_id,
                        observed_date=as_of[:10],
                    )
                )
        batch = EvidenceCollectionBatch(
            source_id=SOURCE_ID,
            checked_at=as_of,
            records=tuple(records),
            source_fingerprint=filing.manifest_hash,
            document_ids=(record.document_id,),
        )
        batch.validate()
        return batch

    return collect


def _available_table_metrics(transport, table_cell_receipts) -> set[str]:
    """Advertise a table-only metric only with a reader or a saved receipt.

    Live transports without role introspection retain their normal capability.
    A configured reader that later fails still raises; this is not error recovery.
    """
    tasks = set(load_table_reading_tasks())
    supports_role = getattr(transport, "supports_role", None)
    if transport is not None and (
        supports_role is None or supports_role("filing_table_reader")
    ):
        return tasks
    return tasks & set(table_cell_receipts or {})


def filing_kpi_collector_provider(
    network: OpenDartNetwork,
    *,
    as_of: str,
    segment_id: str,
    pattern_config_path: str | Path = DEFAULT_PATTERN_CONFIG_PATH,
    lookback_days: int = 540,
    transport: ProposalTransport | None = None,
    table_cell_receipts: Mapping[str, str] | None = None,
) -> LiveCollectorProvider:
    patterns = load_filing_kpi_patterns(pattern_config_path)
    return LiveCollectorProvider(
        capability=CollectorCapability(
            collector_id=COLLECTOR_ID,
            source_id=SOURCE_ID,
            supported_metrics=tuple(sorted(
                {item.metric for item in patterns}
                | _available_table_metrics(transport, table_cell_receipts)
            )),
            jurisdictions=("KR",),
            implementation_ref=(
                "valuation_engine.kr_filing_kpi_collector."
                "request_scoped_filing_kpi_collector"
            ),
        ),
        collector=request_scoped_filing_kpi_collector(
            network,
            as_of=as_of,
            segment_id=segment_id,
            patterns=patterns,
            lookback_days=lookback_days,
            transport=transport,
            table_cell_receipts=table_cell_receipts,
        ),
    )
