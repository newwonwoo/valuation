from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

import yaml

from .live_company_artifact import recompute_artifact_hash_proof
from .orchestrator import load_stage_sequence


_REQUIRED_COMPANIES = ("OCI_HOLDINGS", "ORACLE", "BLOOM_ENERGY", "GE_VERNOVA")
_ALLOWED_STATUS = {"READY", "BLOCKED_SOURCE_FIXTURE"}
_REQUIRED_CONTRACT_FLAGS = (
    "full_live_primary_33_stage",
    "primary_evidence_lineage",
    "target_market_isolation_pre_freeze",
    "audit_and_intrinsic_freeze",
    "post_freeze_market_compare",
    "adversarial_blocked_fixture",
    "synthetic_placeholder_forbidden",
)
_HASH_FIELDS = (
    "ledger_snapshot_hash",
    "assumption_set_hash",
    "valuation_hash",
    "audit_hash",
    "industry_snapshot_hash",
    "source_snapshot_hash",
)
_HASH64 = re.compile(r"^[0-9a-f]{64}$")
_SUCCESS_STATUSES = {"pass", "warning", "skipped_not_applicable", "recovered"}
_BLOCKING_STATUSES = {
    "blocked",
    "not_implemented",
    "recovery_required",
    "awaiting_user_decision",
}


@dataclass(frozen=True)
class LiveCompanyAcceptanceSummary:
    ready: tuple[str, ...]
    blocked: tuple[str, ...]


def validate_live_company_acceptance(
    path: str | Path,
    *,
    repo_root: str | Path,
) -> LiveCompanyAcceptanceSummary:
    manifest_path = Path(path)
    root = Path(repo_root)
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or int(payload.get("version", 0)) != 2:
        raise ValueError("live company acceptance manifest requires version 2")
    contract = payload.get("required_contract")
    if not isinstance(contract, dict):
        raise ValueError("live company acceptance manifest requires required_contract")
    for flag in _REQUIRED_CONTRACT_FLAGS:
        if contract.get(flag) is not True:
            raise ValueError(f"live company acceptance contract flag must be true: {flag}")

    companies = payload.get("companies")
    if not isinstance(companies, dict):
        raise ValueError("live company acceptance manifest requires companies mapping")
    if tuple(sorted(companies)) != tuple(sorted(_REQUIRED_COMPANIES)):
        missing = sorted(set(_REQUIRED_COMPANIES) - set(companies))
        extra = sorted(set(companies) - set(_REQUIRED_COMPANIES))
        raise ValueError(
            f"live company acceptance company set mismatch: missing={missing}, extra={extra}"
        )

    canonical_stages = load_stage_sequence(root / "config" / "control_plane_stage_registry.yaml")
    ready: list[str] = []
    blocked: list[str] = []
    for company_id in _REQUIRED_COMPANIES:
        row = companies[company_id]
        if not isinstance(row, dict):
            raise ValueError(f"acceptance row {company_id} must be a mapping")
        status = str(row.get("status") or "")
        display_name = str(row.get("display_name") or "")
        success_path = str(row.get("success_fixture_path") or "")
        adversarial_path = str(row.get("adversarial_fixture_path") or "")
        if not all((display_name, success_path, adversarial_path)):
            raise ValueError(
                f"acceptance row {company_id} requires display_name and both fixture paths"
            )
        if status not in _ALLOWED_STATUS:
            raise ValueError(f"acceptance row {company_id} has invalid status {status!r}")

        success_file = root / success_path
        blocked_file = root / adversarial_path
        if status == "BLOCKED_SOURCE_FIXTURE":
            blocker = str(row.get("blocker") or "")
            if not blocker:
                raise ValueError(f"blocked acceptance row {company_id} requires blocker")
            if success_file.is_file():
                _validate_success_fixture(
                    company_id,
                    success_file,
                    canonical_stages,
                    expected_file_hash=str(row.get("success_fixture_sha256") or ""),
                )
            if blocked_file.is_file():
                _validate_adversarial_fixture(
                    company_id,
                    blocked_file,
                    canonical_stages,
                    expected_file_hash=str(row.get("adversarial_fixture_sha256") or ""),
                )
            blocked.append(company_id)
            continue

        _validate_success_fixture(
            company_id,
            success_file,
            canonical_stages,
            expected_file_hash=str(row.get("success_fixture_sha256") or ""),
        )
        _validate_adversarial_fixture(
            company_id,
            blocked_file,
            canonical_stages,
            expected_file_hash=str(row.get("adversarial_fixture_sha256") or ""),
        )
        ready.append(company_id)

    return LiveCompanyAcceptanceSummary(tuple(ready), tuple(blocked))


