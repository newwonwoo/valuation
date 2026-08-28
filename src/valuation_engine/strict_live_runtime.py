from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from .authority_orchestrator import (
    AuthorityControlledResult,
    run_authority_controlled_workflow,
)
from .broker_runtime import broker_aware_rocket_insight_adapter
from .control_plane import ExecutionMode
from .generic_reporting import finalize_live_primary_run_artifacts
from .live_runtime import (
    LivePrimaryRuntimeConfig,
    _BLOCKED_RESULT_INTRINSIC_KEYS,
    build_live_primary_adapters,
)
from .orchestrator import ControlledRunResult, load_reporting_contract, load_stage_sequence
from .rocket_context_engine import strict_rocket_insight_dispatch_adapter
from .unit_contracts import load_unit_contract_registry


CANONICAL_ENTRYPOINT_ID = "prism_strict_live_primary/v1"


def run_prism(config: LivePrimaryRuntimeConfig) -> AuthorityControlledResult:
    """Canonical LIVE_PRIMARY entrypoint.

    This is the only run path that produces an execution attestation. The legacy
    ``valuation_engine.live_runtime.run_prism`` remains available for regression
    compatibility but its output is not authority-attested and must not be
    treated as a canonical investment result.
    """
    config.validate()
    sequence = load_stage_sequence(config.stage_registry_path)
    reporting_contract = load_reporting_contract(config.stage_registry_path)
    initial = dict(config.initial_data)
    initial["scenario_binding_spec"] = config.scenario_binding_spec
    initial.setdefault("prior_hypotheses", ())
    initial.setdefault("optional_research_units", ())
    initial.setdefault("research_trigger_state", {})
    initial.setdefault("research_unit_aliases", {})
    initial["canonical_entrypoint_id"] = CANONICAL_ENTRYPOINT_ID

    unit_contract_registry = load_unit_contract_registry(
        config.unit_contract_registry_path
    )
    adapters = build_live_primary_adapters(
        config,
        unit_contract_registry=unit_contract_registry,
    )
    # Replace the generic scanner dispatch with the canonical RocketTesla
    # Context Engine while preserving the broker-research wrapper contract.
    adapters["ROCKET_INSIGHT_SCAN"] = broker_aware_rocket_insight_adapter(
        strict_rocket_insight_dispatch_adapter(
            runners=config.providers.scanner_runners
        ),
        required=bool(getattr(config, "require_broker_research", False)),
    )

    authority_result = run_authority_controlled_workflow(
        run_id=config.run_id,
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_sequence=sequence,
        adapters=adapters,
        required_stages=sequence,
        initial_data=initial,
        unit_contract_registry=unit_contract_registry,
        reporting_contract=reporting_contract,
        major_gate_reporter=getattr(config, "major_gate_reporter", None),
    )
    base = authority_result.result
    if base.blocked_reasons:
        scrubbed = ControlledRunResult(
            run_id=base.run_id,
            execution_mode=base.execution_mode,
            stage_traces=base.stage_traces,
            data={
                key: value
                for key, value in base.data.items()
                if key not in _BLOCKED_RESULT_INTRINSIC_KEYS
            },
            blocked_reasons=base.blocked_reasons,
            freeze_token=None,
            major_gate_summaries=base.major_gate_summaries,
            reporting_warnings=base.reporting_warnings,
        )
        return AuthorityControlledResult(
            scrubbed,
            authority_result.stage_receipts,
            None,
        )

    authority_result.validate_canonical()
    finalized = finalize_live_primary_run_artifacts(
        base,
        state_root=config.state_root,
        stage_registry_path=config.stage_registry_path,
    )
    data = dict(finalized.data)
    attestation = authority_result.execution_attestation
    if attestation is None:
        raise PermissionError("completed strict LIVE_PRIMARY run lost execution attestation")
    data["execution_attestation"] = attestation
    data["execution_attestation_hash"] = attestation.attestation_hash
    data["canonical_entrypoint_id"] = CANONICAL_ENTRYPOINT_ID
    finalized = ControlledRunResult(
        run_id=finalized.run_id,
        execution_mode=finalized.execution_mode,
        stage_traces=finalized.stage_traces,
        data=data,
        blocked_reasons=finalized.blocked_reasons,
        freeze_token=finalized.freeze_token,
        major_gate_summaries=finalized.major_gate_summaries,
        reporting_warnings=finalized.reporting_warnings,
    )
    _persist_execution_attestation(finalized, state_root=config.state_root)
    result = AuthorityControlledResult(
        finalized,
        authority_result.stage_receipts,
        attestation,
    )
    result.validate_canonical()
    return result


def require_canonical_live_result(value: AuthorityControlledResult) -> ControlledRunResult:
    value.validate_canonical()
    if value.result.data.get("canonical_entrypoint_id") != CANONICAL_ENTRYPOINT_ID:
        raise PermissionError("LIVE result did not originate from canonical strict entrypoint")
    return value.result


def _persist_execution_attestation(
    result: ControlledRunResult,
    *,
    state_root: str | Path,
) -> None:
    attestation = result.data.get("execution_attestation")
    run_dir_raw = result.data.get("saved_run_dir")
    if attestation is None or not isinstance(run_dir_raw, str) or not run_dir_raw:
        raise ValueError("completed strict run requires saved_run_dir and execution attestation")
    root = Path(state_root).resolve()
    run_dir = Path(run_dir_raw).resolve()
    try:
        run_dir.relative_to(root)
    except ValueError as exc:
        raise PermissionError("saved run directory is outside canonical state root") from exc
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "execution_attestation.json"
    payload = asdict(attestation)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != encoded:
        raise FileExistsError("execution attestation is immutable for a completed run")
    path.write_text(encoded, encoding="utf-8")
