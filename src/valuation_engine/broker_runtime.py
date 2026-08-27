from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from typing import Callable, Mapping

from .broker_research import (
    BrokerClaim,
    BrokerFieldClass,
    pre_freeze_allowed,
)
from .control_plane import StageStatus
from .industry_dna import IndustryDNAProfile
from .ledger import EvidenceLedger
from .module_plan import ModuleRequirementPlan
from .module_plan_adapter import module_requirement_plan_adapter
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .records import AuditFinding, AuditReport, BridgeRecord, HypothesisRecord


_LOCKED_PRE_FREEZE_FIELDS = frozenset(
    {
        BrokerFieldClass.TARGET_COMPANY_FORECAST,
        BrokerFieldClass.TARGET_PRICE,
        BrokerFieldClass.RATING,
        BrokerFieldClass.TARGET_MULTIPLE,
        BrokerFieldClass.CONSENSUS,
    }
)


class BrokerPreFreezeUse(str, Enum):
    CONTEXT = "context"
    PRIMARY_VERIFICATION_ONLY = "primary_verification_only"
    QUARANTINED = "quarantined"


def pre_freeze_use(claim: BrokerClaim) -> BrokerPreFreezeUse:
    if claim.field_class in _LOCKED_PRE_FREEZE_FIELDS:
        return BrokerPreFreezeUse.QUARANTINED
    if claim.target_company_specific:
        return BrokerPreFreezeUse.PRIMARY_VERIFICATION_ONLY
    if pre_freeze_allowed(claim):
        return BrokerPreFreezeUse.CONTEXT
    return BrokerPreFreezeUse.QUARANTINED


@dataclass(frozen=True)
class BrokerResearchObservation:
    claim: BrokerClaim
    segment_id: str
    source_ref: str
    verification_metrics: tuple[str, ...] = ()
    verification_requests: tuple[str, ...] = ()
    primary_source_hints: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.claim.claim_id or not self.claim.source_id or not self.claim.broker_family:
            raise ValueError("broker observation requires claim/source/broker identity")
        if not self.segment_id or not self.source_ref:
            raise ValueError("broker observation requires segment_id and source_ref")
        if len(self.verification_metrics) != len(set(self.verification_metrics)):
            raise ValueError("broker observation has duplicate verification metrics")
        if any(not item for item in self.verification_metrics):
            raise ValueError("broker verification metric cannot be blank")
        if any(not item for item in self.verification_requests):
            raise ValueError("broker verification request cannot be blank")
        disposition = pre_freeze_use(self.claim)
        if disposition is BrokerPreFreezeUse.PRIMARY_VERIFICATION_ONLY and not (
            self.verification_metrics or self.verification_requests
        ):
            raise ValueError(
                "target-company broker lead requires a primary verification plan"
            )
        if disposition is BrokerPreFreezeUse.QUARANTINED and (
            self.verification_metrics or self.verification_requests
        ):
            raise ValueError(
                "quarantined target forecast/target/rating/multiple/consensus cannot drive pre-freeze verification"
            )


@dataclass(frozen=True)
class BrokerResearchBatch:
    checked_at: str
    observations: tuple[BrokerResearchObservation, ...]
    source_refs: tuple[str, ...]

    def validate(self) -> None:
        if not self.checked_at or not self.observations or not self.source_refs:
            raise ValueError("broker research batch requires date, observations and sources")
        claim_ids = tuple(item.claim.claim_id for item in self.observations)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("broker research batch contains duplicate claim IDs")
        for item in self.observations:
            item.validate()
        referenced = {item.source_ref for item in self.observations}
        missing = tuple(sorted(referenced - set(self.source_refs)))
        if missing:
            raise ValueError(
                "broker research source_refs omit observation sources: "
                + ", ".join(missing)
            )


@dataclass(frozen=True)
class BrokerResearchPreFreezeResult:
    context_claims: tuple[BrokerClaim, ...]
    primary_verification_claims: tuple[BrokerClaim, ...]
    quarantined_claims: tuple[BrokerClaim, ...]
    verification_rows: tuple[tuple[str, str], ...]
    verification_requests: tuple[str, ...]
    primary_source_hints: tuple[str, ...]
    source_refs: tuple[str, ...]
    snapshot_hash: str

    def __post_init__(self) -> None:
        if not self.snapshot_hash or not self.source_refs:
            raise ValueError("broker pre-freeze result requires source refs and hash")
        claim_ids = tuple(
            claim.claim_id
            for claims in (
                self.context_claims,
                self.primary_verification_claims,
                self.quarantined_claims,
            )
            for claim in claims
        )
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("broker pre-freeze claim partition overlaps")
        if len(self.verification_rows) != len(set(self.verification_rows)):
            raise ValueError("broker pre-freeze result has duplicate verification rows")

    @property
    def additional_required_evidence(self) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        for segment_id, metric in self.verification_rows:
            grouped.setdefault(segment_id, []).append(metric)
        return {
            segment_id: tuple(dict.fromkeys(metrics))
            for segment_id, metrics in grouped.items()
        }


