"""Primary Evidence records from an already verified IFRS 8 segment note.

The LLM-reviewed declaration points to the irregular filing table.  The
``SourceBoundSegmentExtraction`` contract has already re-read the declared
cells, checked their hashes and reconciled segment totals.  This provider only
exposes those verified values to the ordinary EvidenceLedger so downstream
research—including Broker Research leads—can bind to company-primary facts.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from hashlib import sha256

from .collection_plan import CollectorCapability
from .declared_segments import DeclaredSegments
from .evidence_collection import EvidenceCollectionBatch, EvidenceCollectionRequest
from .live_runtime import LiveCollectorProvider
from .records import EvidenceRecord, EvidenceSourceLayer


SOURCE_ID = "KR_OPENDART"
COLLECTOR_ID = "declared-ifrs8-segment-evidence"
REVENUE_METRIC_SUFFIX = "reported_segment_revenue"
OPERATING_INCOME_METRIC_SUFFIX = "reported_segment_operating_income"


def _receipt_date(document_id: str) -> str:
    token = document_id.removeprefix("DART_")[:8]
    if len(token) != 8 or not token.isdigit():
        raise ValueError("source-bound DART document_id carries no receipt date")
    return date(int(token[:4]), int(token[4:6]), int(token[6:8])).isoformat()


def _serialized_decimal(value: Decimal) -> int | str:
    integral = value.to_integral_value()
    return int(integral) if value == integral else format(value, "f")


def declared_segment_evidence_provider(
    declaration: DeclaredSegments,
    *,
    effective_date: str,
    checked_at: str,
) -> LiveCollectorProvider:
    declaration.validate()
    extraction = declaration.source_bound_extraction
    if extraction is None:
        raise ValueError(
            "declared segment Evidence requires a source_bound_extraction"
        )
    extraction.validate()
    observed_date = _receipt_date(extraction.document_id)
    supported_metrics = tuple(
        metric
        for declared in declaration.segments
        for metric in (
            f"{declared.segment_id}_{REVENUE_METRIC_SUFFIX}",
            f"{declared.segment_id}_{OPERATING_INCOME_METRIC_SUFFIX}",
        )
    )

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        if request.target_id != declaration.target_id:
            raise ValueError(
                f"segment Evidence is bound to {declaration.target_id}, not "
                f"{request.target_id}"
            )
        requested = set(request.required_metrics).intersection(supported_metrics)
        records: list[EvidenceRecord] = []
        for declared, entry in zip(declaration.segments, extraction.entries, strict=True):
            segment_metrics = (
                f"{declared.segment_id}_{REVENUE_METRIC_SUFFIX}",
                f"{declared.segment_id}_{OPERATING_INCOME_METRIC_SUFFIX}",
            )
            for metric in segment_metrics:
                if metric not in requested:
                    continue
                if metric.endswith(REVENUE_METRIC_SUFFIX):
                    value = entry.revenue
                    offset = entry.revenue_offset
                else:
                    value = entry.operating_income
                    offset = entry.operating_income_offset
                records.append(
                    EvidenceRecord(
                        id=(
                            f"DART_SEGMENT:{declaration.target_id}:"
                            f"{declared.segment_id}:{metric}"
                        ),
                        target=declaration.target_id,
                        metric=metric,
                        value=_serialized_decimal(value),
                        unit=extraction.reporting_unit,
                        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
                        effective_date=effective_date,
                        observed_date=observed_date,
                        source_name="OpenDART IFRS 8 operating-segment note",
                        source_ref=declaration.source_ref,
                        source_grade="A",
                        confidence=1.0,
                        segment=declared.segment_id,
                        notes=(
                            f"document={extraction.document_id}; "
                            f"member={extraction.member_path}; cell_offset={offset}"
                        ),
                    )
                )
        fingerprint = sha256(
            (
                extraction.document_id
                + "|"
                + extraction.member_path
                + "|"
                + extraction.member_sha256
            ).encode("utf-8")
        ).hexdigest()
        batch = EvidenceCollectionBatch(
            source_id=SOURCE_ID,
            checked_at=checked_at,
            records=tuple(records),
            source_fingerprint=fingerprint,
            document_ids=(extraction.document_id,),
        )
        batch.validate()
        return batch

    return LiveCollectorProvider(
        capability=CollectorCapability(
            collector_id=COLLECTOR_ID,
            source_id=SOURCE_ID,
            supported_metrics=supported_metrics,
            jurisdictions=("KR",),
            implementation_ref=(
                "valuation_engine.declared_segment_evidence."
                "declared_segment_evidence_provider"
            ),
        ),
        collector=collect,
    )


__all__ = [
    "COLLECTOR_ID",
    "OPERATING_INCOME_METRIC_SUFFIX",
    "REVENUE_METRIC_SUFFIX",
    "declared_segment_evidence_provider",
]
