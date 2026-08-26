from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from typing import Callable

from .actual_units import Dimension, unit_def
from .capacity_commitment import CapacityCommitmentAssessment
from .control_plane import StageStatus
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .records import BridgeRecord, Direction


class CapacityBridgeRole(str, Enum):
    CAPACITY = "capacity"
    CAPEX = "capex"
    RAMP = "ramp"


_REQUIRED_CORE_ROLES = (
    CapacityBridgeRole.CAPACITY,
    CapacityBridgeRole.CAPEX,
    CapacityBridgeRole.RAMP,
)


@dataclass(frozen=True)
class CapacityBridgeBinding:
    project_id: str
    role: CapacityBridgeRole
    bridge_id: str
    required_evidence_ids: tuple[str, ...]
    project_economic_path_id: str

    def validate(self) -> None:
        if not all((self.project_id, self.bridge_id, self.project_economic_path_id)):
            raise ValueError(
                "capacity bridge binding requires project, bridge and project-economic-path IDs"
            )
        if not self.required_evidence_ids:
            raise ValueError("capacity bridge binding requires Evidence IDs")
        if len(self.required_evidence_ids) != len(set(self.required_evidence_ids)):
            raise ValueError("capacity bridge binding has duplicate Evidence IDs")
        if any(character.isspace() for character in self.project_economic_path_id):
            raise ValueError("project_economic_path_id cannot contain whitespace")

    @property
    def role_economic_path_id(self) -> str:
        return f"{self.project_economic_path_id}:{self.role.value}"


@dataclass(frozen=True)
class CapacityBridgeConsumptionContract:
    assessment_hash: str
    bindings: tuple[CapacityBridgeBinding, ...]

    def validate(self) -> None:
        if not self.assessment_hash:
            raise ValueError("capacity bridge contract requires assessment_hash")
        for binding in self.bindings:
            binding.validate()
        identities = tuple((item.project_id, item.role) for item in self.bindings)
        if len(identities) != len(set(identities)):
            raise ValueError("capacity bridge contract has duplicate project/role bindings")
        bridge_ids = tuple(item.bridge_id for item in self.bindings)
        if len(bridge_ids) != len(set(bridge_ids)):
            raise ValueError(
                "capacity bridge roles require distinct bridge IDs for explicit audit"
            )
        roots_by_project: dict[str, set[str]] = {}
        for item in self.bindings:
            roots_by_project.setdefault(item.project_id, set()).add(
                item.project_economic_path_id
            )
        inconsistent = tuple(
            sorted(project_id for project_id, roots in roots_by_project.items() if len(roots) != 1)
        )
        if inconsistent:
            raise ValueError(
                "capacity bridge roles for one project must share one project-economic-path root: "
                + ", ".join(inconsistent)
            )
        roots = tuple(next(iter(roots)) for roots in roots_by_project.values())
        if len(roots) != len(set(roots)):
            raise ValueError(
                "different capacity projects require distinct project-economic-path roots"
            )


@dataclass(frozen=True)
class CapacityBridgeConsumptionResult:
    assessment_hash: str
    consumed_project_ids: tuple[str, ...]
    project_economic_paths: tuple[tuple[str, str], ...]
    role_bindings: tuple[tuple[str, str, str, str, str], ...]
    bridge_ids: tuple[str, ...]
    consumption_hash: str

    def __post_init__(self) -> None:
        if not self.assessment_hash or not self.consumption_hash:
            raise ValueError("capacity bridge consumption result requires hashes")
        project_ids = tuple(item[0] for item in self.project_economic_paths)
        if len(project_ids) != len(set(project_ids)):
            raise ValueError("capacity bridge consumption has duplicate project paths")
        role_identities = tuple((item[0], item[2]) for item in self.role_bindings)
        if len(role_identities) != len(set(role_identities)):
            raise ValueError("capacity bridge consumption has duplicate project/role rows")


CapacityBridgeConsumptionLoader = Callable[
    [OrchestratorContext], CapacityBridgeConsumptionContract
]


def _project_map(assessment: CapacityCommitmentAssessment):
    return {
        project.project_id: project
        for segment in assessment.segments
        for project in segment.projects
    }


