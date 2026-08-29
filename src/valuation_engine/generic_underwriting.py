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

from hashlib import sha256
from pathlib import Path
from typing import Mapping

import yaml

from .collection_plan import CollectorCapability
from .evidence_collection import EvidenceCollectionBatch, EvidenceCollectionRequest
from .live_runtime import LiveCollectorProvider
from .records import EvidenceRecord, EvidenceSourceLayer


SOURCE_ID = "OPERATOR_UNDERWRITING"
COLLECTOR_ID = "operator-declared-underwriting"
_MIN_RATIONALE_CHARS = 20


class DeclaredUnderwritingError(ValueError):
    """Raised when a declared-underwriting file violates its contract."""


def load_declared_underwriting(path: str | Path) -> dict:
    raw = Path(path).read_text(encoding="utf-8")
    payload = yaml.safe_load(raw)
    if not isinstance(payload, Mapping):
        raise DeclaredUnderwritingError("declared underwriting must be a mapping")
    target_id = str(payload.get("target_id") or "")
    as_of = str(payload.get("as_of") or "")
    source_ref = str(payload.get("source_ref") or "")
    declarations = payload.get("declarations")
    if not target_id or not as_of or not source_ref:
        raise DeclaredUnderwritingError(
            "declared underwriting requires target_id, as_of and source_ref"
        )
    if not source_ref.startswith("http"):
        raise DeclaredUnderwritingError(
            "declared underwriting source_ref must be an HTTP provenance link "
            "(the report's source-link contract verifies every Evidence ref)"
        )
    if not isinstance(declarations, Mapping) or not declarations:
        raise DeclaredUnderwritingError(
            "declared underwriting requires a declarations mapping"
        )
    for metric, row in declarations.items():
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
    return {
        "target_id": target_id,
        "as_of": as_of,
        "source_ref": source_ref,
        "declarations": dict(declarations),
        "file_sha256": sha256(raw.encode("utf-8")).hexdigest(),
    }


def declared_underwriting_collector(path: str | Path):
    """EvidenceCollector serving the operator's declared judgments for one run."""
    payload = load_declared_underwriting(path)

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        if request.target_id != payload["target_id"]:
            raise DeclaredUnderwritingError(
                f"declared underwriting is bound to {payload['target_id']}, "
                f"not {request.target_id}; refusing cross-company reuse"
            )
        declarations = payload["declarations"]
        records = []
        for metric in request.required_metrics:
            row = declarations.get(metric)
            if row is None:
                continue  # undeclared: a named coverage gap downstream
            records.append(
                EvidenceRecord(
                    id=f"UW:{payload['target_id']}:{metric}",
                    target=payload["target_id"],
                    metric=metric,
                    value=row["value"],
                    unit=str(row["unit"]),
                    source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                    effective_date=payload["as_of"],
                    observed_date=payload["as_of"],
                    source_name="operator declared underwriting",
                    source_ref=payload["source_ref"],
                    source_grade="B",
                    confidence=float(row.get("confidence", 0.6)),
                    segment=str(row.get("segment", "core")),
                    notes=(
                        "analyst_declared_judgment; rationale="
                        + str(row["rationale"]).strip()
                    ),
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


def declared_underwriting_provider(path: str | Path) -> LiveCollectorProvider:
    payload = load_declared_underwriting(path)
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
        collector=declared_underwriting_collector(path),
    )
