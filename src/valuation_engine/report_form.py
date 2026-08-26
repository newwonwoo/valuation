from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .capacity_commitment import CapacityCommitmentAssessment
from .control_plane import ExecutionMode, StageStatus
from .orchestrator import ControlledRunResult, load_stage_sequence
from .risk_adapters import LiveBetaStageResult, LiveWACCStageResult
from .risk_impact import selected_methods_require_discount_rate


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_STAGE_REGISTRY = _REPO_ROOT / "config" / "control_plane_stage_registry.yaml"
_ACCEPTABLE_STAGE_STATUSES = {
    StageStatus.PASS,
    StageStatus.WARNING,
    StageStatus.SKIPPED_NOT_APPLICABLE,
    StageStatus.RECOVERED,
}


@dataclass(frozen=True)
class ExecutionCheck:
    check_id: str
    passed: bool
    detail: str

    def __post_init__(self) -> None:
        if not self.check_id or not self.detail:
            raise ValueError("execution check requires identity and detail")


@dataclass(frozen=True)
class RunAttestation:
    run_id: str
    checks: tuple[ExecutionCheck, ...]
    attestation_hash: str

    def __post_init__(self) -> None:
        if not self.run_id or not self.checks or not self.attestation_hash:
            raise ValueError("run attestation is incomplete")

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.checks)