def _validate_bridge_role(
    bridge: BridgeRecord,
    *,
    role: CapacityBridgeRole,
) -> None:
    if not isfinite(bridge.old_value) or not isfinite(bridge.new_value):
        raise ValueError(f"capacity bridge {bridge.id} values must be finite")
    try:
        dimension = unit_def(bridge.unit).dimension
    except ValueError as exc:
        raise ValueError(
            f"capacity bridge {bridge.id} has unsupported unit {bridge.unit}"
        ) from exc

    if role is CapacityBridgeRole.CAPACITY:
        if bridge.direction is not Direction.UP or not bridge.new_value > bridge.old_value:
            raise ValueError(
                f"Core capacity bridge {bridge.id} must increase capacity"
            )
        if dimension not in {
            Dimension.POWER,
            Dimension.COUNT,
            Dimension.MASS,
            Dimension.MONEY,
        }:
            raise ValueError(
                f"Core capacity bridge {bridge.id} requires an operating-capacity unit"
            )
        return

    if role is CapacityBridgeRole.CAPEX:
        if bridge.direction is not Direction.UP or bridge.new_value <= 0:
            raise ValueError(
                f"Core CAPEX bridge {bridge.id} must carry positive expansion CAPEX"
            )
        if dimension is not Dimension.MONEY:
            raise ValueError(
                f"Core CAPEX bridge {bridge.id} requires a money unit"
            )
        return

    if bridge.new_value < 0:
        raise ValueError(
            f"Core ramp bridge {bridge.id} cannot carry a negative ramp input"
        )
    if dimension not in {Dimension.TIME, Dimension.RATIO}:
        raise ValueError(
            f"Core ramp bridge {bridge.id} requires a time or ratio unit"
        )


def validate_capacity_bridge_consumption(
    *,
    assessment: CapacityCommitmentAssessment,
    bridges: tuple[BridgeRecord, ...],
    contract: CapacityBridgeConsumptionContract,
) -> CapacityBridgeConsumptionResult:
    contract.validate()
    if contract.assessment_hash != assessment.assessment_hash:
        raise ValueError(
            "capacity bridge contract assessment_hash does not match frozen assessment"
        )
    if assessment.recovery_required_segments:
        raise ValueError(
            "capacity bridge consumption cannot run while commitment recovery is unresolved"
        )

    projects = _project_map(assessment)
    required_projects = {
        project_id: project
        for project_id, project in projects.items()
        if project.core_inclusion_required
    }
    bridge_map = {item.id: item for item in bridges}
    if len(bridge_map) != len(bridges):
        raise ValueError("capacity bridge consumption received duplicate bridge IDs")

    bindings_by_project: dict[
        str, dict[CapacityBridgeRole, CapacityBridgeBinding]
    ] = {}
    for binding in contract.bindings:
        project = required_projects.get(binding.project_id)
        if project is None:
            raise ValueError(
                f"capacity bridge binding targets non-Core/unknown project {binding.project_id}"
            )
        unknown_evidence = tuple(
            sorted(
                set(binding.required_evidence_ids)
                - set(project.qualifying_evidence_ids)
            )
        )
        if unknown_evidence:
            raise ValueError(
                f"capacity bridge {binding.bridge_id} requires Evidence outside project "
                f"assessment: {', '.join(unknown_evidence)}"
            )
        bindings_by_project.setdefault(binding.project_id, {})[
            binding.role
        ] = binding

    expected_project_ids = set(required_projects)
    if set(bindings_by_project) != expected_project_ids:
        missing = tuple(sorted(expected_project_ids - set(bindings_by_project)))
        extra = tuple(sorted(set(bindings_by_project) - expected_project_ids))
        raise ValueError(
            "capacity bridge project coverage mismatch: "
            f"missing={list(missing)}, extra={list(extra)}"
        )

    project_paths: list[tuple[str, str]] = []
    role_rows: list[tuple[str, str, str, str, str]] = []
    consumed_bridges: list[str] = []
    used_roots: set[str] = set()
    for project_id in sorted(required_projects):
        project = required_projects[project_id]
        role_map = bindings_by_project[project_id]
        missing_roles = tuple(
            role.value for role in _REQUIRED_CORE_ROLES if role not in role_map
        )
        if missing_roles:
            raise ValueError(
                f"Core capacity project {project_id} is missing bridge roles: "
                + ", ".join(missing_roles)
            )

        roots = {item.project_economic_path_id for item in role_map.values()}
        if len(roots) != 1:
            raise ValueError(
                f"capacity, CAPEX and ramp bridges for {project_id} must share one "
                "project economic-path root"
            )
        root = next(iter(roots))
        if root in used_roots:
            raise ValueError(
                f"capacity project economic-path root is reused across projects: {root}"
            )
        used_roots.add(root)
        project_paths.append((project_id, root))

        for role in _REQUIRED_CORE_ROLES:
            binding = role_map[role]
            try:
                bridge = bridge_map[binding.bridge_id]
            except KeyError as exc:
                raise ValueError(
                    f"capacity contract references unknown bridge {binding.bridge_id}"
                ) from exc
            missing_evidence = tuple(
                sorted(
                    set(binding.required_evidence_ids)
                    - set(bridge.evidence_ids)
                )
            )
            if missing_evidence:
                raise ValueError(
                    f"capacity bridge {bridge.id} omits required Evidence: "
                    + ", ".join(missing_evidence)
                )
            expected_path = binding.role_economic_path_id
            if bridge.economic_path_id != expected_path:
                raise ValueError(
                    f"capacity bridge {bridge.id} economic_path_id must be {expected_path}"
                )
            _validate_bridge_role(bridge, role=role)
            consumed_bridges.append(bridge.id)
            role_rows.append(
                (
                    project_id,
                    project.segment_id,
                    role.value,
                    bridge.id,
                    expected_path,
                )
            )

    payload = {
        "contract": "capacity_bridge_consumption/v2",
        "assessment_hash": assessment.assessment_hash,
        "project_economic_paths": project_paths,
        "role_bindings": role_rows,
        "bindings": [
            {
                "project_id": item.project_id,
                "role": item.role.value,
                "bridge_id": item.bridge_id,
                "required_evidence_ids": item.required_evidence_ids,
                "project_economic_path_id": item.project_economic_path_id,
            }
            for item in sorted(
                contract.bindings,
                key=lambda item: (item.project_id, item.role.value),
            )
        ],
        "bridges": [
            {
                "id": bridge_map[bridge_id].id,
                "evidence_ids": bridge_map[bridge_id].evidence_ids,
                "economic_path_id": bridge_map[bridge_id].economic_path_id,
                "old_value": bridge_map[bridge_id].old_value,
                "new_value": bridge_map[bridge_id].new_value,
                "unit": bridge_map[bridge_id].unit,
                "direction": bridge_map[bridge_id].direction.value,
            }
            for bridge_id in sorted(consumed_bridges)
        ],
    }
    consumption_hash = sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return CapacityBridgeConsumptionResult(
        assessment_hash=assessment.assessment_hash,
        consumed_project_ids=tuple(sorted(required_projects)),
        project_economic_paths=tuple(project_paths),
        role_bindings=tuple(role_rows),
        bridge_ids=tuple(sorted(consumed_bridges)),
        consumption_hash=consumption_hash,
    )


