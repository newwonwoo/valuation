from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
import json

from .control_plane import StageStatus
from .ledger import EvidenceLedger
from .module_plan import ModuleRequirementPlan
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .records import EvidenceRecord, EvidenceSourceLayer


class CapacityCommitmentStage(str, Enum):
    ANNOUNCED = "announced"
    BOARD_APPROVED = "board_approved"
    SITE_OPTIONED = "site_optioned"
    SITE_CONTRACTED = "site_contracted"
    SITE_ACQUIRED = "site_acquired"
    PERMITTED = "permitted"
    CONSTRUCTION_CONTRACTED = "construction_contracted"
    UNDER_CONSTRUCTION = "under_construction"
    EQUIPMENT_ORDERED = "equipment_ordered"
    EQUIPMENT_INSTALLED = "equipment_installed"
    COMMISSIONING = "commissioning"
    OPERATING = "operating"
    CANCELLED = "cancelled"


_STAGE_RANK = {
    CapacityCommitmentStage.ANNOUNCED: 1,
    CapacityCommitmentStage.BOARD_APPROVED: 2,
    CapacityCommitmentStage.SITE_OPTIONED: 3,
    CapacityCommitmentStage.SITE_CONTRACTED: 4,
    CapacityCommitmentStage.SITE_ACQUIRED: 5,
    CapacityCommitmentStage.PERMITTED: 6,
    CapacityCommitmentStage.CONSTRUCTION_CONTRACTED: 7,
    CapacityCommitmentStage.UNDER_CONSTRUCTION: 8,
    CapacityCommitmentStage.EQUIPMENT_ORDERED: 9,
    CapacityCommitmentStage.EQUIPMENT_INSTALLED: 10,
    CapacityCommitmentStage.COMMISSIONING: 11,
    CapacityCommitmentStage.OPERATING: 12,
    # Cancellation wins when competing active events have the same effective date.
    CapacityCommitmentStage.CANCELLED: 10_000,
}


class CapacityQuantificationStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    DISCLOSED = "disclosed"
    BOUNDED_INPUTS_AVAILABLE = "bounded_inputs_available"
    UNQUANTIFIED = "unquantified"


@dataclass(frozen=True)
class CapacityCommitmentPolicy:
    core_inclusion_threshold: CapacityCommitmentStage = (
        CapacityCommitmentStage.SITE_CONTRACTED
    )
    stage_metric: str = "capacity_commitment_stage"
    committed_capacity_metric: str = "expansion_capacity_committed"
    site_area_metric: str = "expansion_site_area"
    committed_capex_metric: str = "expansion_capex_committed"
    ramp_date_metric: str = "expansion_ramp_date"
    equipment_commitment_metric: str = "expansion_equipment_commitment"
    capacity_archetype: str = "capacity_manufacturing"
    eligible_source_layers: tuple[EvidenceSourceLayer, ...] = (
        EvidenceSourceLayer.REALIZED_OR_FILING,
        EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN,
    )

    def validate(self) -> None:
        metrics = (
            self.stage_metric,
            self.committed_capacity_metric,
            self.site_area_metric,
            self.committed_capex_metric,
            self.ramp_date_metric,
            self.equipment_commitment_metric,
        )
        if not self.capacity_archetype or not all(metrics):
            raise ValueError("capacity commitment policy requires archetype and metrics")
        if len(metrics) != len(set(metrics)):
            raise ValueError("capacity commitment policy metrics must be unique")
        if not self.eligible_source_layers:
            raise ValueError("capacity commitment policy requires eligible source layers")
        if self.core_inclusion_threshold is CapacityCommitmentStage.CANCELLED:
            raise ValueError("CANCELLED cannot be a Core inclusion threshold")


