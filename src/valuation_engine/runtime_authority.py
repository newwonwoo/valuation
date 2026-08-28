from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from typing import Iterator


class RuntimeActor(str, Enum):
    EXTERNAL = "external"
    ORCHESTRATOR = "orchestrator"
    DETERMINISTIC = "deterministic"
    LLM = "llm"


class DecisionDomain(str, Enum):
    ROUTING = "routing"
    ROCKET_CONTEXT = "rocket_context"
    HYPOTHESIS = "hypothesis"
    RECOVERY = "recovery"
    ASSUMPTION_COMPILE = "assumption_compile"
    SCENARIO_ASSEMBLY = "scenario_assembly"
    PROBABILITY = "probability"
    VALUATION = "valuation"
    AUDIT = "audit"
    FREEZE = "freeze"
    POST_FREEZE = "post_freeze"
    REPORT = "report"


@dataclass(frozen=True)
class AuthoritySnapshot:
    run_id: str
    stage: str
    actor: RuntimeActor


@dataclass(frozen=True)
class StageAuthorityReceipt:
    run_id: str
    stage: str
    status: str
    output_keys: tuple[str, ...]
    receipt_hash: str


@dataclass(frozen=True)
class ExecutionAttestation:
    run_id: str
    execution_mode: str
    stage_receipt_hashes: tuple[str, ...]
    freeze_token_hash: str
    final_stage: str
    attestation_hash: str

    def validate(self) -> None:
        if not self.run_id or not self.execution_mode or not self.final_stage:
            raise ValueError("execution attestation identity is incomplete")
        if not self.stage_receipt_hashes or any(not item for item in self.stage_receipt_hashes):
            raise ValueError("execution attestation requires stage receipts")
        if not self.freeze_token_hash:
            raise ValueError("execution attestation requires intrinsic freeze lineage")
        expected = _attestation_hash(
            run_id=self.run_id,
            execution_mode=self.execution_mode,
            stage_receipt_hashes=self.stage_receipt_hashes,
            freeze_token_hash=self.freeze_token_hash,
            final_stage=self.final_stage,
        )
        if self.attestation_hash != expected:
            raise PermissionError("execution attestation hash mismatch")


_CURRENT_RUN_ID: ContextVar[str] = ContextVar("valuation_runtime_run_id", default="")
_CURRENT_STAGE: ContextVar[str] = ContextVar("valuation_runtime_stage", default="")
_CURRENT_ACTOR: ContextVar[RuntimeActor] = ContextVar(
    "valuation_runtime_actor", default=RuntimeActor.EXTERNAL
)


def authority_snapshot() -> AuthoritySnapshot:
    return AuthoritySnapshot(
        run_id=_CURRENT_RUN_ID.get(),
        stage=_CURRENT_STAGE.get(),
        actor=_CURRENT_ACTOR.get(),
    )


def current_actor() -> RuntimeActor:
    return _CURRENT_ACTOR.get()


def current_stage() -> str:
    return _CURRENT_STAGE.get()


@contextmanager
def orchestrator_stage_scope(*, run_id: str, stage: str) -> Iterator[None]:
    """Establish the only canonical LIVE stage-execution scope.

    Adapters run under ORCHESTRATOR ownership. A nested deterministic or LLM
    scope may narrow authority but cannot change the owning run/stage.
    """
    if not run_id or not stage:
        raise ValueError("orchestrator stage scope requires run_id and stage")
    run_token = _CURRENT_RUN_ID.set(run_id)
    stage_token = _CURRENT_STAGE.set(stage)
    actor_token = _CURRENT_ACTOR.set(RuntimeActor.ORCHESTRATOR)
    try:
        yield
    finally:
        _CURRENT_ACTOR.reset(actor_token)
        _CURRENT_STAGE.reset(stage_token)
        _CURRENT_RUN_ID.reset(run_token)


@contextmanager
def deterministic_scope() -> Iterator[None]:
    """Narrow an active stage to deterministic decision ownership."""
    if not _CURRENT_STAGE.get():
        raise PermissionError("deterministic decision scope requires orchestrator stage authority")
    actor_token = _CURRENT_ACTOR.set(RuntimeActor.DETERMINISTIC)
    try:
        yield
    finally:
        _CURRENT_ACTOR.reset(actor_token)


