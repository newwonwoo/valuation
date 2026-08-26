from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any
from urllib.parse import urlparse

from .assumption_compiler import CompiledAssumptionSet
from .control_plane import ExecutionMode, authorize_post_freeze
from .evidence_collection import PrimaryEvidenceCollectionResult
from .generic_audit import GenericAuditResult
from .ledger import EvidenceLedger
from .live_primary_adapters import IndustryKnowledgeSnapshot
from .orchestrator import ControlledRunResult
from .records import AuditReport
from .scenario_binding import BoundScenarioSet
from .valuation_execution import GenericValuationResult


_HASH64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SourceDocumentLineage:
    source_ref: str
    document_hash: str
    first_seen_at: str

    def validate(self) -> None:
        parsed = urlparse(self.source_ref)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source document lineage requires an absolute HTTP(S) source_ref")
        if not _HASH64.fullmatch(self.document_hash.casefold()):
            raise ValueError("source document lineage requires exact SHA-256 document_hash")
        timestamp = datetime.fromisoformat(self.first_seen_at.replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("source document first_seen_at must be timezone-aware")


def serialize_live_company_success(
    result: ControlledRunResult,
    *,
    company_id: str,
    source_documents: tuple[SourceDocumentLineage, ...],
) -> dict[str, Any]:
    """Serialize a successful real LIVE_PRIMARY run into the acceptance artifact contract.

    The producer never manufactures proof hashes. Each proof is replayed against the exact
    hash already frozen by the runtime before the artifact is emitted.
    """
    if not company_id:
        raise ValueError("live company artifact requires company_id")
    if result.execution_mode is not ExecutionMode.LIVE_PRIMARY:
        raise ValueError("success artifact requires LIVE_PRIMARY ControlledRunResult")
    if result.blocked_reasons or not result.completed:
        raise ValueError("success artifact requires an unblocked completed run")
    if result.freeze_token is None:
        raise ValueError("success artifact requires IntrinsicFreezeToken")
    authorize_post_freeze(result.freeze_token, run_id=result.run_id)
    if not source_documents:
        raise ValueError("success artifact requires source document lineage")
    for document in source_documents:
        document.validate()

    ledger = _require_type(result.data, "evidence_ledger", EvidenceLedger)
    compiled = _require_type(result.data, "compiled_assumption_set", CompiledAssumptionSet)
    scenarios = _require_type(result.data, "bound_scenario_set", BoundScenarioSet)
    valuation = _require_type(result.data, "generic_valuation_result", GenericValuationResult)
    industry = _require_type(result.data, "industry_knowledge_snapshot", IndustryKnowledgeSnapshot)
    collection = _require_type(result.data, "evidence_collection_result", PrimaryEvidenceCollectionResult)
    audit_report = _require_type(result.data, "generic_audit_report", AuditReport)
    audit_hash = str(result.data.get("audit_hash") or "")
    if not audit_hash:
        raise ValueError("success artifact requires audit_hash")

    proofs = {
        "ledger_snapshot_hash": {
            "encoding": "canonical_json",
            "payload": ledger.to_list(),
        },
        "assumption_set_hash": {
            "encoding": "canonical_json",
            "payload": _compiled_assumption_payload(compiled),
        },
        "valuation_hash": {
            "encoding": "utf8",
            "preimage": _valuation_hash_preimage(scenarios, valuation),
        },
        "audit_hash": {
            "encoding": "utf8",
            "preimage": _audit_hash_preimage(
                run_id=result.run_id,
                ledger_snapshot_hash=result.freeze_token.ledger_snapshot_hash,
                assumption_set_hash=compiled.assumption_set_hash,
                scenario_set_hash=scenarios.scenario_set_hash,
                valuation_hash=valuation.valuation_hash,
                report=audit_report,
            ),
        },
        "industry_snapshot_hash": {
            "encoding": "utf8",
            "preimage": _industry_hash_preimage(industry),
        },
        "source_snapshot_hash": {
            "encoding": "canonical_json",
            "payload": _source_snapshot_payload(collection),
        },
    }
    hashes = {
        "ledger_snapshot_hash": result.freeze_token.ledger_snapshot_hash,
        "assumption_set_hash": result.freeze_token.assumption_set_hash,
        "valuation_hash": result.freeze_token.valuation_hash,
        "audit_hash": result.freeze_token.audit_hash,
        "industry_snapshot_hash": result.freeze_token.industry_snapshot_hash,
        "source_snapshot_hash": result.freeze_token.source_snapshot_hash,
    }
    for field, expected in hashes.items():
        recomputed = recompute_artifact_hash_proof(proofs[field])
        if recomputed != expected:
            raise ValueError(
                f"runtime {field} cannot be replayed from the ControlledRunResult: "
                f"expected {expected}, recomputed {recomputed}"
            )

    if compiled.assumption_set_hash != hashes["assumption_set_hash"]:
        raise ValueError("compiled assumption hash does not match Freeze token")
    if valuation.valuation_hash != hashes["valuation_hash"]:
        raise ValueError("valuation hash does not match Freeze token")
    if audit_hash != hashes["audit_hash"]:
        raise ValueError("audit hash does not match Freeze token")
    if industry.snapshot_hash != hashes["industry_snapshot_hash"]:
        raise ValueError("industry snapshot hash does not match Freeze token")
    if collection.source_snapshot_hash != hashes["source_snapshot_hash"]:
        raise ValueError("source snapshot hash does not match Freeze token")

    market_comparison = result.data.get("market_comparison")
    final_report = result.data.get("final_report")
    if market_comparison is None:
        raise ValueError("success artifact requires post-freeze market_comparison")
    if final_report is None:
        raise ValueError("success artifact requires final_report")

    artifact: dict[str, Any] = {
        "artifact_type": "serialized_controlled_run/v1",
        "company_id": company_id,
        "synthetic": False,
        "run_id": result.run_id,
        "execution_mode": result.execution_mode.name,
        "stage_traces": _serialized_traces(result),
        "blocked_reasons": [],
        "freeze_token": _jsonable(result.freeze_token),
        "data_hashes": hashes,
        "hash_proofs": proofs,
        "source_documents": [_jsonable(item) for item in source_documents],
        "market_compare": {
            "phase": "post_freeze",
            "freeze_token_id": result.freeze_token.token_hash,
            "payload": _jsonable(market_comparison),
        },
        "final_report": _jsonable(final_report),
    }
    artifact["run_integrity_hash"] = _stable_hash(artifact)
    return artifact


def serialize_live_company_blocked(
    result: ControlledRunResult,
    *,
    company_id: str,
    adversarial_case_id: str,
    expected_reason_contains: str,
) -> dict[str, Any]:
    """Serialize a real fail-closed LIVE_PRIMARY run for the adversarial acceptance fixture."""
    if not all((company_id, adversarial_case_id, expected_reason_contains)):
        raise ValueError("blocked artifact requires company and adversarial case identity")
    if result.execution_mode is not ExecutionMode.LIVE_PRIMARY:
        raise ValueError("blocked artifact requires LIVE_PRIMARY ControlledRunResult")
    if not result.blocked_reasons or not result.stage_traces:
        raise ValueError("blocked artifact requires a blocking ControlledRunResult")
    if result.freeze_token is not None:
        raise ValueError("blocked artifact must not contain a Freeze token")
    last = result.stage_traces[-1]
    if not last.blocking:
        raise ValueError("blocked artifact must terminate on a blocking stage")
    if not any(expected_reason_contains in reason for reason in result.blocked_reasons):
        raise ValueError("blocked artifact does not contain the expected blocker reason")

    artifact: dict[str, Any] = {
        "artifact_type": "serialized_controlled_run/v1",
        "company_id": company_id,
        "synthetic": False,
        "run_id": result.run_id,
        "execution_mode": result.execution_mode.name,
        "stage_traces": _serialized_traces(result),
        "blocked_reasons": list(result.blocked_reasons),
        "freeze_token": None,
        "adversarial_case": {
            "id": adversarial_case_id,
            "expected_block_stage": last.stage,
            "expected_reason_contains": expected_reason_contains,
        },
    }
    artifact["run_integrity_hash"] = _stable_hash(artifact)
    return artifact


def recompute_artifact_hash_proof(proof: dict[str, Any]) -> str:
    encoding = str(proof.get("encoding") or "")
    if not encoding and "payload" in proof:
        encoding = "canonical_json"  # v1 test/backward-compatible proof shape
    if encoding == "canonical_json":
        if "payload" not in proof:
            raise ValueError("canonical_json hash proof requires payload")
        return _stable_hash(proof["payload"])
    if encoding == "utf8":
        preimage = proof.get("preimage")
        if not isinstance(preimage, str):
            raise ValueError("utf8 hash proof requires string preimage")
        return sha256(preimage.encode("utf-8")).hexdigest()
    raise ValueError(f"unsupported hash proof encoding: {encoding!r}")


def _compiled_assumption_payload(compiled: CompiledAssumptionSet) -> dict[str, Any]:
    return {
        "contract": "compiled_assumption_set/v2",
        "target_id": compiled.target_id,
        "assumptions": [
            {
                "key": item.key,
                "scenario_id": item.scenario_id,
                "measure": {
                    "amount": str(item.measure.amount),
                    "unit": item.measure.unit,
                    "as_of": item.measure.as_of,
                },
                "bridge_id": item.bridge_id,
                "evidence_ids": list(item.evidence_ids),
                "hypothesis_id": item.hypothesis_id,
                "economic_path_id": item.economic_path_id,
                "transform_id": item.transform_id,
                "input_evidence_hash": item.input_evidence_hash,
                "calibration_status": (
                    item.calibration_status.value
                    if item.calibration_status is not None
                    else None
                ),
            }
            for item in sorted(
                compiled.assumptions,
                key=lambda row: (row.scenario_id, row.key, row.bridge_id),
            )
        ],
    }


def _valuation_hash_preimage(
    scenario_set: BoundScenarioSet,
    valuation: GenericValuationResult,
) -> str:
    return "\n".join(
        [
            scenario_set.scenario_set_hash,
            valuation.reporting_unit,
            f"scope={valuation.scope.value}",
        ]
        + [
            (
                f"unvalued={item.asset_id}|{item.segment_id}|{item.status.value}|"
                f"{item.resolution_status}|{item.rationale}|"
                f"{','.join(item.missing_assumptions)}"
            )
            for item in valuation.unvalued_segments
        ]
        + [
            (
                f"{item.scenario_id}|{item.equity_value_amount}|"
                f"{item.diluted_shares}|{item.value_per_share}|"
                f"{item.aggregation_hash}|"
                f"{','.join(item.economic_path_ids)}"
            )
            for item in valuation.scenarios
        ]
        + [
            "expected="
            + (
                str(valuation.expected_value_per_share)
                if valuation.expected_value_per_share is not None
                else "NA"
            )
        ]
    )


def _audit_hash_preimage(
    *,
    run_id: str,
    ledger_snapshot_hash: str,
    assumption_set_hash: str,
    scenario_set_hash: str,
    valuation_hash: str,
    report: AuditReport,
) -> str:
    return "\n".join(
        [
            run_id,
            ledger_snapshot_hash,
            assumption_set_hash,
            scenario_set_hash,
            valuation_hash,
        ]
        + [
            f"{item.check}|{item.passed}|{item.blocking}|{item.detail}"
            for item in report.findings
        ]
    )


def _industry_hash_preimage(snapshot: IndustryKnowledgeSnapshot) -> str:
    return "\n".join(
        (
            snapshot.as_of,
            *sorted(snapshot.source_ids),
            *sorted(snapshot.document_ids),
            *sorted(snapshot.evidence_ids),
            *sorted(snapshot.content_hashes),
            *(
                item.fingerprint
                for item in sorted(
                    snapshot.evidence_lineage,
                    key=lambda value: value.evidence_id,
                )
            ),
        )
    )


def _source_snapshot_payload(result: PrimaryEvidenceCollectionResult) -> dict[str, Any]:
    batch_rows = [
        {
            "source_id": batch.source_id,
            "checked_at": batch.checked_at,
            "source_fingerprint": batch.source_fingerprint,
            "document_ids": sorted(batch.document_ids),
            "evidence_ids": sorted(item.id for item in batch.records),
        }
        for batch in result.batches
    ]
    batch_rows.sort(
        key=lambda row: json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return {
        "target_id": next(iter(result.ledger.records())).target,
        "batches": batch_rows,
        "evidence": sorted(
            result.ledger.to_list(),
            key=lambda item: str(item.get("id", "")),
        ),
    }


def _serialized_traces(result: ControlledRunResult) -> list[dict[str, Any]]:
    return [
        {
            "stage": trace.stage,
            "status": trace.status.value,
            "rationale": trace.rationale,
            "blocking": trace.blocking,
            "output_keys": list(trace.output_keys),
        }
        for trace in result.stage_traces
    ]


def _require_type(data: dict[str, Any], key: str, expected_type):
    value = data.get(key)
    if not isinstance(value, expected_type):
        raise ValueError(f"success artifact requires runtime {key}")
    return value


def _stable_hash(payload: Any) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
