from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path

from .authority_orchestrator import (
    AuthorityControlledResult,
    run_authority_controlled_workflow,
)
from .broker_runtime import broker_aware_rocket_insight_adapter
from .control_plane import ExecutionMode, StageStatus
from .distributional_audit import distributional_audit_adapter
from .distributional_runtime import (
    DistributionalAPVExecutionSpec,
    primary_valuation_dispatch_adapter,
)
from .generic_reporting import finalize_live_primary_run_artifacts
from .live_runtime import (
    LivePrimaryRuntimeConfig,
    _BLOCKED_RESULT_INTRINSIC_KEYS,
    build_live_primary_adapters,
)
from .orchestrator import (
    ControlledRunResult,
    OrchestratorContext,
    StageAdapter,
    StageExecutionResult,
    load_reporting_contract,
    load_stage_sequence,
)
from .recovery_authority import (
    deterministic_recovery_readjudication_adapter,
    proposal_only_recovery_adapter,
)
from .rocket_context_engine import strict_rocket_insight_dispatch_adapter
from .unit_contracts import load_unit_contract_registry


CANONICAL_ENTRYPOINT_ID = "prism_strict_live_primary/v1"
_DISTRIBUTIONAL_SPEC_KEY = "distributional_apv_execution_spec"


def _distributional_spec_loader(context: OrchestratorContext) -> DistributionalAPVExecutionSpec:
    value = context.data.get(_DISTRIBUTIONAL_SPEC_KEY)
    if not isinstance(value, DistributionalAPVExecutionSpec):
        raise TypeError(
            "canonical distributional primary route requires a typed "
            "DistributionalAPVExecutionSpec in initial_data"
        )
    return value


def _distributional_gate_dispatch(
    *,
    deterministic_adapter: StageAdapter,
    distributional_status: StageStatus,
    rationale: str,
) -> StageAdapter:
    """Keep deterministic DCF-only gates out of a company-level APV primary route."""

    def run(context: OrchestratorContext) -> StageExecutionResult:
        if context.data.get("distributional_primary_result") is not None:
            return StageExecutionResult(distributional_status, rationale)
        return deterministic_adapter(context)

    return run


def _distributional_cross_method_dispatch(
    deterministic_adapter: StageAdapter,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        result = context.data.get("distributional_primary_result")
        if result is None:
            return deterministic_adapter(context)
        envelope = getattr(result, "envelope", None)
        economic_paths = getattr(envelope, "economic_path_ids", ())
        if not economic_paths or len(economic_paths) != len(set(economic_paths)):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "distributional primary valuation contains missing or duplicate economic paths",
                blocking=True,
            )
        path_results = getattr(result, "path_results", ())
        path_ids = tuple(getattr(item, "path_id", "") for item in path_results)
        if not path_ids or any(not item for item in path_ids) or len(path_ids) != len(set(path_ids)):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "distributional primary valuation repeats or omits a path identity",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "distributional APV economic paths and model-case path identities are unique",
        )

    return run


def _distributional_audit_dispatch(generic_adapter: StageAdapter) -> StageAdapter:
    distributional = distributional_audit_adapter()

    def run(context: OrchestratorContext) -> StageExecutionResult:
        if context.data.get("distributional_primary_result") is not None:
            return distributional(context)
        return generic_adapter(context)

    return run


