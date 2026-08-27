from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from hashlib import sha256
import json
from typing import Callable, Mapping

from .broker_research import BrokerClaim, BrokerFieldClass, pre_freeze_allowed
from .control_plane import StageStatus
from .industry_dna import IndustryDNAProfile
from .ledger import EvidenceLedger
from .module_plan import ModuleRequirementPlan
from .module_plan_adapter import module_requirement_plan_adapter
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .records import (
    AuditFinding,
    AuditReport,
    BridgeRecord,
    EvidenceSourceLayer,
    HypothesisRecord,
)
from .scanner_runtime import (
    ScannerFinding,
    ScannerFindingStatus,
)


_LOCKED_PRE_FREEZE_FIELDS = frozenset(
    {
        BrokerFieldClass.TARGET_COMPANY_FORECAST,
        BrokerFieldClass.TARGET_PRICE,
        BrokerFieldClass.RATING,
        BrokerFieldClass.TARGET_MULTIPLE,
        BrokerFieldClass.CONSENSUS,
    }
)
_PRIMARY_VERIFICATION_LAYERS = frozenset(
    {
        EvidenceSourceLayer.REALIZED_OR_FILING,
        EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN,
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


def _iso_date(value: str, *, label: str) -> date:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is required")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be ISO YYYY-MM-DD: {value}") from exc


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
        if disposition is BrokerPreFreezeUse.QUARANTINED:
            raise ValueError(
                "target-company forecast/target/rating/multiple/consensus must not be loaded before Intrinsic Freeze"
            )
        if (
            disposition is BrokerPreFreezeUse.PRIMARY_VERIFICATION_ONLY
            and not self.verification_metrics
        ):
            raise ValueError(
                "target-company broker lead requires at least one metric-backed primary verification row"
            )


@dataclass(frozen=True)
class BrokerResearchBatch:
    checked_at: str
    observations: tuple[BrokerResearchObservation, ...]
    source_refs: tuple[str, ...]

    def validate(self, *, data_cutoff: str | None = None) -> None:
        if not self.checked_at or not self.observations or not self.source_refs:
            raise ValueError("broker research batch requires date, observations and sources")
        checked = _iso_date(self.checked_at, label="broker checked_at")
        cutoff = _iso_date(data_cutoff, label="data_cutoff") if data_cutoff else checked
        if checked > cutoff:
            raise ValueError("broker checked_at cannot be later than frozen data_cutoff")
        claim_ids = tuple(item.claim.claim_id for item in self.observations)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("broker research batch contains duplicate claim IDs")
        for item in self.observations:
            item.validate()
            report_date = _iso_date(
                item.claim.report_date,
                label=f"broker report_date[{item.claim.claim_id}]",
            )
            if report_date > cutoff:
                raise ValueError(
                    f"broker claim {item.claim.claim_id} is look-ahead evidence relative to {data_cutoff}"
                )
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
    checked_at: str = ""
    claim_verification_rows: tuple[tuple[str, str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.snapshot_hash or not self.source_refs or not self.checked_at:
            raise ValueError("broker pre-freeze result requires source refs, checked_at and hash")
        if self.quarantined_claims:
            raise ValueError(
                "locked target-company Street fields must not exist in pre-freeze runtime state"
            )
        claim_ids = tuple(
            claim.claim_id
            for claims in (self.context_claims, self.primary_verification_claims)
            for claim in claims
        )
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("broker pre-freeze claim partition overlaps")
        if len(self.verification_rows) != len(set(self.verification_rows)):
            raise ValueError("broker pre-freeze result has duplicate verification rows")
        if len(self.claim_verification_rows) != len(set(self.claim_verification_rows)):
            raise ValueError("broker pre-freeze result has duplicate claim verification rows")
        expected_target_claims = {item.claim_id for item in self.primary_verification_claims}
        mapped_target_claims = {row[0] for row in self.claim_verification_rows}
        if not expected_target_claims.issubset(mapped_target_claims):
            raise ValueError(
                "each target-company broker lead requires an auditable metric mapping"
            )

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
        if any(item.field_class in _LOCKED_PRE_FREEZE_FIELDS for item in (*self.context_claims, *self.primary_verification_claims)):
            raise ValueError("LLM broker context contains a locked Street field")


@dataclass(frozen=True)
class BrokerResearchAuditResult:
    report: AuditReport
    audit_hash: str
    verification_bindings: tuple[tuple[str, str, tuple[str, ...]], ...] = ()

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


def _result_payload(result: BrokerResearchPreFreezeResult) -> dict[str, object]:
    return {
        "contract": "broker_research_prefreeze/v2",
        "checked_at": result.checked_at,
        "context_claims": [_claim_payload(item) for item in result.context_claims],
        "primary_verification_claims": [
            _claim_payload(item) for item in result.primary_verification_claims
        ],
        "verification_rows": result.verification_rows,
        "claim_verification_rows": result.claim_verification_rows,
        "verification_requests": result.verification_requests,
        "primary_source_hints": result.primary_source_hints,
        "source_refs": result.source_refs,
    }


def build_broker_prefreeze_result(
    batch: BrokerResearchBatch,
    *,
    known_segments: tuple[str, ...],
    data_cutoff: str | None = None,
) -> BrokerResearchPreFreezeResult:
    batch.validate(data_cutoff=data_cutoff)
    known = set(known_segments)
    context_claims: list[BrokerClaim] = []
    verification_claims: list[BrokerClaim] = []
    verification_rows: list[tuple[str, str]] = []
    claim_rows: list[tuple[str, str, str]] = []
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
            raise ValueError(
                "locked target-company Street fields must be deferred to STREET_REFERENCE_LOAD"
            )
        for metric in observation.verification_metrics:
            verification_rows.append((observation.segment_id, metric))
            claim_rows.append(
                (observation.claim.claim_id, observation.segment_id, metric)
            )
        verification_requests.extend(observation.verification_requests)
        primary_source_hints.extend(observation.primary_source_hints)

    result = BrokerResearchPreFreezeResult(
        context_claims=tuple(context_claims),
        primary_verification_claims=tuple(verification_claims),
        quarantined_claims=(),
        verification_rows=tuple(dict.fromkeys(verification_rows)),
        verification_requests=tuple(dict.fromkeys(verification_requests)),
        primary_source_hints=tuple(dict.fromkeys(primary_source_hints)),
        source_refs=tuple(dict.fromkeys(batch.source_refs)),
        snapshot_hash="pending",
        checked_at=batch.checked_at,
        claim_verification_rows=tuple(dict.fromkeys(claim_rows)),
    )
    snapshot_hash = _stable_hash(_result_payload(result))
    return BrokerResearchPreFreezeResult(
        context_claims=result.context_claims,
        primary_verification_claims=result.primary_verification_claims,
        quarantined_claims=(),
        verification_rows=result.verification_rows,
        verification_requests=result.verification_requests,
        primary_source_hints=result.primary_source_hints,
        source_refs=result.source_refs,
        snapshot_hash=snapshot_hash,
        checked_at=result.checked_at,
        claim_verification_rows=result.claim_verification_rows,
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
        cutoff = context.data.get("data_cutoff")
        if not isinstance(profiles, tuple) or not profiles or not all(
            isinstance(item, IndustryDNAProfile) for item in profiles
        ):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "IndustryDNA profiles are required before Broker Research discovery",
                blocking=True,
            )
        if not isinstance(cutoff, str) or not cutoff:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "frozen data_cutoff is required before pre-freeze Broker Research",
                blocking=True,
            )
        try:
            batch = loader(context)
            if not isinstance(batch, BrokerResearchBatch):
                raise TypeError("BrokerResearchLoader must return BrokerResearchBatch")
            result = build_broker_prefreeze_result(
                batch,
                known_segments=tuple(item.segment_id for item in profiles),
                data_cutoff=cutoff,
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
            "broker_quarantined_claims": (),
            "broker_primary_verification_requests": result.verification_requests,
            "broker_primary_source_hints": result.primary_source_hints,
            "broker_additional_required_evidence": result.additional_required_evidence,
            "broker_research_rocket_required": True,
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
                "Broker Research discovery produced only pre-freeze-safe context and metric-backed primary verification requirements | "
                + plan_stage.rationale
            ),
            outputs,
        )

    return run


def broker_aware_rocket_insight_adapter(
    inner: StageAdapter,
    *,
    required: bool,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        result = inner(context)
        if result.blocking:
            return result
        broker = context.data.get("broker_research_prefreeze_result")
        if broker is None:
            if required:
                return StageExecutionResult(
                    StageStatus.RECOVERY_REQUIRED,
                    "required Broker Research result is missing before Rocket Insight",
                    result.outputs,
                    blocking=True,
                )
            return result
        if not isinstance(broker, BrokerResearchPreFreezeResult):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "broker_research_prefreeze_result has invalid type before Rocket Insight",
                result.outputs,
                blocking=True,
            )
        outputs = dict(result.outputs)
        findings = outputs.get("scanner_findings", ())
        traces = outputs.get("scanner_impact_traces", ())
        effort = dict(outputs.get("scanner_research_effort", {}))
        if not isinstance(findings, tuple) or not isinstance(traces, tuple):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "Rocket Insight outputs are malformed before Broker Research merge",
                result.outputs,
                blocking=True,
            )
        broker_finding = ScannerFinding(
            scanner_id="BROKER_RESEARCH",
            status=ScannerFindingStatus.PASS,
            summary=(
                "sell-side research supplied mechanism context and primary-verification leads; locked target-company Street fields were absent pre-freeze"
            ),
            mechanism_ids=tuple(item.claim_id for item in broker.context_claims),
            hypothesis_candidates=tuple(item.claim_id for item in broker.context_claims),
            verification_requests=broker.verification_requests,
            context_only=not bool(broker.verification_requests or broker.context_claims),
        )
        ledger = context.data.get("evidence_ledger")
        if not isinstance(ledger, EvidenceLedger):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "EvidenceLedger missing before Broker Research Rocket merge",
                result.outputs,
                blocking=True,
            )
        broker_finding.validate(ledger)
        outputs["scanner_findings"] = (*findings, broker_finding)
        outputs["scanner_impact_traces"] = (*traces, broker_finding.impact_trace())
        effort["BROKER_RESEARCH"] = broker_finding.effort
        outputs["scanner_research_effort"] = effort
        outputs["broker_research_rocket_connected"] = True
        outputs["broker_research_rocket_claim_ids"] = tuple(
            item.claim_id for item in (*broker.context_claims, *broker.primary_verification_claims)
        )
        return StageExecutionResult(
            result.status,
            result.rationale + " | Broker Research context/verification leads connected to Rocket Insight",
            outputs,
        )

    return run


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
        cutoff = context.data.get("data_cutoff")
        if not isinstance(ledger, EvidenceLedger) or not isinstance(plan, ModuleRequirementPlan):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "EvidenceLedger and ModuleRequirementPlan are required for Broker Research audit",
                blocking=True,
            )
        if not isinstance(hypotheses, tuple) or not all(isinstance(item, HypothesisRecord) for item in hypotheses):
            return StageExecutionResult(StageStatus.BLOCKED, "Broker Research audit requires typed hypotheses", blocking=True)
        if not isinstance(bridges, tuple) or not all(isinstance(item, BridgeRecord) for item in bridges):
            return StageExecutionResult(StageStatus.BLOCKED, "Broker Research audit requires typed Bridges", blocking=True)

        exact_hash_ok = snapshot_hash == _stable_hash(_result_payload(result))
        cutoff_ok = True
        try:
            frozen_cutoff = _iso_date(cutoff, label="data_cutoff")
            cutoff_ok = _iso_date(result.checked_at, label="broker checked_at") <= frozen_cutoff and all(
                _iso_date(item.report_date, label=f"broker report_date[{item.claim_id}]") <= frozen_cutoff
                for item in (*result.context_claims, *result.primary_verification_claims)
            )
        except ValueError:
            cutoff_ok = False
        locked_absent = not result.quarantined_claims and all(
            item.field_class not in _LOCKED_PRE_FREEZE_FIELDS
            for item in (*result.context_claims, *result.primary_verification_claims)
        )
        target_metric_mapping_ok = {
            item.claim_id for item in result.primary_verification_claims
        }.issubset({row[0] for row in result.claim_verification_rows})
        plan_rows = {
            (segment.segment_id, metric)
            for segment in plan.segments
            for metric in segment.required_evidence
        }
        plan_coverage_ok = set(result.verification_rows).issubset(plan_rows)

        bindings: list[tuple[str, str, tuple[str, ...]]] = []
        for segment_id, metric in result.verification_rows:
            evidence_ids = tuple(
                item.id
                for item in ledger.active()
                if item.segment == segment_id
                and item.metric == metric
                and item.source_layer in _PRIMARY_VERIFICATION_LAYERS
            )
            bindings.append((segment_id, metric, evidence_ids))
        primary_verification_ok = all(binding[2] for binding in bindings)

        claim_ids = {
            claim.claim_id
            for claim in (*result.context_claims, *result.primary_verification_claims)
        }
        hypothesis_evidence_ids = {
            evidence_id
            for item in hypotheses
            for evidence_id in (*item.supporting_evidence_ids, *item.contradicting_evidence_ids)
        }
        bridge_evidence_ids = {
            evidence_id for item in bridges for evidence_id in item.evidence_ids
        }
        no_direct_broker_evidence = not claim_ids.intersection(
            hypothesis_evidence_ids | bridge_evidence_ids
        )
        broker_source_refs = set(result.source_refs)
        no_broker_sources_in_ledger = not broker_source_refs.intersection(
            item.source_ref for item in ledger.active()
        )
        rocket_connected = bool(context.data.get("broker_research_rocket_connected")) and any(
            getattr(item, "scanner_id", None) == "BROKER_RESEARCH"
            for item in context.data.get("scanner_findings", ())
        )

        findings = (
            AuditFinding("broker_locked_fields_absent_prefreeze", locked_absent, True, "target forecast/target/rating/multiple/consensus are absent from pre-freeze runtime state"),
            AuditFinding("broker_cutoff_integrity", cutoff_ok, True, "broker checked_at/report_date do not exceed frozen data_cutoff"),
            AuditFinding("broker_snapshot_exact_hash", exact_hash_ok, True, "broker pre-freeze snapshot hash exactly binds its semantic payload"),
            AuditFinding("broker_target_leads_metric_backed", target_metric_mapping_ok, True, "every target-company broker lead maps to at least one auditable metric"),
            AuditFinding("broker_verification_in_module_plan", plan_coverage_ok, True, "all broker-discovered verification metrics were compiled into required Evidence"),
            AuditFinding("broker_verification_company_primary", primary_verification_ok, True, "all broker-discovered verification metrics are satisfied only by active company-primary Evidence"),
            AuditFinding("broker_claims_not_direct_assumption_evidence", no_direct_broker_evidence, True, "broker claim IDs never became Hypothesis or Bridge Evidence IDs"),
            AuditFinding("broker_sources_not_in_primary_ledger", no_broker_sources_in_ledger, True, "broker report sources never entered the primary EvidenceLedger"),
            AuditFinding("broker_research_connected_to_rocket_insight", rocket_connected, True, "Broker Research context and verification leads are present in Rocket Insight outputs"),
        )
        report = AuditReport(findings)
        audit_hash = _stable_hash(
            {
                "contract": "broker_research_audit/v2",
                "broker_snapshot_hash": snapshot_hash,
                "verification_bindings": bindings,
                "rocket_connected": rocket_connected,
                "findings": tuple(
                    (item.check, item.passed, item.blocking, item.detail)
                    for item in findings
                ),
            }
        )
        audit_result = BrokerResearchAuditResult(
            report,
            audit_hash,
            tuple(bindings),
        )
        outputs = {
            "broker_research_audit_required": True,
            "broker_research_audit_result": audit_result,
            "broker_research_audit_report": report,
            "broker_research_audit_hash": audit_hash,
            "broker_research_audit_passed": report.passed,
            "broker_primary_verification_bindings": tuple(bindings),
        }
        if not report.passed:
            failed = tuple(item.check for item in findings if item.blocking and not item.passed)
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "Broker Research audit failed: " + ", ".join(failed),
                outputs,
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "Broker Research cutoff, blind-lock, Rocket connection and company-primary verification audit passed",
            outputs,
        )

    return run


__all__ = [
    "BrokerPreFreezeUse",
    "BrokerResearchObservation",
    "BrokerResearchBatch",
    "BrokerResearchPreFreezeResult",
    "BrokerResearchLLMContext",
    "BrokerResearchAuditResult",
    "BrokerResearchLoader",
    "pre_freeze_use",
    "build_broker_prefreeze_result",
    "broker_aware_module_requirement_plan_adapter",
    "broker_aware_rocket_insight_adapter",
    "broker_research_audit_adapter",
]