@dataclass(frozen=True)
class BrokerResearchLLMContext:
    context_claims: tuple[BrokerClaim, ...]
    primary_verification_claims: tuple[BrokerClaim, ...]
    verification_requests: tuple[str, ...]
    primary_source_hints: tuple[str, ...]
    source_refs: tuple[str, ...]
    snapshot_hash: str

    def __post_init__(self) -> None:
        if not self.source_refs or not self.snapshot_hash:
            raise ValueError("BrokerResearchLLMContext requires source refs and hash")
        if any(
            pre_freeze_use(item) is not BrokerPreFreezeUse.CONTEXT
            for item in self.context_claims
        ):
            raise ValueError("LLM broker context contains a non-context claim")
        if any(
            pre_freeze_use(item) is not BrokerPreFreezeUse.PRIMARY_VERIFICATION_ONLY
            for item in self.primary_verification_claims
        ):
            raise ValueError("LLM broker context contains a non-verification claim")


@dataclass(frozen=True)
class BrokerResearchAuditResult:
    report: AuditReport
    audit_hash: str

    @property
    def passed(self) -> bool:
        return self.report.passed


BrokerResearchLoader = Callable[[OrchestratorContext], BrokerResearchBatch]


def _claim_payload(claim: BrokerClaim) -> dict[str, object]:
    return {
        "claim_id": claim.claim_id,
        "source_id": claim.source_id,
        "broker_family": claim.broker_family,
        "report_type": claim.report_type.value,
        "field_class": claim.field_class.value,
        "industry_node": claim.industry_node,
        "statement": claim.statement,
        "target_company_specific": claim.target_company_specific,
        "underlying_data_families": claim.underlying_data_families,
        "report_date": claim.report_date,
    }


def _stable_hash(payload: dict[str, object]) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def build_broker_prefreeze_result(
    batch: BrokerResearchBatch,
    *,
    known_segments: tuple[str, ...],
) -> BrokerResearchPreFreezeResult:
    batch.validate()
    known = set(known_segments)
    context_claims: list[BrokerClaim] = []
    verification_claims: list[BrokerClaim] = []
    quarantined_claims: list[BrokerClaim] = []
    verification_rows: list[tuple[str, str]] = []
    verification_requests: list[str] = []
    primary_source_hints: list[str] = []

    for observation in batch.observations:
        if observation.segment_id not in known:
            raise ValueError(
                f"broker research references unknown segment {observation.segment_id}"
            )
        disposition = pre_freeze_use(observation.claim)
        if disposition is BrokerPreFreezeUse.CONTEXT:
            context_claims.append(observation.claim)
        elif disposition is BrokerPreFreezeUse.PRIMARY_VERIFICATION_ONLY:
            verification_claims.append(observation.claim)
        else:
            quarantined_claims.append(observation.claim)
            continue
        verification_rows.extend(
            (observation.segment_id, metric)
            for metric in observation.verification_metrics
        )
        verification_requests.extend(observation.verification_requests)
        primary_source_hints.extend(observation.primary_source_hints)

    verification_rows_tuple = tuple(dict.fromkeys(verification_rows))
    requests_tuple = tuple(dict.fromkeys(verification_requests))
    hints_tuple = tuple(dict.fromkeys(primary_source_hints))
    payload: dict[str, object] = {
        "contract": "broker_research_prefreeze/v1",
        "checked_at": batch.checked_at,
        "context_claims": [_claim_payload(item) for item in context_claims],
        "primary_verification_claims": [
            _claim_payload(item) for item in verification_claims
        ],
        "quarantined_claims": [_claim_payload(item) for item in quarantined_claims],
        "verification_rows": verification_rows_tuple,
        "verification_requests": requests_tuple,
        "primary_source_hints": hints_tuple,
        "source_refs": batch.source_refs,
    }
    return BrokerResearchPreFreezeResult(
        context_claims=tuple(context_claims),
        primary_verification_claims=tuple(verification_claims),
        quarantined_claims=tuple(quarantined_claims),
        verification_rows=verification_rows_tuple,
        verification_requests=requests_tuple,
        primary_source_hints=hints_tuple,
        source_refs=tuple(dict.fromkeys(batch.source_refs)),
        snapshot_hash=_stable_hash(payload),
    )


