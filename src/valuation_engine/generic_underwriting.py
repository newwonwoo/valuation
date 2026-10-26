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
from typing import TYPE_CHECKING, Mapping

import yaml

from .collection_plan import CollectorCapability
from .evidence_collection import EvidenceCollectionBatch, EvidenceCollectionRequest
from .live_runtime import LiveCollectorProvider
from .records import EvidenceRecord, EvidenceSourceLayer
from .source_reporting import canonical_verification_url

if TYPE_CHECKING:
    from .derived_case import DerivedCaseRegistry


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


@dataclass(frozen=True)
class ResearchUnderwritingEvidenceRecord(DeclaredUnderwritingEvidenceRecord):
    """Structured research lineage survives ledger persistence and audit."""

    research_receipt: Mapping | None = None
    business_cashflow_receipt: Mapping | None = None


@dataclass(frozen=True)
class DerivedCaseUnderwritingEvidenceRecord(ResearchUnderwritingEvidenceRecord):
    """One replayed case output, not a separately approved observed fact.

    The large driver/review receipt lives once in a hash-bound artifact. The
    small proof is useful for persistence, but cannot itself authorize entry:
    the derived-case verifier must have actually replayed the registered model.
    """

    derived_case_ref: Mapping | None = None
    derived_case_proof: Mapping | None = None
    scenario_id: str = ""
    year: int | None = None
    economic_path_id: str = ""


def _assert_nested_revision(research: Mapping, outer: Mapping) -> None:
    """Prevent loss or replacement of informed lineage at the file boundary."""
    from .research_campaign import fingerprint

    order = research.get("work_order", {})
    response = research.get("response", {})
    policy = research.get("broker_source_policy", {})
    informed = (
        isinstance(order, Mapping)
        and (order.get("research_mode") == "INFORMED_REVISION" or "informed_revision" in order)
    ) or (isinstance(response, Mapping) and "informed_assumption" in response) or (
        isinstance(policy, Mapping) and policy.get("research_mode") == "INFORMED_REVISION"
    )
    if not informed:
        return
    if (outer.get("research_mode") != "INFORMED_REVISION"
            or not isinstance(order, Mapping)
            or order.get("research_mode") != "INFORMED_REVISION"):
        raise DeclaredUnderwritingError("nested informed research cannot enter a blind underwriting file")
    if fingerprint(order.get("informed_revision")) != fingerprint(outer.get("informed_revision")):
        raise DeclaredUnderwritingError("nested informed research differs from outer revision lineage")


