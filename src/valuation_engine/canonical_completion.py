"""Strict completion proof for a canonical LIVE_PRIMARY report bundle.

The runtime owns valuation, audit and freeze decisions.  This module owns the
last-mile publication gate: one run must produce every required proof, and the
published bytes must still match those proofs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .runtime_authority import ExecutionAttestation, make_stage_receipt


PRODUCTION_STAGE_COUNT = 33
PASSING_STAGE_STATUSES = frozenset(
    {"pass", "warning", "skipped_not_applicable", "recovered"}
)
BUNDLE_MANIFEST_NAME = "report_bundle_manifest.json"
LATEST_MANIFEST_SCHEMA = "kr-live-latest-report/v2"
BUNDLE_MANIFEST_SCHEMA = "canonical-run-bundle/v2"
HEX64 = frozenset("0123456789abcdef")


class CompletionProofError(ValueError):
    """The published bytes do not prove a completed canonical run."""


@dataclass(frozen=True)
class CompletionProof:
    run_id: str
    ticker: str
    artifact_id: str
    stage_count: int
    valuation_hash: str
    audit_hash: str
    freeze_token_hash: str
    execution_attestation_hash: str
    bundle_manifest_sha256: str
    bundle_tree_sha256: str
    report_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 digest of one immutable artifact."""
    target = Path(path)
    try:
        return sha256(target.read_bytes()).hexdigest()
    except OSError as exc:
        raise CompletionProofError(f"cannot read bundle file: {target}") from exc


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompletionProofError(f"{label} is not valid UTF-8 JSON: {path}") from exc


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CompletionProofError(f"{label} must be a JSON object")
    return value


def _hex_digest(value: Any, label: str) -> str:
    text = str(value or "")
    if len(text) != 64 or any(char.casefold() not in HEX64 for char in text):
        raise CompletionProofError(f"{label} must be a 64-character SHA-256 digest")
    return text.casefold()