def _validate_success_fixture(
    company_id: str,
    path: Path,
    canonical_stages: tuple[str, ...],
    *,
    expected_file_hash: str,
) -> None:
    payload = _load_hashed_fixture(path, expected_file_hash, company_id)
    _validate_common_artifact(payload, company_id)
    if payload.get("execution_mode") != "LIVE_PRIMARY":
        raise ValueError(f"READY fixture {company_id} must be LIVE_PRIMARY")
    if payload.get("blocked_reasons") != []:
        raise ValueError(f"READY fixture {company_id} must have no blocked reasons")

    traces = _require_traces(payload, company_id)
    trace_stages = tuple(str(row.get("stage") or "") for row in traces)
    if trace_stages != canonical_stages:
        raise ValueError(f"READY fixture {company_id} must contain the exact canonical 33-stage sequence")
    for row in traces:
        status = str(row.get("status") or "").casefold()
        if status not in _SUCCESS_STATUSES or bool(row.get("blocking")):
            raise ValueError(
                f"READY fixture {company_id} contains unresolved stage {row.get('stage')}: {status}"
            )
    by_stage = {str(row["stage"]): row for row in traces}
    for required_pass in (
        "AUDIT_GATE",
        "INTRINSIC_VALUE_FREEZE",
        "MARKET_PRICE_LOAD",
        "MARKET_COMPARE",
        "FINAL_REPORT",
    ):
        if str(by_stage[required_pass].get("status") or "").casefold() != "pass":
            raise ValueError(
                f"READY fixture {company_id} requires PASS at {required_pass}"
            )

    freeze_index = canonical_stages.index("INTRINSIC_VALUE_FREEZE")
    if not (
        canonical_stages.index("AUDIT_GATE") < freeze_index
        < canonical_stages.index("MARKET_PRICE_LOAD")
        < canonical_stages.index("MARKET_COMPARE")
        < canonical_stages.index("FINAL_REPORT")
    ):
        raise ValueError("canonical Audit/Freeze/Market order is invalid")

    run_id = str(payload.get("run_id") or "")
    token = payload.get("freeze_token")
    hashes = payload.get("data_hashes")
    proofs = payload.get("hash_proofs")
    if not isinstance(token, dict) or not isinstance(hashes, dict) or not isinstance(proofs, dict):
        raise ValueError(f"READY fixture {company_id} requires Freeze token, data hashes and hash proofs")
    if token.get("run_id") != run_id:
        raise ValueError(f"READY fixture {company_id} Freeze token run_id mismatch")
    for field in _HASH_FIELDS:
        value = str(hashes.get(field) or "").casefold()
        if not _HASH64.fullmatch(value):
            raise ValueError(f"READY fixture {company_id} has invalid {field}")
        if str(token.get(field) or "").casefold() != value:
            raise ValueError(f"READY fixture {company_id} Freeze token mismatch for {field}")
        proof = proofs.get(field)
        if not isinstance(proof, dict):
            raise ValueError(f"READY fixture {company_id} lacks recomputable proof for {field}")
        try:
            recomputed = recompute_artifact_hash_proof(proof)
        except ValueError as exc:
            raise ValueError(
                f"READY fixture {company_id} has invalid proof for {field}: {exc}"
            ) from exc
        if recomputed != value:
            raise ValueError(
                f"READY fixture {company_id} recomputed {field} does not match runtime hash"
            )

    token_hash = str(token.get("token_hash") or "").casefold()
    if not _HASH64.fullmatch(token_hash):
        raise ValueError(f"READY fixture {company_id} has invalid Freeze token_hash")
    expected_token_hash = sha256(
        "|".join((run_id, *(str(token[field]).casefold() for field in _HASH_FIELDS))).encode("utf-8")
    ).hexdigest()
    if token_hash != expected_token_hash:
        raise ValueError(f"READY fixture {company_id} Freeze token_hash mismatch")

    source_documents = payload.get("source_documents")
    if not isinstance(source_documents, list) or not source_documents:
        raise ValueError(f"READY fixture {company_id} requires source document lineage")
    source_by_ref: dict[str, str] = {}
    for row in source_documents:
        if not isinstance(row, dict):
            raise ValueError(f"READY fixture {company_id} source document must be mapping")
        source_ref = str(row.get("source_ref") or "")
        first_seen = str(row.get("first_seen_at") or "")
        if not source_ref or not first_seen:
            raise ValueError(f"READY fixture {company_id} source document lineage is incomplete")
        parsed = urlparse(source_ref)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"READY fixture {company_id} source document must use absolute HTTP(S) provenance")
        document_hash = str(row.get("document_hash") or "").casefold()
        if not _HASH64.fullmatch(document_hash):
            raise ValueError(f"READY fixture {company_id} source document hash is invalid")
        if source_ref in source_by_ref:
            raise ValueError(f"READY fixture {company_id} contains duplicate source document refs")
        source_by_ref[source_ref] = document_hash

    _validate_evidence_revision_bindings(
        company_id,
        payload,
        proofs,
        source_by_ref,
    )

    market_compare = payload.get("market_compare")
    if not isinstance(market_compare, dict) or market_compare.get("phase") != "post_freeze":
        raise ValueError(f"READY fixture {company_id} requires explicit post-freeze market comparison")
    if market_compare.get("freeze_token_id") != token_hash:
        raise ValueError(f"READY fixture {company_id} market comparison Freeze token mismatch")
    if market_compare.get("payload") in (None, {}, []):
        raise ValueError(f"READY fixture {company_id} market comparison payload is empty")
    if payload.get("final_report") in (None, {}, [], ""):
        raise ValueError(f"READY fixture {company_id} requires serialized final_report")