@dataclass(frozen=True)
class CapacitySegmentAssessment:
    segment_id: str
    latest_stage: CapacityCommitmentStage | None
    stage_evidence_id: str | None
    core_inclusion_required: bool
    quantification_status: CapacityQuantificationStatus
    committed_capacity_evidence_ids: tuple[str, ...]
    site_area_evidence_ids: tuple[str, ...]
    committed_capex_evidence_ids: tuple[str, ...]
    ramp_date_evidence_ids: tuple[str, ...]
    equipment_commitment_evidence_ids: tuple[str, ...]
    recovery_required: bool
    rationale: str

    def __post_init__(self) -> None:
        if not self.segment_id or not self.rationale:
            raise ValueError("capacity segment assessment requires segment and rationale")
        if self.latest_stage is None and self.stage_evidence_id is not None:
            raise ValueError("capacity stage Evidence requires a typed latest_stage")
        if self.latest_stage is not None and not self.stage_evidence_id:
            raise ValueError("typed capacity stage requires stage Evidence")
        if self.latest_stage is CapacityCommitmentStage.CANCELLED:
            if self.core_inclusion_required:
                raise ValueError("cancelled capacity cannot remain Core-inclusion required")
            if self.quantification_status is not CapacityQuantificationStatus.NOT_REQUIRED:
                raise ValueError("cancelled capacity quantification must be NOT_REQUIRED")
        if self.core_inclusion_required and (
            self.quantification_status is CapacityQuantificationStatus.NOT_REQUIRED
        ):
            raise ValueError("Core-inclusion capacity requires a quantification state")

    @property
    def qualifying_evidence_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    *((self.stage_evidence_id,) if self.stage_evidence_id else ()),
                    *self.committed_capacity_evidence_ids,
                    *self.site_area_evidence_ids,
                    *self.committed_capex_evidence_ids,
                    *self.ramp_date_evidence_ids,
                    *self.equipment_commitment_evidence_ids,
                )
            )
        )


@dataclass(frozen=True)
class CapacityCommitmentAssessment:
    segments: tuple[CapacitySegmentAssessment, ...]
    assessment_hash: str

    def __post_init__(self) -> None:
        if not self.assessment_hash:
            raise ValueError("capacity commitment assessment requires a hash")
        segment_ids = tuple(item.segment_id for item in self.segments)
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("capacity commitment assessment has duplicate segments")

    @property
    def core_inclusion_required_segments(self) -> tuple[str, ...]:
        return tuple(
            item.segment_id for item in self.segments if item.core_inclusion_required
        )

    @property
    def recovery_required_segments(self) -> tuple[str, ...]:
        return tuple(item.segment_id for item in self.segments if item.recovery_required)


def _parse_stage(value: object) -> CapacityCommitmentStage:
    text = str(value or "").strip().lower()
    try:
        return CapacityCommitmentStage(text)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in CapacityCommitmentStage)
        raise ValueError(
            f"invalid capacity commitment stage {value!r}; allowed: {allowed}"
        ) from exc


def _positive_decimal(record: EvidenceRecord) -> Decimal:
    text = str(record.value or "").strip().replace(",", "")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(
            f"{record.metric} Evidence {record.id} must be numeric"
        ) from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(
            f"{record.metric} Evidence {record.id} must be finite and positive"
        )
    return value


def _validate_ramp_date(record: EvidenceRecord) -> None:
    text = str(record.value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"{record.metric} Evidence {record.id} must be an ISO date"
        ) from exc
    if parsed.isoformat() != text:
        raise ValueError(
            f"{record.metric} Evidence {record.id} must use YYYY-MM-DD"
        )


def _latest_stage_record(
    records: tuple[EvidenceRecord, ...],
    *,
    policy: CapacityCommitmentPolicy,
) -> tuple[EvidenceRecord, CapacityCommitmentStage] | None:
    candidates: list[tuple[EvidenceRecord, CapacityCommitmentStage]] = []
    for record in records:
        if record.metric != policy.stage_metric:
            continue
        if record.source_layer not in policy.eligible_source_layers:
            continue
        stage = _parse_stage(record.value)
        candidates.append((record, stage))
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            date.fromisoformat(item[0].effective_date[:10]),
            _STAGE_RANK[item[1]],
            item[0].id,
        ),
    )


def _metric_records(
    records: tuple[EvidenceRecord, ...],
    metric: str,
    *,
    policy: CapacityCommitmentPolicy,
) -> tuple[EvidenceRecord, ...]:
    return tuple(
        sorted(
            (
                item
                for item in records
                if item.metric == metric
                and item.source_layer in policy.eligible_source_layers
            ),
            key=lambda item: (item.effective_date, item.id),
        )
    )