def expected_stage_sequence(stage_registry_path: str | Path) -> tuple[str, ...]:
    """Flatten the ordered phase registry into the canonical stage contract."""
    try:
        payload = yaml.safe_load(Path(stage_registry_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise CompletionProofError(
            f"stage registry is not readable: {stage_registry_path}"
        ) from exc
    phases = payload.get("phases") if isinstance(payload, Mapping) else None
    if not isinstance(phases, Mapping):
        raise CompletionProofError("stage registry has no ordered phases")
    stages: list[str] = []
    for phase, rows in phases.items():
        if not isinstance(rows, list) or not rows:
            raise CompletionProofError(f"stage registry phase {phase!r} is empty")
        for row in rows:
            if not isinstance(row, str) or not row.strip():
                raise CompletionProofError(f"stage registry phase {phase!r} has an invalid stage")
            stages.append(row)
    if not stages or len(stages) != len(set(stages)):
        raise CompletionProofError("stage registry has duplicate or missing stages")
    return tuple(stages)


def _trace_entries(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        value = value.get("stage_traces", value.get("traces"))
    if not isinstance(value, list):
        raise CompletionProofError("control_plane_trace.json must contain a trace list")
    entries: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        entry = _require_mapping(item, f"stage trace {index}")
        if not entry.get("stage") or not entry.get("status"):
            raise CompletionProofError(f"stage trace {index} lacks stage/status")
        if not isinstance(entry.get("blocking", False), bool):
            raise CompletionProofError(f"stage trace {index} has invalid blocking flag")
        output_keys = entry.get("output_keys", [])
        if not isinstance(output_keys, list) or not all(
            isinstance(key, str) for key in output_keys
        ):
            raise CompletionProofError(f"stage trace {index} has invalid output_keys")
        entries.append(entry)
    return tuple(entries)


def _validate_trace(
    trace: tuple[Mapping[str, Any], ...],
    expected: tuple[str, ...],
    *,
    run_id: str,
) -> None:
    if len(trace) != len(expected):
        raise CompletionProofError(
            f"canonical trace has {len(trace)} stages; expected {len(expected)}"
        )
    stages = tuple(str(item["stage"]) for item in trace)
    if stages != expected:
        raise CompletionProofError("canonical trace order does not match stage registry")
    for index, item in enumerate(trace):
        stage = stages[index]
        if str(item.get("status")) not in PASSING_STAGE_STATUSES:
            raise CompletionProofError(f"stage {stage} is not complete: {item.get('status')}")
        if item.get("blocking") is True:
            raise CompletionProofError(f"stage {stage} remains blocking")
        trace_run_id = item.get("run_id")
        if trace_run_id not in (None, "", run_id):
            raise CompletionProofError(f"stage {stage} belongs to another run")
        if not str(item.get("rationale") or "").strip():
            raise CompletionProofError(f"stage {stage} lacks rationale")


def _validate_audit(value: Any) -> None:
    audit = _require_mapping(value, "audit.json")
    passed = audit.get("pass")
    if passed is not None:
        if not isinstance(passed, bool):
            raise CompletionProofError("audit.json pass field is malformed")
        if not passed:
            raise CompletionProofError("audit.json reports failure")
    findings = audit.get("findings")
    if not isinstance(findings, list) or not findings:
        raise CompletionProofError("audit.json has no auditable findings")
    for index, finding in enumerate(findings):
        row = _require_mapping(finding, f"audit finding {index}")
        if not isinstance(row.get("passed"), bool) or not isinstance(
            row.get("blocking", False), bool
        ):
            raise CompletionProofError(f"audit finding {index} is malformed")
        if row.get("blocking") and not row["passed"]:
            raise CompletionProofError(f"blocking audit finding {index} failed")


def _safe_member(value: Any, label: str) -> str:
    name = str(value or "")
    path = Path(name)
    if not name or path.is_absolute() or ".." in path.parts:
        raise CompletionProofError(f"{label} is not a safe relative filename")
    if name.endswith("/"):
        raise CompletionProofError(f"{label} must name a file")
    return path.as_posix()


def _validate_receipts(
    bundle_dir: Path,
    bundle_manifest: Mapping[str, Any],
) -> tuple[str, str]:
    receipts = bundle_manifest.get("files")
    if not isinstance(receipts, list) or not receipts:
        raise CompletionProofError("bundle manifest has no file receipts")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(receipts):
        item = _require_mapping(raw, f"bundle file receipt {index}")
        filename = _safe_member(item.get("filename"), f"bundle receipt {index}")
        if filename == BUNDLE_MANIFEST_NAME:
            raise CompletionProofError("bundle manifest cannot receipt itself")
        if filename in seen:
            raise CompletionProofError(f"duplicate bundle receipt: {filename}")
        seen.add(filename)
        digest = _hex_digest(item.get("sha256"), f"bundle receipt {filename}")
        path = bundle_dir / filename
        if not path.is_file() or path.is_symlink():
            raise CompletionProofError(f"bundle receipt points to missing file: {filename}")
        actual = sha256_file(path)
        if actual != digest:
            raise CompletionProofError(f"bundle file hash mismatch: {filename}")
        normalized.append({"filename": filename, "sha256": digest})

    actual_files = {
        path.relative_to(bundle_dir).as_posix()
        for path in bundle_dir.rglob("*")
        if path.is_file() and not path.is_symlink() and path.name != BUNDLE_MANIFEST_NAME
    }
    if actual_files != seen:
        missing = sorted(actual_files - seen)
        extra = sorted(seen - actual_files)
        detail: list[str] = []
        if missing:
            detail.append("unlisted=" + ",".join(missing))
        if extra:
            detail.append("missing-files=" + ",".join(extra))
        raise CompletionProofError(
            "bundle receipts do not cover exact files: " + "; ".join(detail)
        )

    canonical_receipts = sorted(normalized, key=lambda item: item["filename"])
    encoded = json.dumps(
        canonical_receipts, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    report_filename = _safe_member(
        bundle_manifest.get("report_filename"), "bundle report_filename"
    )
    report_path = bundle_dir / report_filename
    if not report_path.is_file() or report_path.is_symlink():
        raise CompletionProofError("bundle report_filename points to a missing file")
    return sha256(encoded).hexdigest(), sha256_file(report_path)


def _validate_attestation(
    value: Any,
    *,
    run_id: str,
    freeze_token_hash: str,
    trace: tuple[Mapping[str, Any], ...],
) -> str:
    attestation = _require_mapping(value, "execution_attestation.json")
    if attestation.get("run_id") != run_id:
        raise CompletionProofError("execution attestation run_id mismatch")
    if attestation.get("execution_mode") != "live_primary":
        raise CompletionProofError("execution attestation is not LIVE_PRIMARY")
    if attestation.get("final_stage") != trace[-1]["stage"]:
        raise CompletionProofError("execution attestation final stage mismatch")
    if attestation.get("freeze_token_hash") != freeze_token_hash:
        raise CompletionProofError("execution attestation freeze lineage mismatch")
    hashes = attestation.get("stage_receipt_hashes")
    if not isinstance(hashes, list) or len(hashes) != len(trace):
        raise CompletionProofError("execution attestation stage receipt count mismatch")
    expected = tuple(
        make_stage_receipt(
            run_id=run_id,
            stage=str(item["stage"]),
            status=str(item["status"]),
            output_keys=tuple(str(key) for key in item.get("output_keys", [])),
        ).receipt_hash
        for item in trace
    )
    if tuple(hashes) != expected:
        raise CompletionProofError("execution attestation receipts do not match the trace")
    digest = _hex_digest(attestation.get("attestation_hash"), "execution attestation hash")
    try:
        ExecutionAttestation(
            run_id=run_id,
            execution_mode="live_primary",
            stage_receipt_hashes=tuple(hashes),
            freeze_token_hash=freeze_token_hash,
            final_stage=str(attestation["final_stage"]),
            attestation_hash=digest,
        ).validate()
    except (ValueError, PermissionError, KeyError) as exc:
        raise CompletionProofError(f"execution attestation is invalid: {exc}") from exc
    return digest


def _validate_latest(
    latest_path: Path,
    *,
    bundle_dir: Path,
    bundle_manifest: Mapping[str, Any],
    bundle_manifest_sha256: str,
    report_sha256: str,
) -> None:
    latest = _require_mapping(_load_json(latest_path, "latest manifest"), "latest manifest")
    if latest.get("schema_version") != LATEST_MANIFEST_SCHEMA:
        raise CompletionProofError("latest manifest is not kr-live-latest-report/v2")
    bundle_relative = bundle_dir.relative_to(latest_path.parent).as_posix()
    manifest_relative = f"{bundle_relative}/{BUNDLE_MANIFEST_NAME}"
    report_relative = f"{bundle_relative}/{bundle_manifest.get('report_filename')}"
    expected = {
        "artifact_id": bundle_manifest.get("artifact_id"),
        "run_id": bundle_manifest.get("run_id"),
        "ticker": bundle_manifest.get("ticker"),
        "as_of": bundle_manifest.get("as_of"),
        "valuation_hash": bundle_manifest.get("valuation_hash"),
        "audit_hash": bundle_manifest.get("audit_hash"),
        "execution_attestation_hash": bundle_manifest.get("execution_attestation_hash"),
        "bundle_tree_sha256": bundle_manifest.get("bundle_tree_sha256"),
        "bundle_manifest": manifest_relative,
        "bundle_manifest_sha256": bundle_manifest_sha256,
        "report_filename": report_relative,
        "report_sha256": report_sha256,
    }
    for key, wanted in expected.items():
        if latest.get(key) != wanted:
            raise CompletionProofError(f"latest manifest disagrees on {key}")
    if latest.get("bundle_directory") != bundle_relative:
        raise CompletionProofError("latest manifest bundle_directory mismatch")


def validate_completion_bundle(
    bundle_dir: str | Path,
    *,
    stage_registry_path: str | Path,
    latest_manifest_path: str | Path | None = None,
    expected_stages: Iterable[str] | None = None,
) -> CompletionProof:
    """Validate every proof needed to publish a canonical bundle.

    Production callers omit ``expected_stages`` so the repository's ordered
    registry and its 33-stage contract cannot be bypassed.  The override keeps
    focused unit tests small while exercising the same hash and lineage rules.
    """
    root = Path(bundle_dir).resolve()
    if not root.is_dir():
        raise CompletionProofError(f"bundle directory does not exist: {root}")
    expected = (
        tuple(expected_stages)
        if expected_stages is not None
        else expected_stage_sequence(stage_registry_path)
    )
    if not expected:
        raise CompletionProofError("canonical stage registry is empty")
    if expected_stages is None and len(expected) != PRODUCTION_STAGE_COUNT:
        raise CompletionProofError(
            f"production stage registry must contain {PRODUCTION_STAGE_COUNT} stages"
        )

    run_manifest = _require_mapping(
        _load_json(root / "manifest.json", "run manifest"), "run manifest"
    )
    run_id = str(run_manifest.get("run_id") or "")
    ticker = str(run_manifest.get("ticker") or "")
    if not run_id or not ticker:
        raise CompletionProofError("run manifest identity is incomplete")
    if (
        run_manifest.get("status") != "COMPLETED"
        or run_manifest.get("audit_passed") is not True
    ):
        raise CompletionProofError("run manifest is not completed and audit-passed")

    trace = _trace_entries(_load_json(root / "control_plane_trace.json", "control plane trace"))
    _validate_trace(trace, expected, run_id=run_id)
    _validate_audit(_load_json(root / "audit.json", "audit"))
    token = _require_mapping(
        _load_json(root / "freeze_token.json", "freeze token"), "freeze token"
    )
    freeze_hash = _hex_digest(token.get("token_hash"), "freeze token hash")
    if token.get("run_id") != run_id:
        raise CompletionProofError("freeze token run_id mismatch")
    token_valuation_hash = _hex_digest(token.get("valuation_hash"), "freeze token valuation hash")
    token_audit_hash = _hex_digest(token.get("audit_hash"), "freeze token audit hash")
    if run_manifest.get("valuation_hash"):
        run_valuation_hash = _hex_digest(
            run_manifest.get("valuation_hash"), "run manifest valuation hash"
        )
        if run_valuation_hash != token_valuation_hash:
            raise CompletionProofError("run manifest valuation lineage mismatch")

    attestation_hash = _validate_attestation(
        _load_json(root / "execution_attestation.json", "execution attestation"),
        run_id=run_id,
        freeze_token_hash=freeze_hash,
        trace=trace,
    )
    bundle_manifest = _require_mapping(
        _load_json(root / BUNDLE_MANIFEST_NAME, "bundle manifest"), "bundle manifest"
    )
    if bundle_manifest.get("schema_version") != BUNDLE_MANIFEST_SCHEMA:
        raise CompletionProofError("bundle manifest is not canonical-run-bundle/v2")
    if (
        bundle_manifest.get("run_id") != run_id
        or bundle_manifest.get("ticker") != ticker
    ):
        raise CompletionProofError("bundle manifest identity mismatch")
    artifact_id = str(bundle_manifest.get("artifact_id") or "")
    if not artifact_id:
        raise CompletionProofError("bundle manifest has no artifact_id")
    valuation_hash = _hex_digest(bundle_manifest.get("valuation_hash"), "valuation hash")
    audit_hash = _hex_digest(bundle_manifest.get("audit_hash"), "audit hash")
    if valuation_hash != token_valuation_hash or audit_hash != token_audit_hash:
        raise CompletionProofError("bundle manifest does not match freeze lineage")
    if bundle_manifest.get("freeze_token_hash") != freeze_hash:
        raise CompletionProofError("bundle manifest freeze lineage mismatch")
    if bundle_manifest.get("execution_attestation_hash") != attestation_hash:
        raise CompletionProofError("bundle manifest attestation lineage mismatch")
    tree_hash, report_hash = _validate_receipts(root, bundle_manifest)
    if bundle_manifest.get("bundle_tree_sha256") != tree_hash:
        raise CompletionProofError("bundle tree hash mismatch")
    if bundle_manifest.get("report_sha256") != report_hash:
        raise CompletionProofError("bundle report hash mismatch")

    manifest_hash = sha256_file(root / BUNDLE_MANIFEST_NAME)
    if latest_manifest_path is not None:
        try:
            _validate_latest(
                Path(latest_manifest_path).resolve(),
                bundle_dir=root,
                bundle_manifest=bundle_manifest,
                bundle_manifest_sha256=manifest_hash,
                report_sha256=report_hash,
            )
        except ValueError as exc:
            raise CompletionProofError("bundle is outside latest manifest directory") from exc
    return CompletionProof(
        run_id=run_id,
        ticker=ticker,
        artifact_id=artifact_id,
        stage_count=len(trace),
        valuation_hash=valuation_hash,
        audit_hash=audit_hash,
        freeze_token_hash=freeze_hash,
        execution_attestation_hash=attestation_hash,
        bundle_manifest_sha256=manifest_hash,
        bundle_tree_sha256=tree_hash,
        report_sha256=report_hash,
    )