def _resolved_derived_reference(reference: object, path: str | Path) -> dict:
    if (not isinstance(reference, Mapping) or set(reference) != {
        "schema_version", "artifact_path", "artifact_sha256", "output_key"
    } or reference.get("schema_version") != "derived-case-reference/v1"):
        raise DeclaredUnderwritingError("derived_case_ref requires exact artifact/hash/output reference fields")
    if not isinstance(reference["artifact_path"], str) or not reference["artifact_path"].strip():
        raise DeclaredUnderwritingError("derived_case_ref requires an artifact path")
    normalized = dict(reference)
    artifact_path = Path(str(reference.get("artifact_path") or ""))
    if not artifact_path.is_absolute():
        artifact_path = Path(path).resolve().parent / artifact_path
    normalized["artifact_path"] = str(artifact_path)
    return normalized


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
    from .research_campaign import _revision_context, fingerprint
    try:
        outer_review = _revision_context(payload)
    except ValueError as exc:
        raise DeclaredUnderwritingError(f"invalid underwriting revision context: {exc}") from exc
    normalized_declarations: dict[object, list[dict]] = {}
    derived_receipts: dict[tuple[str, str], dict] = {}
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
            # Derived business FCFF receipts retain their constituent research
            # orders too. Neither direct nor derived informed inputs may lose
            # outer execution mode/lineage at this final file-ingress boundary.
            research_items = [row.get("research_receipt")]
            business = row.get("business_cashflow_receipt")
            if isinstance(business, Mapping):
                assumptions = business.get("assumptions", {})
                if isinstance(assumptions, Mapping):
                    research_items.extend(assumptions.values())
            derived_ref = row.get("derived_case_ref")
            if derived_ref is not None:
                from .derived_case import load_derived_case_reference

                derived_ref = _resolved_derived_reference(derived_ref, path)
                key = (derived_ref["artifact_path"], str(derived_ref.get("artifact_sha256", "")))
                try:
                    if key not in derived_receipts:
                        derived_receipts[key] = load_derived_case_reference(derived_ref)
                    derived = derived_receipts[key]
                    if derived_ref["output_key"] not in derived.get("outputs", {}):
                        raise DeclaredUnderwritingError("unknown derived output key")
                except (ValueError, OSError, TypeError, KeyError) as exc:
                    raise DeclaredUnderwritingError(f"invalid derived-case reference: {exc}") from exc
                if (outer_review is None or derived.get("research_mode") != "INFORMED_REVISION"
                        or fingerprint(derived.get("informed_revision")) != fingerprint(payload.get("informed_revision"))):
                    raise DeclaredUnderwritingError("nested informed derived case differs from outer revision lineage")
                if (derived.get("target_id"), derived.get("as_of")) != (target_id, as_of):
                    raise DeclaredUnderwritingError("derived-case receipt target/cutoff mismatch")
                drivers = derived.get("drivers")
                if not isinstance(drivers, Mapping) or not drivers:
                    raise DeclaredUnderwritingError("derived-case receipt requires reviewed driver receipts")
                research_items.extend(drivers.values())
            for research in research_items:
                if not isinstance(research, Mapping):
                    continue
                _assert_nested_revision(research, payload)
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
                    **({"derived_case_ref": derived_ref} if derived_ref is not None else {}),
                }
            )
        normalized_declarations[metric] = normalized_rows
    return {
        "target_id": target_id,
        "as_of": as_of,
        "source_ref": source_ref,
        "declarations": normalized_declarations,
        "file_sha256": sha256(raw.encode("utf-8")).hexdigest(),
        "research_mode": payload.get("research_mode", "BLIND_BASELINE"),
        **({"informed_revision": payload["informed_revision"]} if outer_review is not None else {}),
    }


