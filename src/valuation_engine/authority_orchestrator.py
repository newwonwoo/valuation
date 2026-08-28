from __future__ import annotations

from dataclasses import dataclass

from .control_plane import ExecutionMode, StageStatus
from .orchestrator import (
    ControlledRunResult,
    MajorGateReporter,
    ReportingContract,
    StageAdapter,
    StageExecutionResult,
    run_controlled_workflow,
)
from .runtime_authority import (
    ExecutionAttestation,
    StageAuthorityReceipt,
    build_execution_attestation,
    make_stage_receipt,
    orchestrator_stage_scope,
)
from .unit_contracts import UnitContractRegistry


_LLM_PROPOSAL_STAGES = frozenset(
    {"RESEARCHER_A", "BLIND_RED_TEAM_B", "RESEARCH_LOOP"}
)
_LLM_FORBIDDEN_OUTPUT_TOKENS = (
    "compiled_assumption",
    "assumption_set",
    "bound_scenario",
    "generic_valuation",
    "valuation_hash",
    "expected_value",
    "scenario_probability_assessment",
    "probability_weighting_allowed",
    "audit_hash",
    "intrinsic_freeze",
    "execution_attestation",
    "market_comparison",
    "street_comparison",
)
_PREFREEZE_MARKET_OUTPUT_TOKENS = (
    "current_market_price",
    "market_observation",
    "market_comparison",
    "street_reference",
    "street_reports",
    "street_comparison",
    "target_price",
    "target_multiple",
)
_POST_FREEZE_STAGES = frozenset(
    {
        "STREET_REFERENCE_LOAD",
        "STREET_GAP_ANALYZER",
        "MARKET_PRICE_LOAD",
        "MARKET_COMPARE",
        "THESIS_DELTA",
        "SAVE_STATE",
        "FINAL_REPORT",
    }
)


@dataclass(frozen=True)
class AuthorityControlledResult:
    result: ControlledRunResult
    stage_receipts: tuple[StageAuthorityReceipt, ...]
    execution_attestation: ExecutionAttestation | None

    @property
    def canonical_live_result(self) -> bool:
        return (
            self.result.execution_mode is ExecutionMode.LIVE_PRIMARY
            and self.result.completed
            and self.execution_attestation is not None
        )

    def validate_canonical(self) -> None:
        if not self.canonical_live_result or self.execution_attestation is None:
            raise PermissionError(
                "LIVE_PRIMARY result is not canonical: execution attestation missing"
            )
        if len(self.stage_receipts) != len(self.result.stage_traces):
            raise ValueError("authority receipt count does not match stage trace count")
        for receipt, trace in zip(self.stage_receipts, self.result.stage_traces):
            if receipt.stage != trace.stage or receipt.status != trace.status.value:
                raise ValueError("authority receipt/trace mismatch")
        self.execution_attestation.validate()


def _authority_validate_stage_result(
    *,
    stage: str,
    result: StageExecutionResult,
) -> StageExecutionResult:
    """Fail closed when a stage emits a decision outside its authority domain."""
    keys = tuple(result.outputs)
    violations: list[str] = []
    if stage in _LLM_PROPOSAL_STAGES:
        for key in keys:
            lowered = key.casefold()
            if any(token in lowered for token in _LLM_FORBIDDEN_OUTPUT_TOKENS):
                violations.append(key)
    if stage not in _POST_FREEZE_STAGES:
        for key in keys:
            lowered = key.casefold()
            if any(token in lowered for token in _PREFREEZE_MARKET_OUTPUT_TOKENS):
                violations.append(key)
    if violations:
        return StageExecutionResult(
            StageStatus.BLOCKED,
            f"stage authority output violation at {stage}: "
            + ", ".join(sorted(set(violations))),
            blocking=True,
        )
    return result


def authority_wrap_adapters(
    *,
    run_id: str,
    adapters: dict[str, StageAdapter],
) -> dict[str, StageAdapter]:
    """Bind every adapter to its owning orchestrator stage.

    Nested LLM callbacks may narrow the actor to LLM proposal-only, while
    deterministic compiler/model calls remain under the owning stage scope.
    Stage outputs are also checked before the base orchestrator can commit them.
    """

    wrapped: dict[str, StageAdapter] = {}
    for stage, adapter in adapters.items():
        def make_wrapper(stage_name: str, inner: StageAdapter) -> StageAdapter:
            def run(context) -> StageExecutionResult:
                if context.run_id != run_id:
                    raise PermissionError("adapter run_id does not match authority owner")
                with orchestrator_stage_scope(run_id=run_id, stage=stage_name):
                    result = inner(context)
                if not isinstance(result, StageExecutionResult):
                    return result  # type: ignore[return-value]
                return _authority_validate_stage_result(
                    stage=stage_name,
                    result=result,
                )

            return run

        wrapped[stage] = make_wrapper(stage, adapter)
    return wrapped


def run_authority_controlled_workflow(
    *,
    run_id: str,
    execution_mode: ExecutionMode,
    stage_sequence: tuple[str, ...],
    adapters: dict[str, StageAdapter],
    required_stages: tuple[str, ...],
    initial_data: dict[str, object] | None = None,
    unit_contract_registry: UnitContractRegistry | None = None,
    reporting_contract: ReportingContract | None = None,
    major_gate_reporter: MajorGateReporter | None = None,
) -> AuthorityControlledResult:
    base = run_controlled_workflow(
        run_id=run_id,
        execution_mode=execution_mode,
        stage_sequence=stage_sequence,
        adapters=authority_wrap_adapters(run_id=run_id, adapters=adapters),
        required_stages=required_stages,
        initial_data=initial_data,
        unit_contract_registry=unit_contract_registry,
        reporting_contract=reporting_contract,
        major_gate_reporter=major_gate_reporter,
    )
    receipts = tuple(
        make_stage_receipt(
            run_id=run_id,
            stage=trace.stage,
            status=trace.status.value,
            output_keys=trace.output_keys,
        )
        for trace in base.stage_traces
    )
    attestation: ExecutionAttestation | None = None
    if (
        not base.blocked_reasons
        and execution_mode is ExecutionMode.LIVE_PRIMARY
        and base.freeze_token is not None
        and base.stage_traces
        and base.stage_traces[-1].stage == stage_sequence[-1]
    ):
        attestation = build_execution_attestation(
            run_id=run_id,
            execution_mode=execution_mode.value,
            receipts=receipts,
            freeze_token_hash=base.freeze_token.token_hash,
            final_stage=base.stage_traces[-1].stage,
        )
    result = AuthorityControlledResult(base, receipts, attestation)
    if attestation is not None:
        result.validate_canonical()
    return result