def capacity_bridge_consumption_gate_adapter(
    *,
    loader: CapacityBridgeConsumptionLoader | None,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        assessment = context.data.get("capacity_commitment_assessment")
        if not isinstance(assessment, CapacityCommitmentAssessment):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "CapacityCommitmentAssessment is required before bridge consumption",
                blocking=True,
            )
        if not assessment.core_inclusion_required_projects:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "no Core-inclusion capacity project requires bridge consumption",
                {
                    "capacity_bridge_consumption_required": False,
                },
            )
        if loader is None:
            return StageExecutionResult(
                StageStatus.NOT_IMPLEMENTED,
                "Core-inclusion capacity projects require a typed bridge-consumption loader",
                blocking=True,
            )
        bridges = context.data.get("bridges")
        if not isinstance(bridges, tuple) or not all(
            isinstance(item, BridgeRecord) for item in bridges
        ):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "typed BridgeRecord tuple is required before capacity bridge consumption",
                blocking=True,
            )
        try:
            contract = loader(context)
            if not isinstance(contract, CapacityBridgeConsumptionContract):
                raise TypeError(
                    "capacity bridge loader must return CapacityBridgeConsumptionContract"
                )
            result = validate_capacity_bridge_consumption(
                assessment=assessment,
                bridges=bridges,
                contract=contract,
            )
        except (TypeError, ValueError) as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"Capacity bridge consumption failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "every Core-inclusion capacity project consumed explicit capacity, CAPEX and ramp bridge paths",
            {
                "capacity_bridge_consumption_required": True,
                "capacity_bridge_consumption_result": result,
                "capacity_bridge_consumption_hash": result.consumption_hash,
            },
        )

    return run