def run_prism(config: LivePrimaryRuntimeConfig) -> AuthorityControlledResult:
    """Canonical LIVE_PRIMARY entrypoint.

    This is the only run path that produces an execution attestation. The legacy
    ``valuation_engine.live_runtime.run_prism`` remains available for regression
    compatibility but its output is not authority-attested and must not be
    treated as a canonical investment result.

    A company-level distributional primary method is dispatched only here, after
    method intent and risk stages. Its typed execution spec is merely input data;
    all operating, financing, APV, payoff and entry arithmetic runs inside the
    canonical DETERMINISTIC_VALUATION stage and is replayed at AUDIT_GATE.
    """
    config.validate()
    strict_providers = replace(
        config.providers,
        research_recovery_adapter=proposal_only_recovery_adapter(
            config.providers.research_recovery_adapter
        ),
    )
    strict_config = replace(config, providers=strict_providers)
    strict_config.validate()

    sequence = load_stage_sequence(strict_config.stage_registry_path)
    reporting_contract = load_reporting_contract(strict_config.stage_registry_path)
    initial = dict(strict_config.initial_data)
    initial["scenario_binding_spec"] = strict_config.scenario_binding_spec
    initial.setdefault("prior_hypotheses", ())
    initial.setdefault("optional_research_units", ())
    initial.setdefault("research_trigger_state", {})
    initial.setdefault("research_unit_aliases", {})
    initial["canonical_entrypoint_id"] = CANONICAL_ENTRYPOINT_ID

    distributional_spec = initial.get(_DISTRIBUTIONAL_SPEC_KEY)
    if distributional_spec is not None and not isinstance(
        distributional_spec, DistributionalAPVExecutionSpec
    ):
        raise TypeError(
            f"{_DISTRIBUTIONAL_SPEC_KEY} must be DistributionalAPVExecutionSpec"
        )

    unit_contract_registry = load_unit_contract_registry(
        strict_config.unit_contract_registry_path
    )
    adapters = build_live_primary_adapters(
        strict_config,
        unit_contract_registry=unit_contract_registry,
    )
    # RocketTesla Context Engine owns scanner routing. The LLM only receives
    # scanner findings after this deterministic dispatch completes.
    adapters["ROCKET_INSIGHT_SCAN"] = broker_aware_rocket_insight_adapter(
        strict_rocket_insight_dispatch_adapter(
            runners=strict_config.providers.scanner_runners
        ),
        required=bool(getattr(strict_config, "require_broker_research", False)),
    )
    # The recovery provider itself runs as proposal-only; this outer gate is the
    # authority that decides whether the proposed recovery is evidence-backed.
    adapters["RESEARCH_LOOP"] = deterministic_recovery_readjudication_adapter(
        adapters["RESEARCH_LOOP"]
    )

    # Preserve the existing deterministic/SOTP implementation as the fallback.
    # A selected company-level primary aggregator takes over only at the exact
    # valuation stage, after the normal method-intent and risk gates have run.
    adapters["DETERMINISTIC_VALUATION"] = primary_valuation_dispatch_adapter(
        deterministic_adapter=adapters["DETERMINISTIC_VALUATION"],
        distributional_loader=(
            _distributional_spec_loader if distributional_spec is not None else None
        ),
    )
    adapters["DCF_PER_ASSUMPTION_CONSISTENCY_GATE"] = (
        _distributional_gate_dispatch(
            deterministic_adapter=adapters["DCF_PER_ASSUMPTION_CONSISTENCY_GATE"],
            distributional_status=StageStatus.SKIPPED_NOT_APPLICABLE,
            rationale=(
                "company-level distributional APV is primary; DCF/PER kernel "
                "consistency is not an intrinsic authorization gate for this run"
            ),
        )
    )
    adapters["CROSS_METHOD_DOUBLE_COUNT_AUDIT"] = _distributional_cross_method_dispatch(
        adapters["CROSS_METHOD_DOUBLE_COUNT_AUDIT"]
    )
    adapters["AUDIT_GATE"] = _distributional_audit_dispatch(adapters["AUDIT_GATE"])

    authority_result = run_authority_controlled_workflow(
        run_id=strict_config.run_id,
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_sequence=sequence,
        adapters=adapters,
        required_stages=sequence,
        initial_data=initial,
        unit_contract_registry=unit_contract_registry,
        reporting_contract=reporting_contract,
        major_gate_reporter=getattr(strict_config, "major_gate_reporter", None),
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
        state_root=strict_config.state_root,
        stage_registry_path=strict_config.stage_registry_path,
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
    _persist_execution_attestation(finalized, state_root=strict_config.state_root)
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