def _merge_evidence_maps(
    base: Mapping[str, tuple[str, ...]] | None,
    dynamic: Mapping[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    merged: dict[str, tuple[str, ...]] = {}
    for source in (base or {}, dynamic):
        for segment_id, metrics in source.items():
            merged[segment_id] = tuple(
                dict.fromkeys((*merged.get(segment_id, ()), *metrics))
            )
    return merged


def broker_aware_module_requirement_plan_adapter(
    *,
    registry_path,
    control_requirements_path,
    loader: BrokerResearchLoader | None,
    require_broker_research: bool,
    additional_required_evidence: Mapping[str, tuple[str, ...]] | None = None,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        if loader is None:
            if require_broker_research:
                return StageExecutionResult(
                    StageStatus.NOT_IMPLEMENTED,
                    "LIVE_PRIMARY requires pre-freeze Broker Research but no BrokerResearchLoader is configured",
                    {"broker_research_required": True},
                    blocking=True,
                )
            return module_requirement_plan_adapter(
                registry_path=registry_path,
                control_requirements_path=control_requirements_path,
                additional_required_evidence=additional_required_evidence,
            )(context)

        profiles = context.data.get("industry_dna_profiles")
        if not isinstance(profiles, tuple) or not profiles or not all(
            isinstance(item, IndustryDNAProfile) for item in profiles
        ):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "IndustryDNA profiles are required before Broker Research discovery",
                blocking=True,
            )
        try:
            batch = loader(context)
            if not isinstance(batch, BrokerResearchBatch):
                raise TypeError("BrokerResearchLoader must return BrokerResearchBatch")
            result = build_broker_prefreeze_result(
                batch,
                known_segments=tuple(item.segment_id for item in profiles),
            )
            merged_evidence = _merge_evidence_maps(
                additional_required_evidence,
                result.additional_required_evidence,
            )
            plan_stage = module_requirement_plan_adapter(
                registry_path=registry_path,
                control_requirements_path=control_requirements_path,
                additional_required_evidence=merged_evidence,
            )(context)
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"pre-freeze Broker Research discovery failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        if plan_stage.blocking:
            return plan_stage
        outputs = {
            "broker_research_required": bool(require_broker_research),
            "broker_research_prefreeze_result": result,
            "broker_research_snapshot_hash": result.snapshot_hash,
            "broker_context_claims": result.context_claims,
            "broker_primary_verification_claims": result.primary_verification_claims,
            "broker_quarantined_claims": result.quarantined_claims,
            "broker_primary_verification_requests": result.verification_requests,
            "broker_primary_source_hints": result.primary_source_hints,
            "broker_additional_required_evidence": result.additional_required_evidence,
            "broker_research_llm_context": BrokerResearchLLMContext(
                context_claims=result.context_claims,
                primary_verification_claims=result.primary_verification_claims,
                verification_requests=result.verification_requests,
                primary_source_hints=result.primary_source_hints,
                source_refs=result.source_refs,
                snapshot_hash=result.snapshot_hash,
            ),
            **plan_stage.outputs,
        }
        return StageExecutionResult(
            plan_stage.status,
            (
                "Broker Research discovery partitioned context, primary-verification-only and quarantined claims; "
                "primary verification metrics were compiled into the Module Requirement Plan | "
                + plan_stage.rationale
            ),
            outputs,
        )

    return run


def _broker_result_hash(result: BrokerResearchPreFreezeResult) -> str:
    payload: dict[str, object] = {
        "contract": "broker_research_prefreeze/v1",
        "checked_at": "replay",
        "context_claims": [_claim_payload(item) for item in result.context_claims],
        "primary_verification_claims": [
            _claim_payload(item) for item in result.primary_verification_claims
        ],
        "quarantined_claims": [
            _claim_payload(item) for item in result.quarantined_claims
        ],
        "verification_rows": result.verification_rows,
        "verification_requests": result.verification_requests,
        "primary_source_hints": result.primary_source_hints,
        "source_refs": result.source_refs,
    }
    # checked_at is intentionally not replayable from the compact result. The audit binds
    # the exact runtime hash separately and validates all semantic partitions below.
    return _stable_hash(payload)


def broker_research_audit_adapter(*, required: bool) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        result = context.data.get("broker_research_prefreeze_result")
        snapshot_hash = context.data.get("broker_research_snapshot_hash")
        if result is None:
            if required:
                return StageExecutionResult(
                    StageStatus.RECOVERY_REQUIRED,
                    "required pre-freeze Broker Research result is missing before Audit",
                    blocking=True,
                )
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "pre-freeze Broker Research is not configured for this run",
                {"broker_research_audit_required": False},
            )
        if not isinstance(result, BrokerResearchPreFreezeResult):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "broker_research_prefreeze_result has invalid type",
                blocking=True,
            )
        if not isinstance(snapshot_hash, str) or not snapshot_hash:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "broker_research_snapshot_hash is missing",
                blocking=True,
            )

        ledger = context.data.get("evidence_ledger")
        plan = context.data.get("module_requirement_plan")
        hypotheses = context.data.get("hypotheses", ())
        bridges = context.data.get("bridges", ())
        if not isinstance(ledger, EvidenceLedger):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "EvidenceLedger is required for Broker Research audit",
                blocking=True,
            )
        if not isinstance(plan, ModuleRequirementPlan):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "ModuleRequirementPlan is required for Broker Research audit",
                blocking=True,
            )
        if not isinstance(hypotheses, tuple) or not all(
            isinstance(item, HypothesisRecord) for item in hypotheses
        ):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "Broker Research audit requires typed hypotheses",
                blocking=True,
            )
        if not isinstance(bridges, tuple) or not all(
            isinstance(item, BridgeRecord) for item in bridges
        ):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "Broker Research audit requires typed Bridges",
                blocking=True,
            )

        context_partition_ok = all(
            pre_freeze_use(item) is BrokerPreFreezeUse.CONTEXT
            for item in result.context_claims
        )
        verification_partition_ok = all(
            pre_freeze_use(item) is BrokerPreFreezeUse.PRIMARY_VERIFICATION_ONLY
            for item in result.primary_verification_claims
        )
        quarantine_ok = all(
            pre_freeze_use(item) is BrokerPreFreezeUse.QUARANTINED
            for item in result.quarantined_claims
        )
        plan_rows = {
            (segment.segment_id, metric)
            for segment in plan.segments
            for metric in segment.required_evidence
        }
        plan_coverage_ok = set(result.verification_rows).issubset(plan_rows)
        evidence_rows = {
            (item.segment, item.metric)
            for item in ledger.active()
        }
        primary_verification_ok = set(result.verification_rows).issubset(evidence_rows)
        claim_ids = {
            claim.claim_id
            for claims in (
                result.context_claims,
                result.primary_verification_claims,
                result.quarantined_claims,
            )
            for claim in claims
        }
        hypothesis_evidence_ids = {
            evidence_id
            for item in hypotheses
            for evidence_id in (
                *item.supporting_evidence_ids,
                *item.contradicting_evidence_ids,
            )
        }
        bridge_evidence_ids = {
            evidence_id
            for item in bridges
            for evidence_id in item.evidence_ids
        }
        no_direct_broker_evidence = not claim_ids.intersection(
            hypothesis_evidence_ids | bridge_evidence_ids
        )
        broker_source_refs = set(result.source_refs)
        no_broker_sources_in_ledger = not broker_source_refs.intersection(
            item.source_ref for item in ledger.active()
        )

        findings = (
            AuditFinding(
                "broker_context_partition",
                context_partition_ok,
                True,
                "non-target broker research is context/discovery only",
            ),
            AuditFinding(
                "broker_target_fact_verification_only",
                verification_partition_ok,
                True,
                "target-company broker factual leads only generate primary verification",
            ),
            AuditFinding(
                "broker_locked_fields_quarantined",
                quarantine_ok,
                True,
                "target forecasts, targets, ratings, multiples and consensus remain quarantined before Freeze",
            ),
            AuditFinding(
                "broker_verification_in_module_plan",
                plan_coverage_ok,
                True,
                "all broker-discovered verification metrics were compiled into required Evidence",
            ),
            AuditFinding(
                "broker_verification_primary_evidence",
                primary_verification_ok,
                True,
                "all broker-discovered verification metrics were satisfied by the primary EvidenceLedger",
            ),
            AuditFinding(
                "broker_claims_not_direct_assumption_evidence",
                no_direct_broker_evidence,
                True,
                "broker claim IDs never became Hypothesis or Bridge Evidence IDs",
            ),
            AuditFinding(
                "broker_sources_not_in_primary_ledger",
                no_broker_sources_in_ledger,
                True,
                "broker report sources never entered the primary EvidenceLedger",
            ),
        )
        report = AuditReport(findings)
        audit_hash = _stable_hash(
            {
                "contract": "broker_research_audit/v1",
                "broker_snapshot_hash": snapshot_hash,
                "semantic_replay_hash": _broker_result_hash(result),
                "ledger_active_ids": tuple(item.id for item in ledger.active()),
                "verification_rows": result.verification_rows,
                "findings": tuple(
                    (item.check, item.passed, item.blocking, item.detail)
                    for item in findings
                ),
            }
        )
        outputs = {
            "broker_research_audit_required": True,
            "broker_research_audit_result": BrokerResearchAuditResult(
                report,
                audit_hash,
            ),
            "broker_research_audit_report": report,
            "broker_research_audit_hash": audit_hash,
            "broker_research_audit_passed": report.passed,
        }
        if not report.passed:
            failed = tuple(
                item.check for item in findings if item.blocking and not item.passed
            )
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "Broker Research audit failed: " + ", ".join(failed),
                outputs,
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "Broker Research pre-freeze placement, primary verification and quarantine audit passed",
            outputs,
        )

    return run