def _validate_evidence_revision_bindings(
    company_id: str,
    payload: dict[str, Any],
    proofs: dict[str, Any],
    source_by_ref: dict[str, str],
) -> None:
    active_ids = payload.get("active_evidence_ids")
    bindings = payload.get("evidence_revision_bindings")
    if (
        not isinstance(active_ids, list)
        or not active_ids
        or not all(isinstance(item, str) and item for item in active_ids)
        or len(active_ids) != len(set(active_ids))
    ):
        raise ValueError(f"READY fixture {company_id} requires unique active_evidence_ids")
    if not isinstance(bindings, list) or not bindings:
        raise ValueError(f"READY fixture {company_id} requires Evidence revision bindings")

    ledger_proof = proofs.get("ledger_snapshot_hash")
    ledger_rows = ledger_proof.get("payload") if isinstance(ledger_proof, dict) else None
    if not isinstance(ledger_rows, list):
        raise ValueError(f"READY fixture {company_id} ledger proof must expose canonical Evidence rows")
    ledger_by_id = {
        str(row.get("id") or ""): row
        for row in ledger_rows
        if isinstance(row, dict) and str(row.get("id") or "")
    }
    if not set(active_ids).issubset(ledger_by_id):
        raise ValueError(f"READY fixture {company_id} active Evidence is missing from ledger proof")

    binding_by_id: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        if not isinstance(binding, dict):
            raise ValueError(f"READY fixture {company_id} Evidence revision binding must be mapping")
        evidence_id = str(binding.get("evidence_id") or "")
        source_ref = str(binding.get("source_ref") or "")
        revision_hash = str(binding.get("revision_hash") or "").casefold()
        if not evidence_id or evidence_id in binding_by_id:
            raise ValueError(f"READY fixture {company_id} Evidence revision bindings contain duplicate/missing IDs")
        if not _HASH64.fullmatch(revision_hash):
            raise ValueError(f"READY fixture {company_id} Evidence {evidence_id} has invalid revision hash")
        if source_by_ref.get(source_ref) != revision_hash:
            raise ValueError(
                f"READY fixture {company_id} Evidence {evidence_id} revision is not bound to source document lineage"
            )
        ledger_ref = str(ledger_by_id.get(evidence_id, {}).get("source_ref") or "").split("#", 1)[0]
        if ledger_ref != source_ref:
            raise ValueError(
                f"READY fixture {company_id} Evidence {evidence_id} source_ref does not match ledger proof"
            )
        binding_by_id[evidence_id] = binding
    if set(binding_by_id) != set(active_ids):
        raise ValueError(f"READY fixture {company_id} every active Evidence requires exactly one revision binding")