def declared_underwriting_collector(
    path: str | Path, *, run_as_of: str | None = None,
    trusted_derived_case_registry: DerivedCaseRegistry | None = None,
):
    """EvidenceCollector serving the operator's declared judgments for one run."""
    payload = load_declared_underwriting(path)
    if run_as_of is not None:
        _assert_knowable_by(payload, run_as_of)
    if trusted_derived_case_registry is None and any(
        row.get("derived_case_ref") is not None
        for rows in payload["declarations"].values() for row in rows
    ):
        raise DeclaredUnderwritingError(
            "WORK_REQUIRED: derived-case declarations require an orchestrator-supplied "
            "trusted_derived_case_registry; register the reviewed model callable, code "
            "hash, fixed baseline inputs and driver/output dependency contracts before compilation"
        )
    replayed_cases = {}

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        if request.target_id != payload["target_id"]:
            raise DeclaredUnderwritingError(
                f"declared underwriting is bound to {payload['target_id']}, "
                f"not {request.target_id}; refusing cross-company reuse"
            )
        declarations = payload["declarations"]
        records = []
        checked_derived_receipts = set()
        for metric in request.required_metrics:
            rows = declarations.get(metric)
            if rows is None:
                continue  # undeclared: a named coverage gap downstream
            for row in rows:
                source_refs = tuple(row["source_refs"])
                research = row.get("research_receipt")
                business = row.get("business_cashflow_receipt")
                derived_ref = row.get("derived_case_ref")
                record_type = DeclaredUnderwritingEvidenceRecord
                extra = {}
                if sum(item is not None for item in (research, business, derived_ref)) > 1:
                    raise DeclaredUnderwritingError("one declaration cannot have multiple research authorities")
                if derived_ref is not None:
                    from .derived_case import load_derived_case_reference, verify_derived_case_receipt
                    from .runtime_authority import DecisionDomain, forbid_llm_decision

                    # A cached deterministic replay is not permission for an
                    # LLM proposal actor to emit compiled underwriting rows.
                    forbid_llm_decision(DecisionDomain.ASSUMPTION_COMPILE)
                    key = (derived_ref["artifact_path"], derived_ref["artifact_sha256"])
                    try:
                        if key not in checked_derived_receipts:
                            receipt = load_derived_case_reference(derived_ref)
                            checked_derived_receipts.add(key)
                        if key not in replayed_cases:
                            replayed_cases[key] = verify_derived_case_receipt(
                                receipt, trusted_derived_case_registry
                            )
                        output = replayed_cases[key].output(derived_ref["output_key"])
                    except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
                        raise DeclaredUnderwritingError(f"derived-case replay failed: {exc}") from exc
                    if (str(row["value"]), row["unit"], str(metric), row.get("segment", "core"),
                            row.get("scenario_id"), row.get("year"), row.get("economic_path_id"), source_refs) != (
                            output.value, output.unit, output.metric, output.segment_id,
                            output.scenario_id, output.year, output.economic_path_id, output.source_refs
                    ) or (row.get("year") is not None and type(row["year"]) is not int):
                        raise DeclaredUnderwritingError(
                            "derived-case output does not bind exact declaration value/unit/metric/"
                            "segment/scenario/year/economic path/source refs"
                        )
                    record_type = DerivedCaseUnderwritingEvidenceRecord
                    extra = {"derived_case_ref": dict(derived_ref), "derived_case_proof": dict(output.proof),
                             "scenario_id": output.scenario_id, "year": output.year,
                             "economic_path_id": output.economic_path_id}
                if research is not None:
                    from .research_campaign import validate_response, fingerprint

                    if not isinstance(research, Mapping):
                        raise DeclaredUnderwritingError("research receipt must be an object")
                    order = research.get("work_order", {})
                    if (order.get("target_id"), order.get("as_of")) != (
                        payload["target_id"], payload["as_of"]
                    ):
                        raise DeclaredUnderwritingError("research receipt target/cutoff mismatch")
                    verified = validate_response(order, research.get("response", {}))
                    if fingerprint(verified) != fingerprint(research):
                        raise DeclaredUnderwritingError("research receipt was altered")
                    if (str(row["value"]), row["unit"], str(metric), row.get("segment", "core")) != (
                        verified["value"], verified["unit"], verified["metric"], verified["segment"]
                    ) or source_refs != tuple(dict.fromkeys(s["url"] for s in verified["response"]["sources"])):
                        raise DeclaredUnderwritingError("research receipt does not bind the declaration")
                    record_type = ResearchUnderwritingEvidenceRecord
                    extra = {"research_receipt": research}
                if business is not None:
                    from .new_business_research import validate_cashflow_receipt
                    from .research_campaign import validate_response, fingerprint

                    validate_cashflow_receipt(business, str(metric), str(row["value"]), str(row["unit"]))
                    if business["path"]["periods"][0]["year"] != int(payload["as_of"][:4]) + 1:
                        raise DeclaredUnderwritingError("cashflow receipt must begin in the year after underwriting as_of")
                    if set(source_refs) != set(business.get("source_refs", ())):
                        raise DeclaredUnderwritingError("cashflow declaration sources do not match receipt")
                    for item in business.get("assumptions", {}).values():
                        if item.get("status") != "ACCEPTED":
                            continue
                        order = item["work_order"]
                        if (order["target_id"], order["as_of"]) != (payload["target_id"], payload["as_of"]):
                            raise DeclaredUnderwritingError("cashflow research target/cutoff mismatch")
                        if fingerprint(validate_response(order, item["response"])) != fingerprint(item):
                            raise DeclaredUnderwritingError("cashflow research receipt changed")
                    record_type = ResearchUnderwritingEvidenceRecord
                    extra = {"business_cashflow_receipt": business}
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
                    record_type(
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
                    **extra,
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
    path: str | Path, *, run_as_of: str | None = None,
    trusted_derived_case_registry: DerivedCaseRegistry | None = None,
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
        collector=declared_underwriting_collector(
            path, run_as_of=run_as_of, trusted_derived_case_registry=trusted_derived_case_registry
        ),
    )
