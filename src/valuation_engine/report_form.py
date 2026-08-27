from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .broker_runtime import BrokerResearchPreFreezeResult
from .capacity_commitment import CapacityCommitmentAssessment
from .control_plane import ExecutionMode, StageStatus
from .orchestrator import (
    ControlledRunResult,
    load_reporting_contract,
    load_stage_sequence,
    summarize_major_gates,
)
from .risk_adapters import LiveBetaStageResult, LiveWACCStageResult
from .risk_impact import selected_methods_require_discount_rate
from .source_reporting import build_source_link_index


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
    reporting_contract = load_reporting_contract(stage_registry_path)
    observed_stages = tuple(item.stage for item in result.stage_traces)
    observed_statuses = tuple(
        (item.stage, item.status.value, item.blocking) for item in result.stage_traces
    )
    expected_gate_summaries = summarize_major_gates(
        result.stage_traces, reporting_contract
    )
    data = result.data
    try:
        source_links = build_source_link_index(
            data,
            require_all_http=reporting_contract.direct_http_links_required,
        )
    except (TypeError, ValueError):
        source_links = ()
    persisted_report = data.get("final_report")
    source_links_bound = bool(source_links) and isinstance(persisted_report, str) and all(
        item.url in persisted_report for item in source_links
    )

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
        _check(
            "major_gate_reporting_contract",
            result.major_gate_summaries == expected_gate_summaries
            and len(expected_gate_summaries) == len(reporting_contract.major_gates),
            "all five major gates produced compact terminal summaries",
            "the five-gate summaries are missing, incomplete or stale against the stage trace",
        ),
        _check(
            "major_gate_delivery",
            not result.reporting_warnings,
            "major-gate summary delivery recorded no reporter failure",
            "one or more major-gate summary reporters failed",
        ),
        _check(
            "direct_source_links",
            source_links_bound,
            "all report source references are direct HTTP(S) links bound into the final report",
            "direct source links are missing, invalid or absent from the final report",
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

    broker_required = bool(data.get("broker_research_required", False))
    broker_result = data.get("broker_research_prefreeze_result")
    broker_configured = broker_required or broker_result is not None
    if broker_configured:
        broker_runtime_ok = (
            isinstance(broker_result, BrokerResearchPreFreezeResult)
            and _string_hash(data, "broker_research_snapshot_hash")
            == broker_result.snapshot_hash
            and bool(data.get("broker_research_audit_passed"))
            and _string_hash(data, "broker_research_audit_hash") is not None
        )
        checks.append(
            _check(
                "broker_research_primary_verification_chain",
                broker_runtime_ok,
                "pre-freeze Broker Research was partitioned, primary-verified and audit-bound",
                "Broker Research discovery, primary verification or audit binding is missing",
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
        "major_gate_summaries": [
            {
                "gate_id": item.gate_id,
                "status": item.status.value,
                "completed_stage_count": item.completed_stage_count,
                "expected_stage_count": item.expected_stage_count,
                "decisive_result": item.decisive_result,
                "residual_risk": item.residual_risk,
                "next_action": item.next_action,
            }
            for item in result.major_gate_summaries
        ],
        "reporting_warnings": result.reporting_warnings,
        "reporting_contract": {
            "contract_id": reporting_contract.contract_id,
            "main_body_target_pages": reporting_contract.main_body_target_pages,
            "audit_appendix_target_pages": reporting_contract.audit_appendix_target_pages,
            "total_page_cap": reporting_contract.total_page_cap,
            "body_min_pt": reporting_contract.body_min_pt,
            "primary_heading_min_pt": reporting_contract.primary_heading_min_pt,
            "section_heading_min_pt": reporting_contract.section_heading_min_pt,
            "dense_wide_tables_forbidden": reporting_contract.dense_wide_tables_forbidden,
            "direct_http_links_required": reporting_contract.direct_http_links_required,
            "claim_source_mapping_required": reporting_contract.claim_source_mapping_required,
            "non_http_source_refs_forbidden_in_live_reports": reporting_contract.non_http_source_refs_forbidden_in_live_reports,
        },
        "direct_source_links": [item.url for item in source_links],
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
    if broker_configured:
        payload["broker_research"] = {
            "snapshot_hash": data.get("broker_research_snapshot_hash"),
            "audit_hash": data.get("broker_research_audit_hash"),
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
    reporting_contract = load_reporting_contract(stage_registry_path)
    broker_configured = bool(data.get("broker_research_required", False)) or (
        data.get("broker_research_prefreeze_result") is not None
    )
    passed_checks = sum(item.passed for item in attestation.checks)
    failed_checks = tuple(item for item in attestation.checks if not item.passed)
    lines = [
        "# PRISM Verified Controlled-Run Report",
        "",
        f"- Run ID: `{result.run_id}`",
        f"- Execution mode: `{result.execution_mode.value}`",
        f"- Run status: **{status}**",
        f"- Attestation hash: `{attestation.attestation_hash}`",
        "",
        "## Verification",
        f"- Checks: **{passed_checks}/{len(attestation.checks)} PASS**",
        f"- Canonical stages: **{len(result.stage_traces)}/33 terminal traces**",
    ]
    for item in failed_checks:
        lines.append(f"- **FAIL `{item.check_id}`:** {item.detail}")

    lines.extend(
        (
            "",
            "## Frozen Identity Chain",
            f"- Evidence: `{data.get('ledger_snapshot_hash') or 'MISSING'}`",
            f"- Assumptions: `{data.get('assumption_set_hash') or 'MISSING'}`",
            f"- Scenarios: `{data.get('scenario_set_hash') or 'MISSING'}`",
            f"- Valuation: `{data.get('valuation_hash') or 'MISSING'}`",
            f"- Audit: `{data.get('audit_hash') or 'MISSING'}`",
            f"- Intrinsic Freeze: `{getattr(result.freeze_token, 'token_hash', None) or 'MISSING'}`",
        )
    )
    auxiliary = tuple(
        (label, data.get(key))
        for label, key in (
            ("Beta", "beta_snapshot_hash"),
            ("WACC", "wacc_snapshot_hash"),
            ("Capacity assessment", "capacity_commitment_assessment_hash"),
            ("Capacity consumption", "capacity_bridge_consumption_hash"),
            ("Capacity scenario", "capacity_scenario_binding_hash"),
            ("Capacity valuation", "capacity_valuation_binding_hash"),
            ("Capacity PER", "capacity_per_binding_hash"),
            ("Capacity consistency", "capacity_consistency_hash"),
            ("Capacity audit", "capacity_audit_hash"),
            *(
                (
                    ("Broker pre-freeze", "broker_research_snapshot_hash"),
                    ("Broker audit", "broker_research_audit_hash"),
                )
                if broker_configured
                else ()
            ),
        )
        if data.get(key) is not None
    )
    if auxiliary:
        lines.append(
            "- Auxiliary bindings: "
            + " · ".join(f"{label} `{value}`" for label, value in auxiliary)
        )

    lines.extend(("", "## Major Gate Summaries"))
    for summary in result.major_gate_summaries:
        lines.extend(
            (
                "",
                f"### {summary.ordinal}. {summary.title} — {summary.status.value.upper()} "
                f"({summary.completed_stage_count}/{summary.expected_stage_count})",
                f"- Result: {summary.decisive_result}",
                f"- Risk: {summary.residual_risk} · Next: `{summary.next_action}`",
            )
        )
    if not result.major_gate_summaries:
        lines.extend(("", "### MISSING", "- Five-gate reporting contract unavailable."))
    if result.reporting_warnings:
        lines.extend(("", "### Reporting Delivery Warnings", ""))
        lines.extend(f"- {item}" for item in result.reporting_warnings)
    lines.extend(
        (
            "",
            "## Final Report Delivery Contract",
            f"- Main body editorial target: {reporting_contract.main_body_target_pages[0]}–{reporting_contract.main_body_target_pages[1]} pages",
            f"- Audit appendix editorial target: {reporting_contract.audit_appendix_target_pages[0]}–{reporting_contract.audit_appendix_target_pages[1]} pages",
            f"- Combined editorial cap: {reporting_contract.total_page_cap} pages",
            f"- Typography: body ≥ {reporting_contract.body_min_pt}pt, primary heading ≥ {reporting_contract.primary_heading_min_pt}pt, section heading ≥ {reporting_contract.section_heading_min_pt}pt; dense wide tables forbidden.",
            "- Mandatory: every claim source is mapped to a direct HTTP(S) original link in `Sources — Direct Verification`.",
        )
    )

    lines.extend(("", "## Compact Audit Appendix — 33-Stage Trace"))
    trace_index = {trace.stage: trace for trace in result.stage_traces}
    stage_number = {
        trace.stage: index
        for index, trace in enumerate(result.stage_traces, start=1)
    }
    for gate in reporting_contract.major_gates:
        compact = " · ".join(
            f"{stage_number[stage]} `{stage}`={trace_index[stage].status.value}"
            for stage in gate.stages
            if stage in trace_index
        )
        lines.append(f"- **{gate.gate_id}:** {compact or 'NOT_EXECUTED'}")
    lines.append(
        "- Exact rationales and output keys remain in the immutable `control_plane_trace.json` artifact."
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

## Verification

- Checks: **{{ passed_checks }}/{{ total_checks }} PASS**
- Canonical stages: **{{ terminal_stage_count }}/33 terminal traces**
- Failed checks only: `{{ canonical_stage_sequence | beta_wacc_same_run_chain | capacity_core_consumption_chain | broker_research_primary_verification_chain | freeze_hash_binding | major_gate_reporting_contract | major_gate_delivery | direct_source_links | none }} — {{ detail }}`

## Frozen Identity Chain

- Evidence: `{{ ledger_snapshot_hash }}`
- Assumptions: `{{ assumption_set_hash }}`
- Scenarios: `{{ scenario_set_hash }}`
- Valuation: `{{ valuation_hash }}`
- Audit: `{{ audit_hash }}`
- Intrinsic Freeze: `{{ freeze_token_hash }}`
- Auxiliary bindings: `{{ beta_snapshot_hash | wacc_snapshot_hash | capacity_audit_hash | broker_research_snapshot_hash | broker_research_audit_hash | NOT_APPLICABLE }}`

## Major Gate Summaries

### {{ ordinal }}. {{ title }} — {{ STATUS }} ({{ completed/expected }})

- Result: `{{ decisive_result }}`
- Risk: `{{ residual_risk }}` · Next: `{{ next_action }}`

## Final Report Delivery Contract

- Main body editorial target: 3–4 pages
- Audit appendix editorial target: 1–2 pages
- Combined editorial cap: 6 pages
- Typography: body ≥ 13pt, primary heading ≥ 22pt, section heading ≥ 18pt; dense wide tables forbidden.
- Mandatory: every claim source is mapped to a direct HTTP(S) original link in `Sources — Direct Verification`.

## Compact Audit Appendix — 33-Stage Trace

- **{{ gate_id }}:** `{{ stage_number }} {{ stage }}={{ status }}` · …
- Exact rationales and output keys remain in the immutable `control_plane_trace.json` artifact.

## Persisted Research Report

{{ immutable_saved_final_report_including_sources_direct_verification }}
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