def _stable_hash(payload: dict[str, Any]) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _string_hash(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _check(check_id: str, passed: bool, success: str, failure: str) -> ExecutionCheck:
    return ExecutionCheck(check_id, passed, success if passed else failure)


def attest_controlled_run(
    result: ControlledRunResult,
    *,
    stage_registry_path: str | Path = _DEFAULT_STAGE_REGISTRY,
) -> RunAttestation:
    sequence = load_stage_sequence(stage_registry_path)
    observed_stages = tuple(item.stage for item in result.stage_traces)
    observed_statuses = tuple(
        (item.stage, item.status.value, item.blocking) for item in result.stage_traces
    )
    data = result.data

    checks: list[ExecutionCheck] = [
        _check(
            "live_primary_mode",
            result.execution_mode is ExecutionMode.LIVE_PRIMARY,
            "the report was produced by LIVE_PRIMARY",
            "the report was not produced by LIVE_PRIMARY",
        ),
        _check(
            "run_unblocked",
            not result.blocked_reasons,
            "the controlled run has no blocking reason",
            "the controlled run has blocking reasons",
        ),
        _check(
            "canonical_stage_sequence",
            observed_stages == sequence,
            f"all {len(sequence)} canonical stages executed in order",
            "the observed stage sequence differs from the canonical registry",
        ),
        _check(
            "terminal_stage_statuses",
            bool(result.stage_traces)
            and all(
                item.status in _ACCEPTABLE_STAGE_STATUSES and not item.blocking
                for item in result.stage_traces
            ),
            "every stage ended in a non-blocking terminal status",
            "one or more stages are unresolved or blocking",
        ),
        _check(
            "intrinsic_freeze_token",
            result.freeze_token is not None,
            "the same run issued an IntrinsicFreezeToken",
            "no IntrinsicFreezeToken was issued",
        ),
        _check(
            "evidence_ledger_hash",
            _string_hash(data, "ledger_snapshot_hash") is not None,
            "the frozen Evidence Ledger hash is present",
            "ledger_snapshot_hash is missing",
        ),
        _check(
            "assumption_set_hash",
            _string_hash(data, "assumption_set_hash") is not None,
            "the compiled assumption-set hash is present",
            "assumption_set_hash is missing",
        ),
        _check(
            "scenario_set_hash",
            _string_hash(data, "scenario_set_hash") is not None,
            "the bound scenario-set hash is present",
            "scenario_set_hash is missing",
        ),
        _check(
            "valuation_hash",
            _string_hash(data, "valuation_hash") is not None,
            "the deterministic valuation hash is present",
            "valuation_hash is missing",
        ),
        _check(
            "audit_hash",
            _string_hash(data, "audit_hash") is not None
            and bool(data.get("audit_passed")),
            "the generic audit passed and its hash is present",
            "the generic audit did not pass or its hash is missing",
        ),
        _check(
            "persisted_final_report",
            isinstance(data.get("final_report"), str) and bool(data.get("final_report")),
            "the final report was emitted from the persisted run payload",
            "the persisted final report is missing",
        ),
    ]

    selected_methods = data.get("selected_methods", ())
    selected_methods_ok = isinstance(selected_methods, tuple) and all(
        isinstance(item, str) for item in selected_methods
    )
    checks.append(
        _check(
            "selected_method_contract",
            selected_methods_ok,
            "selected valuation methods are typed",
            "selected_methods is missing or malformed",
        )
    )
    requires_risk = bool(
        selected_methods_ok
        and selected_methods_require_discount_rate(selected_methods)
    )
    if requires_risk:
        beta = data.get("live_beta_result")
        wacc = data.get("live_wacc_result")
        risk_chain_ok = (
            isinstance(beta, LiveBetaStageResult)
            and isinstance(wacc, LiveWACCStageResult)
            and wacc.beta_result.snapshot_hash == beta.snapshot_hash
            and _string_hash(data, "beta_snapshot_hash") == beta.snapshot_hash
            and _string_hash(data, "wacc_snapshot_hash") == wacc.snapshot_hash
        )
        checks.append(
            _check(
                "beta_wacc_same_run_chain",
                risk_chain_ok,
                "Beta and WACC snapshots are executed and bound to one risk chain",
                "a required Beta/WACC provider output or same-run hash binding is missing",
            )
        )

    capacity = data.get("capacity_commitment_assessment")
    capacity_typed = isinstance(capacity, CapacityCommitmentAssessment)
    checks.append(
        _check(
            "capacity_assessment",
            capacity_typed
            and _string_hash(data, "capacity_commitment_assessment_hash")
            == capacity.assessment_hash,
            "the typed Capacity Commitment assessment and hash are present",
            "the Capacity Commitment assessment is missing or stale",
        )
    )
    checks.append(
        _check(
            "capacity_audit",
            bool(data.get("capacity_audit_passed"))
            and _string_hash(data, "capacity_audit_hash") is not None,
            "the Capacity omission/double-count audit passed",
            "the Capacity audit did not pass or its hash is missing",
        )
    )
    core_projects = (
        capacity.core_inclusion_required_projects if capacity_typed else ()
    )
    if core_projects:
        required_capacity_hashes = (
            "capacity_bridge_consumption_hash",
            "capacity_scenario_binding_hash",
            "capacity_valuation_binding_hash",
            "capacity_per_binding_hash",
            "capacity_consistency_hash",
        )
        missing = tuple(
            key for key in required_capacity_hashes if _string_hash(data, key) is None
        )
        checks.append(
            _check(
                "capacity_core_consumption_chain",
                not missing,
                "Core Capacity, CAPEX and ramp paths are bound through valuation",
                "missing Capacity execution hashes: " + ", ".join(missing),
            )
        )

    token = result.freeze_token
    freeze_hash_match = False
    if token is not None:
        freeze_hash_match = all(
            (
                getattr(token, "run_id", None) == result.run_id,
                getattr(token, "ledger_snapshot_hash", None)
                == data.get("ledger_snapshot_hash"),
                getattr(token, "assumption_set_hash", None)
                == data.get("assumption_set_hash"),
                getattr(token, "valuation_hash", None) == data.get("valuation_hash"),
                getattr(token, "audit_hash", None) == data.get("audit_hash"),
            )
        )
    checks.append(
        _check(
            "freeze_hash_binding",
            freeze_hash_match,
            "Freeze is bound to the same Evidence, assumptions, valuation and audit",
            "Freeze token fields do not match the controlled-run hashes",
        )
    )

    payload = {
        "contract": "prism_verified_report_attestation/v1",
        "run_id": result.run_id,
        "execution_mode": result.execution_mode.value,
        "observed_stage_statuses": observed_statuses,
        "blocked_reasons": result.blocked_reasons,
        "checks": [
            {
                "check_id": item.check_id,
                "passed": item.passed,
                "detail": item.detail,
            }
            for item in checks
        ],
        "hashes": {
            key: data.get(key)
            for key in (
                "ledger_snapshot_hash",
                "assumption_set_hash",
                "scenario_set_hash",
                "beta_snapshot_hash",
                "wacc_snapshot_hash",
                "capacity_commitment_assessment_hash",
                "capacity_bridge_consumption_hash",
                "capacity_scenario_binding_hash",
                "capacity_valuation_binding_hash",
                "capacity_per_binding_hash",
                "capacity_consistency_hash",
                "capacity_audit_hash",
                "valuation_hash",
                "audit_hash",
            )
        },
        "freeze_token_hash": getattr(token, "token_hash", None),
    }
    return RunAttestation(result.run_id, tuple(checks), _stable_hash(payload))


def render_controlled_run_report(
    result: ControlledRunResult,
    *,
    stage_registry_path: str | Path = _DEFAULT_STAGE_REGISTRY,
) -> str:
    attestation = attest_controlled_run(
        result,
        stage_registry_path=stage_registry_path,
    )
    status = (
        "BLOCKED"
        if result.blocked_reasons
        else ("VERIFIED_FROZEN" if attestation.passed else "INCOMPLETE")
    )
    data = result.data
    lines = [
        "# PRISM Verified Controlled-Run Report",
        "",
        f"- Run ID: `{result.run_id}`",
        f"- Execution mode: `{result.execution_mode.value}`",
        f"- Run status: **{status}**",
        f"- Attestation hash: `{attestation.attestation_hash}`",
        "",
        "## Execution Attestation",
        "",
        "| Check | Result | Detail |",
        "|---|---:|---|",
    ]
    for item in attestation.checks:
        result_label = "PASS" if item.passed else "FAIL"
        detail = item.detail.replace("|", "\\|")
        lines.append(f"| `{item.check_id}` | **{result_label}** | {detail} |")

    lines.extend(
        (
            "",
            "## Immutable Run Identities",
            "",
            "| Artifact | Hash |",
            "|---|---|",
        )
    )
    for label, key in (
        ("Evidence Ledger", "ledger_snapshot_hash"),
        ("Assumption set", "assumption_set_hash"),
        ("Scenario set", "scenario_set_hash"),
        ("Beta", "beta_snapshot_hash"),
        ("WACC", "wacc_snapshot_hash"),
        ("Capacity assessment", "capacity_commitment_assessment_hash"),
        ("Capacity consumption", "capacity_bridge_consumption_hash"),
        ("Capacity scenario", "capacity_scenario_binding_hash"),
        ("Capacity valuation", "capacity_valuation_binding_hash"),
        ("Capacity PER", "capacity_per_binding_hash"),
        ("Capacity consistency", "capacity_consistency_hash"),
        ("Capacity audit", "capacity_audit_hash"),
        ("Valuation", "valuation_hash"),
        ("Audit", "audit_hash"),
    ):
        value = data.get(key)
        lines.append(f"| {label} | `{value if value is not None else 'NOT_APPLICABLE'}` |")
    lines.append(
        f"| Intrinsic Freeze | `{getattr(result.freeze_token, 'token_hash', None) or 'MISSING'}` |"
    )

    lines.extend(
        (
            "",
            "## Stage Trace",
            "",
            "| # | Stage | Status | Blocking | Rationale |",
            "|---:|---|---|---:|---|",
        )
    )
    for index, trace in enumerate(result.stage_traces, start=1):
        rationale = trace.rationale.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {index} | `{trace.stage}` | `{trace.status.value}` | "
            f"{'YES' if trace.blocking else 'NO'} | {rationale} |"
        )

    persisted = data.get("final_report")
    lines.extend(("", "## Persisted Research Report", ""))
    if isinstance(persisted, str) and persisted:
        lines.append(persisted.rstrip())
    else:
        lines.append("No persisted final report is available for this run.")
    return "\n".join(lines) + "\n"