def _validate_adversarial_fixture(
    company_id: str,
    path: Path,
    canonical_stages: tuple[str, ...],
    *,
    expected_file_hash: str,
) -> None:
    payload = _load_hashed_fixture(path, expected_file_hash, company_id)
    _validate_common_artifact(payload, company_id)
    if payload.get("execution_mode") != "LIVE_PRIMARY":
        raise ValueError(f"adversarial fixture {company_id} must be LIVE_PRIMARY")
    if payload.get("freeze_token") is not None:
        raise ValueError(f"adversarial fixture {company_id} must not issue a Freeze token")
    blocked_reasons = payload.get("blocked_reasons")
    if not isinstance(blocked_reasons, list) or not blocked_reasons:
        raise ValueError(f"adversarial fixture {company_id} requires blocked reasons")

    traces = _require_traces(payload, company_id)
    trace_stages = tuple(str(row.get("stage") or "") for row in traces)
    if not trace_stages or trace_stages != canonical_stages[: len(trace_stages)]:
        raise ValueError(
            f"adversarial fixture {company_id} traces must be a canonical stage prefix"
        )
    last = traces[-1]
    last_status = str(last.get("status") or "").casefold()
    if last_status not in _BLOCKING_STATUSES or not bool(last.get("blocking")):
        raise ValueError(f"adversarial fixture {company_id} must terminate on a blocking stage")

    case = payload.get("adversarial_case")
    if not isinstance(case, dict) or not str(case.get("id") or ""):
        raise ValueError(f"adversarial fixture {company_id} requires adversarial_case")
    expected_stage = str(case.get("expected_block_stage") or "")
    reason_fragment = str(case.get("expected_reason_contains") or "")
    if expected_stage != str(last.get("stage") or ""):
        raise ValueError(f"adversarial fixture {company_id} blocked at unexpected stage")
    if not reason_fragment or not any(reason_fragment in str(reason) for reason in blocked_reasons):
        raise ValueError(f"adversarial fixture {company_id} does not prove expected blocker")


def _validate_common_artifact(payload: dict[str, Any], company_id: str) -> None:
    if payload.get("artifact_type") != "serialized_controlled_run/v1":
        raise ValueError(f"fixture {company_id} has unsupported artifact_type")
    if payload.get("company_id") != company_id:
        raise ValueError(f"fixture {company_id} company_id mismatch")
    if payload.get("synthetic") is not False:
        raise ValueError(f"fixture {company_id} must explicitly declare synthetic=false")
    if not str(payload.get("run_id") or ""):
        raise ValueError(f"fixture {company_id} requires run_id")
    expected = str(payload.get("run_integrity_hash") or "").casefold()
    if not _HASH64.fullmatch(expected):
        raise ValueError(f"fixture {company_id} requires run_integrity_hash")
    body = dict(payload)
    body.pop("run_integrity_hash", None)
    if _stable_hash(body) != expected:
        raise ValueError(f"fixture {company_id} run_integrity_hash mismatch")


def _load_hashed_fixture(path: Path, expected_hash: str, company_id: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"acceptance fixture {company_id} is missing: {path}")
    expected = expected_hash.casefold()
    if not _HASH64.fullmatch(expected):
        raise ValueError(f"acceptance row {company_id} requires exact fixture SHA-256")
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != expected:
        raise ValueError(f"acceptance fixture {company_id} file SHA-256 mismatch")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"acceptance fixture {company_id} must be a JSON mapping")
    return payload


def _require_traces(payload: dict[str, Any], company_id: str) -> list[dict[str, Any]]:
    traces = payload.get("stage_traces")
    if not isinstance(traces, list) or not traces or not all(isinstance(row, dict) for row in traces):
        raise ValueError(f"fixture {company_id} requires serialized stage traces")
    return traces


def _stable_hash(payload: Any) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
