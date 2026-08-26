from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
import json
from typing import Callable

from .control_plane import StageStatus
from .ledger import EvidenceLedger
from .module_plan import ModuleRequirementPlan
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .records import EvidenceRecord, EvidenceSourceLayer
from .signal_intelligence import ProjectGate, ProjectGateEvidence, ProjectGateSet


class CapacityProjectDisposition(str, Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


class BaselineInclusionStatus(str, Enum):
    NOT_IN_BASELINE = "not_in_baseline"
    IN_BASELINE = "in_baseline"
    UNKNOWN = "unknown"


class CapacityQuantificationStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    DISCLOSED = "disclosed"
    BOUNDED_INPUTS_AVAILABLE = "bounded_inputs_available"
    UNQUANTIFIED = "unquantified"


@dataclass(frozen=True)
class CapacityCommitmentPolicy:
    """Capacity-specific interpretation of the canonical ProjectGateSet.

    Project gates remain independent. In particular, construction or commissioning does not
    silently prove land control. The user's Core threshold is an explicitly verified LAND_CONTROL
    gate, which covers a signed purchase/lease contract or completed site acquisition.
    """

    core_inclusion_gate: ProjectGate = ProjectGate.LAND_CONTROL
    capacity_archetype: str = "capacity_manufacturing"
    land_control_metric: str = "expansion_land_control"
    committed_capacity_metric: str = "expansion_capacity_committed"
    site_area_metric: str = "expansion_site_area"
    committed_capex_metric: str = "expansion_capex_committed"
    ramp_date_metric: str = "expansion_ramp_date"
    equipment_commitment_metric: str = "expansion_equipment_commitment"
    baseline_inclusion_metric: str = "expansion_baseline_inclusion"
    cancellation_metric: str = "expansion_cancelled"
    no_active_expansion_metric: str = "no_active_capacity_expansion"
    eligible_source_layers: tuple[EvidenceSourceLayer, ...] = (
        EvidenceSourceLayer.REALIZED_OR_FILING,
        EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN,
    )

    def validate(self) -> None:
        metrics = (
            self.land_control_metric,
            self.committed_capacity_metric,
            self.site_area_metric,
            self.committed_capex_metric,
            self.ramp_date_metric,
            self.equipment_commitment_metric,
            self.baseline_inclusion_metric,
            self.cancellation_metric,
            self.no_active_expansion_metric,
        )
        if not self.capacity_archetype or not all(metrics):
            raise ValueError("capacity commitment policy requires archetype and metrics")
        if len(metrics) != len(set(metrics)):
            raise ValueError("capacity commitment policy metrics must be unique")
        if not self.eligible_source_layers:
            raise ValueError("capacity commitment policy requires eligible source layers")
        if self.core_inclusion_gate is not ProjectGate.LAND_CONTROL:
            raise ValueError(
                "capacity Core inclusion must be bound to canonical ProjectGate.LAND_CONTROL"
            )


@dataclass(frozen=True)
class CapacityProjectBinding:
    """Typed project-to-segment binding supplied by an authorized live provider.

    The gate does not infer project identity from free-form notes or source URLs. Every Evidence ID
    is replayed against the frozen EvidenceLedger.
    """

    project_id: str
    segment_id: str
    gate_set: ProjectGateSet
    baseline_inclusion: BaselineInclusionStatus
    baseline_inclusion_evidence_ids: tuple[str, ...]
    disposition: CapacityProjectDisposition = CapacityProjectDisposition.ACTIVE
    disposition_evidence_ids: tuple[str, ...] = ()
    committed_capacity_evidence_ids: tuple[str, ...] = ()
    site_area_evidence_ids: tuple[str, ...] = ()
    committed_capex_evidence_ids: tuple[str, ...] = ()
    ramp_date_evidence_ids: tuple[str, ...] = ()
    equipment_commitment_evidence_ids: tuple[str, ...] = ()

    def validate(self, *, policy: CapacityCommitmentPolicy) -> None:
        if not self.project_id or not self.segment_id:
            raise ValueError("capacity project binding requires project and segment IDs")
        self.gate_set.validate()
        if self.gate_set.project_id != self.project_id:
            raise ValueError("CapacityProjectBinding project_id must match ProjectGateSet")
        if policy.core_inclusion_gate not in self.gate_set.required_gates:
            raise ValueError(
                "capacity project gate set must explicitly require LAND_CONTROL"
            )
        all_ids = (
            *self.baseline_inclusion_evidence_ids,
            *self.disposition_evidence_ids,
            *self.committed_capacity_evidence_ids,
            *self.site_area_evidence_ids,
            *self.committed_capex_evidence_ids,
            *self.ramp_date_evidence_ids,
            *self.equipment_commitment_evidence_ids,
        )
        if len(all_ids) != len(set(all_ids)):
            raise ValueError(
                f"capacity project {self.project_id} reuses Evidence across semantic roles"
            )
        if self.baseline_inclusion is not BaselineInclusionStatus.UNKNOWN and not (
            self.baseline_inclusion_evidence_ids
        ):
            raise ValueError(
                "known baseline-inclusion status requires explicit Evidence"
            )
        if (
            self.disposition is CapacityProjectDisposition.CANCELLED
            and not self.disposition_evidence_ids
        ):
            raise ValueError("cancelled capacity project requires cancellation Evidence")


@dataclass(frozen=True)
class CapacitySegmentCommitmentInput:
    segment_id: str
    projects: tuple[CapacityProjectBinding, ...] = ()
    no_active_expansion_evidence_ids: tuple[str, ...] = ()

    def validate(self, *, policy: CapacityCommitmentPolicy) -> None:
        if not self.segment_id:
            raise ValueError("capacity segment commitment input requires segment_id")
        if self.projects and self.no_active_expansion_evidence_ids:
            raise ValueError(
                "capacity segment cannot declare both projects and no active expansion"
            )
        if not self.projects and not self.no_active_expansion_evidence_ids:
            raise ValueError(
                "capacity segment requires projects or explicit no-active-expansion Evidence"
            )
        project_ids = tuple(item.project_id for item in self.projects)
        if len(project_ids) != len(set(project_ids)):
            raise ValueError("capacity segment has duplicate project IDs")
        for project in self.projects:
            project.validate(policy=policy)
            if project.segment_id != self.segment_id:
                raise ValueError("capacity project segment_id does not match segment input")


@dataclass(frozen=True)
class CapacityCommitmentInput:
    segments: tuple[CapacitySegmentCommitmentInput, ...]

    def validate(self, *, policy: CapacityCommitmentPolicy) -> None:
        if not self.segments:
            raise ValueError("capacity commitment input requires segment coverage")
        segment_ids = tuple(item.segment_id for item in self.segments)
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("capacity commitment input has duplicate segments")
        for item in self.segments:
            item.validate(policy=policy)


@dataclass(frozen=True)
class CapacityProjectAssessment:
    project_id: str
    segment_id: str
    verified_gates: tuple[ProjectGate, ...]
    land_control_verified: bool
    baseline_inclusion: BaselineInclusionStatus
    disposition: CapacityProjectDisposition
    core_inclusion_required: bool
    quantification_status: CapacityQuantificationStatus
    qualifying_evidence_ids: tuple[str, ...]
    recovery_required: bool
    rationale: str

    def __post_init__(self) -> None:
        if not self.project_id or not self.segment_id or not self.rationale:
            raise ValueError("capacity project assessment is incomplete")
        if self.core_inclusion_required and (
            self.quantification_status is CapacityQuantificationStatus.NOT_REQUIRED
        ):
            raise ValueError("Core-inclusion project requires quantification status")
        if self.disposition is CapacityProjectDisposition.CANCELLED and (
            self.core_inclusion_required
        ):
            raise ValueError("cancelled project cannot remain Core-inclusion required")


@dataclass(frozen=True)
class CapacitySegmentAssessment:
    segment_id: str
    projects: tuple[CapacityProjectAssessment, ...]
    no_active_expansion_verified: bool
    no_active_expansion_evidence_ids: tuple[str, ...]
    recovery_required: bool
    rationale: str

    def __post_init__(self) -> None:
        if not self.segment_id or not self.rationale:
            raise ValueError("capacity segment assessment is incomplete")
        project_ids = tuple(item.project_id for item in self.projects)
        if len(project_ids) != len(set(project_ids)):
            raise ValueError("capacity segment assessment has duplicate projects")
        if self.projects and self.no_active_expansion_verified:
            raise ValueError("segment cannot have projects and verified no-active status")


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
    def core_inclusion_required_projects(self) -> tuple[str, ...]:
        return tuple(
            project.project_id
            for segment in self.segments
            for project in segment.projects
            if project.core_inclusion_required
        )

    @property
    def core_inclusion_required_segments(self) -> tuple[str, ...]:
        return tuple(
            segment.segment_id
            for segment in self.segments
            if any(project.core_inclusion_required for project in segment.projects)
        )

    @property
    def recovery_required_segments(self) -> tuple[str, ...]:
        return tuple(item.segment_id for item in self.segments if item.recovery_required)


CapacityCommitmentLoader = Callable[
    [OrchestratorContext], CapacityCommitmentInput
]


def _parse_iso_datetime(value: str, label: str) -> None:
    text = str(value or "").strip()
    if not text:
        return
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be ISO date/datetime") from exc


def _positive_decimal(record: EvidenceRecord) -> Decimal:
    text = str(record.value if record.value is not None else "").strip().replace(",", "")
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


def _records_for_ids(
    evidence_by_id: dict[str, EvidenceRecord],
    evidence_ids: tuple[str, ...],
    *,
    expected_metric: str | None,
    segment_id: str,
    policy: CapacityCommitmentPolicy,
) -> tuple[EvidenceRecord, ...]:
    records: list[EvidenceRecord] = []
    for evidence_id in evidence_ids:
        try:
            record = evidence_by_id[evidence_id]
        except KeyError as exc:
            raise ValueError(
                f"capacity contract references inactive/unknown Evidence {evidence_id}"
            ) from exc
        if record.source_layer not in policy.eligible_source_layers:
            raise PermissionError(
                f"capacity Evidence {evidence_id} cannot use source layer "
                f"{record.source_layer.value} pre-freeze"
            )
        if record.segment != segment_id:
            raise ValueError(
                f"capacity Evidence {evidence_id} segment mismatch: "
                f"expected {segment_id}, got {record.segment}"
            )
        if expected_metric is not None and record.metric != expected_metric:
            raise ValueError(
                f"capacity Evidence {evidence_id} metric mismatch: "
                f"expected {expected_metric}, got {record.metric}"
            )
        records.append(record)
    return tuple(records)


def _gate_observation(
    gate_set: ProjectGateSet,
    gate: ProjectGate,
) -> ProjectGateEvidence | None:
    for observation in gate_set.observations:
        if observation.gate is gate:
            return observation
    return None


def _validate_gate_evidence(
    binding: CapacityProjectBinding,
    *,
    evidence_by_id: dict[str, EvidenceRecord],
    policy: CapacityCommitmentPolicy,
) -> tuple[ProjectGate, ...]:
    verified: list[ProjectGate] = []
    for observation in binding.gate_set.observations:
        observation.validate()
        _parse_iso_datetime(
            observation.effective_at or "",
            f"{binding.project_id}/{observation.gate.value} effective_at",
        )
        if observation.evidence_ids:
            expected_metric = (
                policy.land_control_metric
                if observation.gate is policy.core_inclusion_gate
                else None
            )
            _records_for_ids(
                evidence_by_id,
                observation.evidence_ids,
                expected_metric=expected_metric,
                segment_id=binding.segment_id,
                policy=policy,
            )
        if observation.verified:
            verified.append(observation.gate)
    return tuple(verified)


def _validate_baseline_status(
    binding: CapacityProjectBinding,
    *,
    evidence_by_id: dict[str, EvidenceRecord],
    policy: CapacityCommitmentPolicy,
) -> None:
    records = _records_for_ids(
        evidence_by_id,
        binding.baseline_inclusion_evidence_ids,
        expected_metric=policy.baseline_inclusion_metric,
        segment_id=binding.segment_id,
        policy=policy,
    )
    for record in records:
        if str(record.value).strip().lower() != binding.baseline_inclusion.value:
            raise ValueError(
                f"baseline-inclusion Evidence {record.id} does not match binding status"
            )


def _validate_disposition(
    binding: CapacityProjectBinding,
    *,
    evidence_by_id: dict[str, EvidenceRecord],
    policy: CapacityCommitmentPolicy,
) -> None:
    if binding.disposition is not CapacityProjectDisposition.CANCELLED:
        return
    records = _records_for_ids(
        evidence_by_id,
        binding.disposition_evidence_ids,
        expected_metric=policy.cancellation_metric,
        segment_id=binding.segment_id,
        policy=policy,
    )
    for record in records:
        if str(record.value).strip().lower() not in {"true", "cancelled"}:
            raise ValueError(
                f"cancellation Evidence {record.id} must state true/cancelled"
            )


def _numeric_evidence(
    binding: CapacityProjectBinding,
    *,
    evidence_by_id: dict[str, EvidenceRecord],
    evidence_ids: tuple[str, ...],
    expected_metric: str,
    policy: CapacityCommitmentPolicy,
) -> tuple[EvidenceRecord, ...]:
    records = _records_for_ids(
        evidence_by_id,
        evidence_ids,
        expected_metric=expected_metric,
        segment_id=binding.segment_id,
        policy=policy,
    )
    for record in records:
        _positive_decimal(record)
    return records


def _assess_project(
    binding: CapacityProjectBinding,
    *,
    evidence_by_id: dict[str, EvidenceRecord],
    policy: CapacityCommitmentPolicy,
) -> CapacityProjectAssessment:
    binding.validate(policy=policy)
    verified_gates = _validate_gate_evidence(
        binding,
        evidence_by_id=evidence_by_id,
        policy=policy,
    )
    _validate_baseline_status(
        binding,
        evidence_by_id=evidence_by_id,
        policy=policy,
    )
    _validate_disposition(
        binding,
        evidence_by_id=evidence_by_id,
        policy=policy,
    )

    capacity_records = _numeric_evidence(
        binding,
        evidence_by_id=evidence_by_id,
        evidence_ids=binding.committed_capacity_evidence_ids,
        expected_metric=policy.committed_capacity_metric,
        policy=policy,
    )
    site_records = _numeric_evidence(
        binding,
        evidence_by_id=evidence_by_id,
        evidence_ids=binding.site_area_evidence_ids,
        expected_metric=policy.site_area_metric,
        policy=policy,
    )
    capex_records = _numeric_evidence(
        binding,
        evidence_by_id=evidence_by_id,
        evidence_ids=binding.committed_capex_evidence_ids,
        expected_metric=policy.committed_capex_metric,
        policy=policy,
    )
    ramp_records = _records_for_ids(
        evidence_by_id,
        binding.ramp_date_evidence_ids,
        expected_metric=policy.ramp_date_metric,
        segment_id=binding.segment_id,
        policy=policy,
    )
    for record in ramp_records:
        _validate_ramp_date(record)
    equipment_records = _records_for_ids(
        evidence_by_id,
        binding.equipment_commitment_evidence_ids,
        expected_metric=policy.equipment_commitment_metric,
        segment_id=binding.segment_id,
        policy=policy,
    )

    land_observation = _gate_observation(
        binding.gate_set,
        policy.core_inclusion_gate,
    )
    land_control_verified = bool(
        land_observation is not None and land_observation.verified
    )
    land_control_resolved = bool(
        land_observation is not None and land_observation.evidence_ids
    )

    evidence_ids = tuple(
        dict.fromkeys(
            (
                *(
                    evidence_id
                    for observation in binding.gate_set.observations
                    for evidence_id in observation.evidence_ids
                ),
                *binding.baseline_inclusion_evidence_ids,
                *binding.disposition_evidence_ids,
                *binding.committed_capacity_evidence_ids,
                *binding.site_area_evidence_ids,
                *binding.committed_capex_evidence_ids,
                *binding.ramp_date_evidence_ids,
                *binding.equipment_commitment_evidence_ids,
            )
        )
    )

    if binding.disposition is CapacityProjectDisposition.CANCELLED:
        return CapacityProjectAssessment(
            project_id=binding.project_id,
            segment_id=binding.segment_id,
            verified_gates=verified_gates,
            land_control_verified=land_control_verified,
            baseline_inclusion=binding.baseline_inclusion,
            disposition=binding.disposition,
            core_inclusion_required=False,
            quantification_status=CapacityQuantificationStatus.NOT_REQUIRED,
            qualifying_evidence_ids=evidence_ids,
            recovery_required=False,
            rationale="official Evidence marks the capacity project cancelled",
        )

    if not land_control_resolved:
        return CapacityProjectAssessment(
            project_id=binding.project_id,
            segment_id=binding.segment_id,
            verified_gates=verified_gates,
            land_control_verified=False,
            baseline_inclusion=binding.baseline_inclusion,
            disposition=binding.disposition,
            core_inclusion_required=False,
            quantification_status=CapacityQuantificationStatus.NOT_REQUIRED,
            qualifying_evidence_ids=evidence_ids,
            recovery_required=True,
            rationale=(
                "LAND_CONTROL is unresolved; absence cannot be treated as no contract"
            ),
        )

    if not land_control_verified:
        return CapacityProjectAssessment(
            project_id=binding.project_id,
            segment_id=binding.segment_id,
            verified_gates=verified_gates,
            land_control_verified=False,
            baseline_inclusion=binding.baseline_inclusion,
            disposition=binding.disposition,
            core_inclusion_required=False,
            quantification_status=CapacityQuantificationStatus.NOT_REQUIRED,
            qualifying_evidence_ids=evidence_ids,
            recovery_required=False,
            rationale="official Evidence resolves LAND_CONTROL as not verified",
        )

    if binding.baseline_inclusion is BaselineInclusionStatus.UNKNOWN:
        return CapacityProjectAssessment(
            project_id=binding.project_id,
            segment_id=binding.segment_id,
            verified_gates=verified_gates,
            land_control_verified=True,
            baseline_inclusion=binding.baseline_inclusion,
            disposition=binding.disposition,
            core_inclusion_required=False,
            quantification_status=CapacityQuantificationStatus.NOT_REQUIRED,
            qualifying_evidence_ids=evidence_ids,
            recovery_required=True,
            rationale=(
                "LAND_CONTROL is verified but incremental-vs-baseline treatment is unknown"
            ),
        )

    if binding.baseline_inclusion is BaselineInclusionStatus.IN_BASELINE:
        return CapacityProjectAssessment(
            project_id=binding.project_id,
            segment_id=binding.segment_id,
            verified_gates=verified_gates,
            land_control_verified=True,
            baseline_inclusion=binding.baseline_inclusion,
            disposition=binding.disposition,
            core_inclusion_required=False,
            quantification_status=CapacityQuantificationStatus.NOT_REQUIRED,
            qualifying_evidence_ids=evidence_ids,
            recovery_required=False,
            rationale=(
                "committed capacity is already reflected in the baseline and cannot be added again"
            ),
        )

    if capacity_records:
        quantification = CapacityQuantificationStatus.DISCLOSED
        recovery_required = False
        rationale = (
            "LAND_CONTROL is verified, capacity is incremental to baseline, and positive "
            "committed capacity is disclosed"
        )
    elif site_records or capex_records:
        quantification = CapacityQuantificationStatus.BOUNDED_INPUTS_AVAILABLE
        recovery_required = False
        rationale = (
            "LAND_CONTROL is verified and incremental; exact capacity is absent but "
            "site/CAPEX Evidence requires a bounded Core path"
        )
    else:
        quantification = CapacityQuantificationStatus.UNQUANTIFIED
        recovery_required = True
        rationale = (
            "LAND_CONTROL is verified and incremental, but no disclosed capacity, site "
            "area or committed CAPEX exists; zero expansion is forbidden"
        )

    return CapacityProjectAssessment(
        project_id=binding.project_id,
        segment_id=binding.segment_id,
        verified_gates=verified_gates,
        land_control_verified=True,
        baseline_inclusion=binding.baseline_inclusion,
        disposition=binding.disposition,
        core_inclusion_required=True,
        quantification_status=quantification,
        qualifying_evidence_ids=evidence_ids,
        recovery_required=recovery_required,
        rationale=rationale,
    )


def _validate_no_active_expansion(
    segment: CapacitySegmentCommitmentInput,
    *,
    evidence_by_id: dict[str, EvidenceRecord],
    policy: CapacityCommitmentPolicy,
) -> tuple[str, ...]:
    records = _records_for_ids(
        evidence_by_id,
        segment.no_active_expansion_evidence_ids,
        expected_metric=policy.no_active_expansion_metric,
        segment_id=segment.segment_id,
        policy=policy,
    )
    for record in records:
        if record.value is not True:
            raise ValueError(
                f"no-active-expansion Evidence {record.id} must be boolean true"
            )
    return tuple(item.id for item in records)


def _stable_hash(
    assessments: tuple[CapacitySegmentAssessment, ...],
    evidence_by_id: dict[str, EvidenceRecord],
    policy: CapacityCommitmentPolicy,
) -> str:
    evidence_ids = tuple(
        sorted(
            {
                evidence_id
                for segment in assessments
                for evidence_id in (
                    *segment.no_active_expansion_evidence_ids,
                    *(
                        evidence_id
                        for project in segment.projects
                        for evidence_id in project.qualifying_evidence_ids
                    ),
                )
            }
        )
    )
    payload = {
        "contract": "capacity_commitment_assessment/v2",
        "policy": {
            "core_inclusion_gate": policy.core_inclusion_gate.value,
            "capacity_archetype": policy.capacity_archetype,
            "metrics": {
                "land_control": policy.land_control_metric,
                "committed_capacity": policy.committed_capacity_metric,
                "site_area": policy.site_area_metric,
                "committed_capex": policy.committed_capex_metric,
                "ramp_date": policy.ramp_date_metric,
                "equipment_commitment": policy.equipment_commitment_metric,
                "baseline_inclusion": policy.baseline_inclusion_metric,
                "cancellation": policy.cancellation_metric,
                "no_active_expansion": policy.no_active_expansion_metric,
            },
            "eligible_source_layers": tuple(
                item.value for item in policy.eligible_source_layers
            ),
        },
        "segments": [
            {
                "segment_id": segment.segment_id,
                "no_active_expansion_verified": segment.no_active_expansion_verified,
                "no_active_expansion_evidence_ids": (
                    segment.no_active_expansion_evidence_ids
                ),
                "recovery_required": segment.recovery_required,
                "projects": [
                    {
                        "project_id": project.project_id,
                        "verified_gates": tuple(
                            item.value for item in project.verified_gates
                        ),
                        "land_control_verified": project.land_control_verified,
                        "baseline_inclusion": project.baseline_inclusion.value,
                        "disposition": project.disposition.value,
                        "core_inclusion_required": project.core_inclusion_required,
                        "quantification_status": project.quantification_status.value,
                        "qualifying_evidence_ids": project.qualifying_evidence_ids,
                        "recovery_required": project.recovery_required,
                    }
                    for project in segment.projects
                ],
            }
            for segment in assessments
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
    inputs: CapacityCommitmentInput,
    policy: CapacityCommitmentPolicy | None = None,
) -> CapacityCommitmentAssessment:
    effective_policy = policy or CapacityCommitmentPolicy()
    effective_policy.validate()
    plan.validate()
    inputs.validate(policy=effective_policy)

    capacity_segment_ids = tuple(
        segment.segment_id
        for segment in plan.segments
        if effective_policy.capacity_archetype in segment.archetypes
    )
    supplied_ids = tuple(item.segment_id for item in inputs.segments)
    if set(supplied_ids) != set(capacity_segment_ids):
        raise ValueError(
            "capacity commitment input coverage mismatch: "
            f"expected={sorted(capacity_segment_ids)}, got={sorted(supplied_ids)}"
        )

    active = ledger.active()
    evidence_by_id = {item.id: item for item in active}
    assessments: list[CapacitySegmentAssessment] = []
    input_by_segment = {item.segment_id: item for item in inputs.segments}

    for segment_id in capacity_segment_ids:
        segment_input = input_by_segment[segment_id]
        if not segment_input.projects:
            evidence_ids = _validate_no_active_expansion(
                segment_input,
                evidence_by_id=evidence_by_id,
                policy=effective_policy,
            )
            assessments.append(
                CapacitySegmentAssessment(
                    segment_id=segment_id,
                    projects=(),
                    no_active_expansion_verified=True,
                    no_active_expansion_evidence_ids=evidence_ids,
                    recovery_required=False,
                    rationale="official Evidence confirms no active capacity expansion",
                )
            )
            continue

        projects = tuple(
            _assess_project(
                project,
                evidence_by_id=evidence_by_id,
                policy=effective_policy,
            )
            for project in segment_input.projects
        )
        assessments.append(
            CapacitySegmentAssessment(
                segment_id=segment_id,
                projects=projects,
                no_active_expansion_verified=False,
                no_active_expansion_evidence_ids=(),
                recovery_required=any(item.recovery_required for item in projects),
                rationale=(
                    "capacity projects were classified through canonical independent "
                    "ProjectGate evidence"
                ),
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
    loader: CapacityCommitmentLoader | None,
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

        capacity_segments = tuple(
            segment.segment_id
            for segment in plan.segments
            if effective_policy.capacity_archetype in segment.archetypes
        )
        if not capacity_segments:
            empty = CapacityCommitmentAssessment(
                segments=(),
                assessment_hash=sha256(
                    b"capacity_commitment_assessment/v2:not_applicable"
                ).hexdigest(),
            )
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "no capacity_manufacturing segment requires Capacity Commitment Gate",
                {
                    "capacity_commitment_assessment": empty,
                    "capacity_commitment_assessment_hash": empty.assessment_hash,
                    "core_capacity_inclusion_required_segments": (),
                    "core_capacity_inclusion_required_projects": (),
                    "capacity_commitment_recovery_segments": (),
                },
            )
        if loader is None:
            return StageExecutionResult(
                StageStatus.NOT_IMPLEMENTED,
                "capacity_manufacturing route requires a typed CapacityCommitmentLoader",
                blocking=True,
            )

        try:
            inputs = loader(context)
            if not isinstance(inputs, CapacityCommitmentInput):
                raise TypeError(
                    "CapacityCommitmentLoader must return CapacityCommitmentInput"
                )
            assessment = assess_capacity_commitment(
                plan=plan,
                ledger=ledger,
                inputs=inputs,
                policy=effective_policy,
            )
        except (PermissionError, TypeError, ValueError) as exc:
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
            "core_capacity_inclusion_required_projects": (
                assessment.core_inclusion_required_projects
            ),
            "capacity_commitment_recovery_segments": (
                assessment.recovery_required_segments
            ),
        }
        if assessment.recovery_required_segments:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "capacity commitment requires Evidence recovery or bounded quantification for: "
                + ", ".join(assessment.recovery_required_segments),
                outputs,
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "canonical project gates were classified and Core capacity obligations frozen",
            outputs,
        )

    return run