def _stable_hash(
    assessments: tuple[CapacitySegmentAssessment, ...],
    evidence_by_id: dict[str, EvidenceRecord],
    policy: CapacityCommitmentPolicy,
) -> str:
    evidence_ids = tuple(
        sorted(
            {
                evidence_id
                for assessment in assessments
                for evidence_id in assessment.qualifying_evidence_ids
            }
        )
    )
    payload = {
        "contract": "capacity_commitment_assessment/v1",
        "policy": {
            "core_inclusion_threshold": policy.core_inclusion_threshold.value,
            "stage_metric": policy.stage_metric,
            "committed_capacity_metric": policy.committed_capacity_metric,
            "site_area_metric": policy.site_area_metric,
            "committed_capex_metric": policy.committed_capex_metric,
            "ramp_date_metric": policy.ramp_date_metric,
            "equipment_commitment_metric": policy.equipment_commitment_metric,
            "capacity_archetype": policy.capacity_archetype,
            "eligible_source_layers": tuple(
                item.value for item in policy.eligible_source_layers
            ),
        },
        "segments": [
            {
                "segment_id": item.segment_id,
                "latest_stage": item.latest_stage.value if item.latest_stage else None,
                "stage_evidence_id": item.stage_evidence_id,
                "core_inclusion_required": item.core_inclusion_required,
                "quantification_status": item.quantification_status.value,
                "committed_capacity_evidence_ids": item.committed_capacity_evidence_ids,
                "site_area_evidence_ids": item.site_area_evidence_ids,
                "committed_capex_evidence_ids": item.committed_capex_evidence_ids,
                "ramp_date_evidence_ids": item.ramp_date_evidence_ids,
                "equipment_commitment_evidence_ids": (
                    item.equipment_commitment_evidence_ids
                ),
                "recovery_required": item.recovery_required,
            }
            for item in assessments
        ],
        "evidence": [
            {
                "id": evidence_by_id[evidence_id].id,
                "metric": evidence_by_id[evidence_id].metric,
                "value": str(evidence_by_id[evidence_id].value),
                "unit": evidence_by_id[evidence_id].unit,
                "source_layer": evidence_by_id[evidence_id].source_layer.value,
                "effective_date": evidence_by_id[evidence_id].effective_date,
                "observed_date": evidence_by_id[evidence_id].observed_date,
                "source_ref": evidence_by_id[evidence_id].source_ref,
                "segment": evidence_by_id[evidence_id].segment,
            }
            for evidence_id in evidence_ids
        ],
    }
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def assess_capacity_commitment(
    *,
    plan: ModuleRequirementPlan,
    ledger: EvidenceLedger,
    policy: CapacityCommitmentPolicy | None = None,
) -> CapacityCommitmentAssessment:
    effective_policy = policy or CapacityCommitmentPolicy()
    effective_policy.validate()
    plan.validate()

    active = ledger.active()
    evidence_by_id = {item.id: item for item in active}
    assessments: list[CapacitySegmentAssessment] = []

    capacity_segments = tuple(
        segment
        for segment in plan.segments
        if effective_policy.capacity_archetype in segment.archetypes
    )
    for segment in capacity_segments:
        records = tuple(item for item in active if item.segment == segment.segment_id)
        latest = _latest_stage_record(records, policy=effective_policy)

        capacity_records = _metric_records(
            records,
            effective_policy.committed_capacity_metric,
            policy=effective_policy,
        )
        site_records = _metric_records(
            records,
            effective_policy.site_area_metric,
            policy=effective_policy,
        )
        capex_records = _metric_records(
            records,
            effective_policy.committed_capex_metric,
            policy=effective_policy,
        )
        ramp_records = _metric_records(
            records,
            effective_policy.ramp_date_metric,
            policy=effective_policy,
        )
        equipment_records = _metric_records(
            records,
            effective_policy.equipment_commitment_metric,
            policy=effective_policy,
        )

        for record in (*capacity_records, *site_records, *capex_records):
            _positive_decimal(record)
        for record in ramp_records:
            _validate_ramp_date(record)

        if latest is None:
            assessments.append(
                CapacitySegmentAssessment(
                    segment_id=segment.segment_id,
                    latest_stage=None,
                    stage_evidence_id=None,
                    core_inclusion_required=False,
                    quantification_status=CapacityQuantificationStatus.NOT_REQUIRED,
                    committed_capacity_evidence_ids=tuple(
                        item.id for item in capacity_records
                    ),
                    site_area_evidence_ids=tuple(item.id for item in site_records),
                    committed_capex_evidence_ids=tuple(
                        item.id for item in capex_records
                    ),
                    ramp_date_evidence_ids=tuple(item.id for item in ramp_records),
                    equipment_commitment_evidence_ids=tuple(
                        item.id for item in equipment_records
                    ),
                    recovery_required=True,
                    rationale=(
                        "capacity_manufacturing requires official/filing commitment-stage "
                        "Evidence before Core capacity treatment can be decided"
                    ),
                )
            )
            continue

        stage_record, stage = latest
        if stage is CapacityCommitmentStage.CANCELLED:
            assessments.append(
                CapacitySegmentAssessment(
                    segment_id=segment.segment_id,
                    latest_stage=stage,
                    stage_evidence_id=stage_record.id,
                    core_inclusion_required=False,
                    quantification_status=CapacityQuantificationStatus.NOT_REQUIRED,
                    committed_capacity_evidence_ids=tuple(
                        item.id for item in capacity_records
                    ),
                    site_area_evidence_ids=tuple(item.id for item in site_records),
                    committed_capex_evidence_ids=tuple(
                        item.id for item in capex_records
                    ),
                    ramp_date_evidence_ids=tuple(item.id for item in ramp_records),
                    equipment_commitment_evidence_ids=tuple(
                        item.id for item in equipment_records
                    ),
                    recovery_required=False,
                    rationale=(
                        "latest active official capacity event is CANCELLED; prior "
                        "expansion progress cannot remain in Core"
                    ),
                )
            )
            continue

        threshold_rank = _STAGE_RANK[effective_policy.core_inclusion_threshold]
        core_required = _STAGE_RANK[stage] >= threshold_rank
        if not core_required:
            quantification = CapacityQuantificationStatus.NOT_REQUIRED
            recovery_required = False
            rationale = (
                f"latest capacity stage {stage.value} is below Core threshold "
                f"{effective_policy.core_inclusion_threshold.value}"
            )
        elif capacity_records:
            quantification = CapacityQuantificationStatus.DISCLOSED
            recovery_required = False
            rationale = (
                f"latest capacity stage {stage.value} crosses the Core threshold and "
                "positive committed capacity is disclosed"
            )
        elif site_records or capex_records:
            quantification = CapacityQuantificationStatus.BOUNDED_INPUTS_AVAILABLE
            recovery_required = False
            rationale = (
                f"latest capacity stage {stage.value} crosses the Core threshold; "
                "exact capacity is absent but site/CAPEX inputs require a bounded Core path"
            )
        else:
            quantification = CapacityQuantificationStatus.UNQUANTIFIED
            recovery_required = True
            rationale = (
                f"latest capacity stage {stage.value} crosses the Core threshold, but "
                "no disclosed capacity, site area or committed CAPEX exists; zero "
                "expansion is forbidden and bounded quantification is required"
            )

        assessments.append(
            CapacitySegmentAssessment(
                segment_id=segment.segment_id,
                latest_stage=stage,
                stage_evidence_id=stage_record.id,
                core_inclusion_required=core_required,
                quantification_status=quantification,
                committed_capacity_evidence_ids=tuple(
                    item.id for item in capacity_records
                ),
                site_area_evidence_ids=tuple(item.id for item in site_records),
                committed_capex_evidence_ids=tuple(
                    item.id for item in capex_records
                ),
                ramp_date_evidence_ids=tuple(item.id for item in ramp_records),
                equipment_commitment_evidence_ids=tuple(
                    item.id for item in equipment_records
                ),
                recovery_required=recovery_required,
                rationale=rationale,
            )
        )

    assessment_tuple = tuple(assessments)
    return CapacityCommitmentAssessment(
        segments=assessment_tuple,
        assessment_hash=_stable_hash(
            assessment_tuple,
            evidence_by_id,
            effective_policy,
        ),
    )