def render_report_form_template() -> str:
    return """# PRISM Verified Controlled-Run Report

- Run ID: `{{ run_id }}`
- Execution mode: `LIVE_PRIMARY`
- Run status: **{{ VERIFIED_FROZEN | INCOMPLETE | BLOCKED }}**
- Attestation hash: `{{ attestation_hash }}`

## Execution Attestation

| Check | Result | Detail |
|---|---:|---|
| `canonical_stage_sequence` | `{{ PASS_OR_FAIL }}` | `{{ detail }}` |
| `beta_wacc_same_run_chain` | `{{ PASS_OR_FAIL_OR_NOT_APPLICABLE }}` | `{{ detail }}` |
| `capacity_core_consumption_chain` | `{{ PASS_OR_FAIL_OR_NOT_APPLICABLE }}` | `{{ detail }}` |
| `freeze_hash_binding` | `{{ PASS_OR_FAIL }}` | `{{ detail }}` |

## Immutable Run Identities

| Artifact | Hash |
|---|---|
| Evidence Ledger | `{{ ledger_snapshot_hash }}` |
| Assumption set | `{{ assumption_set_hash }}` |
| Scenario set | `{{ scenario_set_hash }}` |
| Beta | `{{ beta_snapshot_hash_or_not_applicable }}` |
| WACC | `{{ wacc_snapshot_hash_or_not_applicable }}` |
| Capacity assessment | `{{ capacity_commitment_assessment_hash }}` |
| Capacity consumption | `{{ capacity_bridge_consumption_hash_or_not_applicable }}` |
| Capacity scenario | `{{ capacity_scenario_binding_hash_or_not_applicable }}` |
| Capacity valuation | `{{ capacity_valuation_binding_hash_or_not_applicable }}` |
| Capacity audit | `{{ capacity_audit_hash }}` |
| Valuation | `{{ valuation_hash }}` |
| Audit | `{{ audit_hash }}` |
| Intrinsic Freeze | `{{ freeze_token_hash }}` |

## Stage Trace

| # | Stage | Status | Blocking | Rationale |
|---:|---|---|---:|---|
| 1 | `{{ stage }}` | `{{ status }}` | `{{ YES_OR_NO }}` | `{{ rationale }}` |

## Persisted Research Report

{{ immutable_saved_final_report }}
"""


def write_verified_report(
    result: ControlledRunResult,
    path: str | Path,
    *,
    stage_registry_path: str | Path = _DEFAULT_STAGE_REGISTRY,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_controlled_run_report(
            result,
            stage_registry_path=stage_registry_path,
        ),
        encoding="utf-8",
    )
    return target