@contextmanager
def llm_proposal_scope() -> Iterator[None]:
    """Mark execution as LLM proposal-only.

    Direct/offline LLM unit tests may enter this scope without a run/stage, but
    protected deterministic decision functions will still reject the LLM actor.
    """
    actor_token = _CURRENT_ACTOR.set(RuntimeActor.LLM)
    try:
        yield
    finally:
        _CURRENT_ACTOR.reset(actor_token)


def forbid_llm_decision(domain: DecisionDomain | str) -> None:
    if _CURRENT_ACTOR.get() is RuntimeActor.LLM:
        name = domain.value if isinstance(domain, DecisionDomain) else str(domain)
        raise PermissionError(f"LLM proposal boundary violation: {name}")


def require_orchestrated_live_decision(
    domain: DecisionDomain | str,
    *,
    expected_stage: str | None = None,
) -> None:
    """Require a canonical orchestrator stage for a LIVE decision.

    Offline analytical helpers should use ``forbid_llm_decision`` instead. This
    stronger guard is reserved for adapters/binders whose output can become a
    canonical LIVE_PRIMARY decision.
    """
    forbid_llm_decision(domain)
    snapshot = authority_snapshot()
    if not snapshot.run_id or not snapshot.stage:
        name = domain.value if isinstance(domain, DecisionDomain) else str(domain)
        raise PermissionError(f"canonical LIVE decision requires orchestrator authority: {name}")
    if expected_stage is not None and snapshot.stage != expected_stage:
        raise PermissionError(
            f"decision stage mismatch: expected {expected_stage}, got {snapshot.stage}"
        )
    if snapshot.actor not in {RuntimeActor.ORCHESTRATOR, RuntimeActor.DETERMINISTIC}:
        raise PermissionError(f"decision actor is not authorized: {snapshot.actor.value}")


def make_stage_receipt(
    *,
    run_id: str,
    stage: str,
    status: str,
    output_keys: tuple[str, ...],
) -> StageAuthorityReceipt:
    if not run_id or not stage or not status:
        raise ValueError("stage authority receipt identity is incomplete")
    normalized = tuple(sorted(dict.fromkeys(output_keys)))
    payload = {
        "contract": "stage_authority_receipt/v1",
        "run_id": run_id,
        "stage": stage,
        "status": status,
        "output_keys": normalized,
    }
    digest = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return StageAuthorityReceipt(run_id, stage, status, normalized, digest)


def build_execution_attestation(
    *,
    run_id: str,
    execution_mode: str,
    receipts: tuple[StageAuthorityReceipt, ...],
    freeze_token_hash: str,
    final_stage: str,
) -> ExecutionAttestation:
    if not receipts:
        raise ValueError("execution attestation requires stage receipts")
    if any(item.run_id != run_id for item in receipts):
        raise ValueError("execution attestation contains cross-run stage receipt")
    hashes = tuple(item.receipt_hash for item in receipts)
    result = ExecutionAttestation(
        run_id=run_id,
        execution_mode=execution_mode,
        stage_receipt_hashes=hashes,
        freeze_token_hash=freeze_token_hash,
        final_stage=final_stage,
        attestation_hash=_attestation_hash(
            run_id=run_id,
            execution_mode=execution_mode,
            stage_receipt_hashes=hashes,
            freeze_token_hash=freeze_token_hash,
            final_stage=final_stage,
        ),
    )
    result.validate()
    return result


def _attestation_hash(
    *,
    run_id: str,
    execution_mode: str,
    stage_receipt_hashes: tuple[str, ...],
    freeze_token_hash: str,
    final_stage: str,
) -> str:
    payload = {
        "contract": "execution_attestation/v1",
        "run_id": run_id,
        "execution_mode": execution_mode,
        "stage_receipt_hashes": stage_receipt_hashes,
        "freeze_token_hash": freeze_token_hash,
        "final_stage": final_stage,
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