def capacity_commitment_gate_adapter(
    *,
    policy: CapacityCommitmentPolicy | None = None,
) -> StageAdapter:
    effective_policy = policy or CapacityCommitmentPolicy()
    effective_policy.validate()

    def run(context: OrchestratorContext) -> StageExecutionResult:
        plan = context.data.get("module_requirement_plan")
        ledger = context.data.get("evidence_ledger")
        if not isinstance(plan, ModuleRequirementPlan):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "ModuleRequirementPlan is required before Capacity Commitment Gate",
                blocking=True,
            )
        if not isinstance(ledger, EvidenceLedger):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "EvidenceLedger is required before Capacity Commitment Gate",
                blocking=True,
            )
        try:
            assessment = assess_capacity_commitment(
                plan=plan,
                ledger=ledger,
                policy=effective_policy,
            )
        except (TypeError, ValueError) as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"Capacity Commitment Gate failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        outputs = {
            "capacity_commitment_assessment": assessment,
            "capacity_commitment_assessment_hash": assessment.assessment_hash,
            "core_capacity_inclusion_required_segments": (
                assessment.core_inclusion_required_segments
            ),
            "capacity_commitment_recovery_segments": (
                assessment.recovery_required_segments
            ),
        }
        if not assessment.segments:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "no capacity_manufacturing segment requires Capacity Commitment Gate",
                outputs,
            )
        if assessment.recovery_required_segments:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "capacity commitment requires evidence recovery or bounded quantification for: "
                + ", ".join(assessment.recovery_required_segments),
                outputs,
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "capacity commitment stages were classified and Core-inclusion obligations frozen",
            outputs,
        )

    return run
