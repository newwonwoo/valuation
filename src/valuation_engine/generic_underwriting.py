"""The operator's judgments enter through the front door, or not at all.

Some required inputs are nobody's disclosure: a normalized mid-cycle EBITDA, a
through-cycle multiple, a cash-cost assumption. In the hand-written company
modules these judgments lived inside Python; the whole point of the cold-start
work is that they may not. This collector is their one legitimate entrance:

- a **per-run declared file**, owned by the operator, bound to exactly one
  ``target_id`` — a file written for another company fails closed;
- every declaration carries a value, a unit, and a **rationale of substance**
  (a judgment without a reason is not admissible Evidence);
- every record enters at ``ANALYST_UNDERWRITING`` layer, never anything that
  could be mistaken for a filing — the evidence-composition guardrail then
  reports exactly how much of the valuation stands on these declarations;
- the file's own SHA-256 is the batch fingerprint, so the run's hash chain
  binds the exact set of judgments that entered.

The registered source is ``OPERATOR_UNDERWRITING`` — a company-primary source
whose authority field says what it is: ``analyst_declared``. Nothing here makes
a judgment look like a fact; it makes the judgment *auditable*.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Mapping

import yaml

from .collection_plan import CollectorCapability
from .evidence_collection import EvidenceCollectionBatch, EvidenceCollectionRequest
from .live_runtime import LiveCollectorProvider
from .records import EvidenceRecord, EvidenceSourceLayer
from .source_reporting import canonical_verification_url


SOURCE_ID = "OPERATOR_UNDERWRITING"
COLLECTOR_ID = "operator-declared-underwriting"
_MIN_RATIONALE_CHARS = 20


class DeclaredUnderwritingError(ValueError):
    """Raised when a declared-underwriting file violates its contract."""


@dataclass(frozen=True)
class DeclaredUnderwritingEvidenceRecord(EvidenceRecord):
    """Evidence that retains every original source behind one declaration."""

    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.source_refs or self.source_ref != self.source_refs[0]:
            raise ValueError(
                "declared underwriting evidence requires source_ref to be the "
                "first member of its non-empty source_refs"
            )


def _declared_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise DeclaredUnderwritingError(f"{label} must be YYYY-MM-DD") from exc


def _assert_knowable_by(payload: Mapping, run_as_of: str) -> None:
    cutoff = _declared_date(run_as_of, "run as_of")
    declared_at = _declared_date(str(payload["as_of"]), "underwriting as_of")
    if declared_at > cutoff:
        raise DeclaredUnderwritingError(
            f"declared underwriting as_of {declared_at.isoformat()} is after run "
            f"cutoff {cutoff.isoformat()}; future analyst judgment is inadmissible"
        )


def _declaration_source_refs(
    row: Mapping, *, fallback: str, metric: object
) -> tuple[str, ...]:
    has_single = row.get("source_ref") is not None
    has_many = row.get("source_refs") is not None
    if has_single and has_many:
        raise DeclaredUnderwritingError(
            f"declaration {metric} must use source_ref or source_refs, not both"
        )
    if has_many:
        raw_refs = row.get("source_refs")
        if not isinstance(raw_refs, (list, tuple)) or not raw_refs:
            raise DeclaredUnderwritingError(
                f"declaration {metric} source_refs must be a non-empty list"
            )
        refs = tuple(str(item or "").strip() for item in raw_refs)
    else:
        single = str(row.get("source_ref") or fallback or "").strip()
        refs = (single,) if single else ()
    refs = tuple(dict.fromkeys(refs))
    if not refs:
        raise DeclaredUnderwritingError(
            f"declaration {metric} requires an HTTP provenance source_ref"
        )
    invalid = tuple(ref for ref in refs if canonical_verification_url(ref) is None)
    if invalid:
        raise DeclaredUnderwritingError(
            f"declaration {metric} source_refs must be credential-free HTTP(S) "
            "provenance links"
        )
    return refs


def load_declared_underwriting(path: str | Path) -> dict:
    raw = Path(path).read_text(encoding="utf-8")
    payload = yaml.safe_load(raw)
    if not isinstance(payload, Mapping):
        raise DeclaredUnderwritingError("declared underwriting must be a mapping")
    target_id = str(payload.get("target_id") or "")
    as_of = str(payload.get("as_of") or "")
    source_ref = str(payload.get("source_ref") or "")
    declarations = payload.get("declarations")
    if not target_id or not as_of:
        raise DeclaredUnderwritingError(
            "declared underwriting requires target_id and as_of"
        )
    _declared_date(as_of, "underwriting as_of")
    if source_ref and canonical_verification_url(source_ref) is None:
        raise DeclaredUnderwritingError(
            "declared underwriting source_ref must be a credential-free HTTP(S) provenance link "
            "(the report's source-link contract verifies every Evidence ref)"
        )
    if not isinstance(declarations, Mapping) or not declarations:
        raise DeclaredUnderwritingError(
            "declared underwriting requires a declarations mapping"
        )
    normalized_declarations: dict[object, list[dict]] = {}
    for metric, entry in declarations.items():
        # A metric declared once is the historical single-row form. A list of
        # rows declares the same metric for several segments (a multi-segment
        # run's steel and transport both underwrite an input_price); each row
        # then names its segment and the pair (metric, segment) must be
        # unique.
        rows = entry if isinstance(entry, list) else [entry]
        if not rows:
            raise DeclaredUnderwritingError(
                f"declaration {metric} carries no rows"
            )
        multi_row = isinstance(entry, list)
        seen_segments: set[str] = set()
        normalized_rows: list[dict] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise DeclaredUnderwritingError(
                    f"declaration {metric} must be a mapping"
                )
            if row.get("value") is None or not str(row.get("unit") or ""):
                raise DeclaredUnderwritingError(
                    f"declaration {metric} requires value and unit"
                )
            rationale = str(row.get("rationale") or "")
            if len(rationale.strip()) < _MIN_RATIONALE_CHARS:
                raise DeclaredUnderwritingError(
                    f"declaration {metric} requires a substantive rationale "
                    f"(>= {_MIN_RATIONALE_CHARS} chars); a judgment without a reason "
                    "is not admissible"
                )
            segment = str(row.get("segment", "core"))
            if multi_row and not str(row.get("segment") or ""):
                raise DeclaredUnderwritingError(
                    f"declaration {metric} lists multiple rows; each row must "
                    "name its segment"
                )
            if segment in seen_segments:
                raise DeclaredUnderwritingError(
                    f"declaration {metric} declares segment {segment} twice"
                )
            seen_segments.add(segment)
            normalized_rows.append(
                {
                    **row,
                    "_multi_row": multi_row,
                    "source_refs": _declaration_source_refs(
                        row, fallback=source_ref, metric=metric
                    ),
                }
            )
        normalized_declarations[metric] = normalized_rows
    return {
        "target_id": target_id,
        "as_of": as_of,
        "source_ref": source_ref,
        "declarations": normalized_declarations,
        "file_sha256": sha256(raw.encode("utf-8")).hexdigest(),
    }


def declared_underwriting_collector(
    path: str | Path, *, run_as_of: str | None = None
):
    """EvidenceCollector serving the operator's declared judgments for one run."""
    payload = load_declared_underwriting(path)
    if run_as_of is not None:
        _assert_knowable_by(payload, run_as_of)

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        if request.target_id != payload["target_id"]:
            raise DeclaredUnderwritingError(
                f"declared underwriting is bound to {payload['target_id']}, "
                f"not {request.target_id}; refusing cross-company reuse"
            )
        declarations = payload["declarations"]
        records = []
        for metric in request.required_metrics:
            rows = declarations.get(metric)
            if rows is None:
                continue  # undeclared: a named coverage gap downstream
            for row in rows:
                source_refs = tuple(row["source_refs"])
                # Single-row declarations keep the historical Evidence id so
                # committed runs replay byte-identically; a multi-row metric
                # appends the segment, because two segments' judgments are
                # two pieces of Evidence.
                record_id = (
                    f"UW:{payload['target_id']}:{metric}:{row.get('segment')}"
                    if row.get("_multi_row")
                    else f"UW:{payload['target_id']}:{metric}"
                )
                records.append(
                    DeclaredUnderwritingEvidenceRecord(
                        id=record_id,
                    target=payload["target_id"],
                    metric=metric,
                    value=row["value"],
                    unit=str(row["unit"]),
                    source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                    effective_date=payload["as_of"],
                    observed_date=payload["as_of"],
                    source_name="operator declared underwriting",
                    source_ref=source_refs[0],
                    source_grade="B",
                    confidence=float(row.get("confidence", 0.6)),
                    segment=str(row.get("segment", "core")),
                    notes=(
                        "analyst_declared_judgment; rationale="
                        + str(row["rationale"]).strip()
                    ),
                    source_refs=source_refs,
                )
            )
        batch = EvidenceCollectionBatch(
            source_id=SOURCE_ID,
            checked_at=payload["as_of"],
            records=tuple(records),
            source_fingerprint=payload["file_sha256"],
            document_ids=(f"UNDERWRITING_{payload['file_sha256'][:16]}",),
        )
        batch.validate()
        return batch

    return collect


def declared_underwriting_provider(
    path: str | Path, *, run_as_of: str | None = None
) -> LiveCollectorProvider:
    payload = load_declared_underwriting(path)
    if run_as_of is not None:
        _assert_knowable_by(payload, run_as_of)
    return LiveCollectorProvider(
        capability=CollectorCapability(
            collector_id=COLLECTOR_ID,
            source_id=SOURCE_ID,
            supported_metrics=tuple(payload["declarations"]),
            jurisdictions=("KR",),
            implementation_ref=(
                "valuation_engine.generic_underwriting.declared_underwriting_collector"
            ),
        ),
        collector=declared_underwriting_collector(path, run_as_of=run_as_of),
    )
