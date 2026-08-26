from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from .assumption_compiler import CompiledAssumptionSet
from .capacity_commitment import (
    BaselineInclusionStatus,
    CapacityCommitmentAssessment,
    CapacityProjectDisposition,
)
from .capacity_consumption import CapacityBridgeConsumptionResult
from .control_plane import StageStatus
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .scenario_binding import BoundScenarioSet
from .valuation_execution import GenericValuationResult


def _stable_hash(payload: dict) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _core_projects(assessment: CapacityCommitmentAssessment):
    return tuple(
        project
        for segment in assessment.segments
        for project in segment.projects
        if project.core_inclusion_required
    )


@dataclass(frozen=True)
class CapacityScenarioBindingResult:
    assessment_hash: str
    consumption_hash: str
    core_scenario_id: str
    bridge_ids: tuple[str, ...]
    project_economic_paths: tuple[tuple[str, str], ...]
    binding_hash: str


@dataclass(frozen=True)
class CapacityValuationBindingResult:
    assessment_hash: str
    scenario_binding_hash: str
    valuation_hash: str
    core_scenario_id: str
    consumed_project_paths: tuple[str, ...]
    binding_hash: str


@dataclass(frozen=True)
class CapacityPERBindingResult:
    assessment_hash: str
    per_snapshot_hash: str
    expansion_evidence_ids: tuple[str, ...]
    binding_hash: str


@dataclass(frozen=True)
class CapacityAuditResult:
    assessment_hash: str
    checks: tuple[tuple[str, bool], ...]
    scenario_binding_hash: str | None
    valuation_binding_hash: str | None
    per_binding_hash: str | None
    audit_hash: str

    @property
    def passed(self) -> bool:
        return all(passed for _, passed in self.checks)


def validate_capacity_scenario_binding(
    *,
    assessment: CapacityCommitmentAssessment,
    consumption: CapacityBridgeConsumptionResult,
    compiled: CompiledAssumptionSet,
    scenario_set: BoundScenarioSet,
) -> CapacityScenarioBindingResult:
    if consumption.assessment_hash != assessment.assessment_hash:
        raise ValueError("capacity consumption assessment hash is stale")
    required_projects = set(assessment.core_inclusion_required_projects)
    if set(consumption.consumed_project_ids) != required_projects:
        raise ValueError("capacity consumption project coverage drifted")

    matched = []
    for bridge_id in consumption.bridge_ids:
        assumptions = tuple(
            item for item in compiled.assumptions if item.bridge_id == bridge_id
        )
        if len(assumptions) != 1:
            raise ValueError(
                f"capacity bridge {bridge_id} must compile exactly once, got {len(assumptions)}"
            )
        matched.append(assumptions[0])
    scenario_ids = {item.scenario_id for item in matched}
    if len(scenario_ids) != 1:
        raise ValueError("capacity assumptions must bind to one Core scenario")
    core_scenario_id = next(iter(scenario_ids))
    try:
        bound = scenario_set.get(core_scenario_id)
    except KeyError as exc:
        raise ValueError("capacity Core scenario is absent from BoundScenarioSet") from exc
    bound_bridge_ids = {item.bridge_id for item in bound.assumptions}
    missing = tuple(sorted(set(consumption.bridge_ids) - bound_bridge_ids))
    if missing:
        raise ValueError(
            "BoundScenarioSet omitted capacity bridges: " + ", ".join(missing)
        )
    allowed_paths = {path for _, path in consumption.project_economic_paths}
    assumption_paths = {item.economic_path_id for item in matched}
    outside = tuple(sorted(assumption_paths - allowed_paths))
    if outside:
        raise ValueError(
            "capacity assumptions changed project economic paths: "
            + ", ".join(outside)
        )
    missing_paths = tuple(sorted(allowed_paths - assumption_paths))
    if missing_paths:
        raise ValueError(
            "Core scenario omitted capacity project paths: "
            + ", ".join(missing_paths)
        )
    payload = {
        "contract": "capacity_scenario_binding/v1",
        "assessment_hash": assessment.assessment_hash,
        "consumption_hash": consumption.consumption_hash,
        "assumption_set_hash": compiled.assumption_set_hash,
        "scenario_set_hash": scenario_set.scenario_set_hash,
        "core_scenario_id": core_scenario_id,
        "bridge_ids": sorted(consumption.bridge_ids),
        "project_economic_paths": sorted(consumption.project_economic_paths),
    }
    return CapacityScenarioBindingResult(
        assessment_hash=assessment.assessment_hash,
        consumption_hash=consumption.consumption_hash,
        core_scenario_id=core_scenario_id,
        bridge_ids=tuple(sorted(consumption.bridge_ids)),
        project_economic_paths=tuple(sorted(consumption.project_economic_paths)),
        binding_hash=_stable_hash(payload),
    )


def capacity_scenario_binding_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        assessment = context.data.get("capacity_commitment_assessment")
        if not isinstance(assessment, CapacityCommitmentAssessment):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "CapacityCommitmentAssessment missing before Scenario binding",
                blocking=True,
            )
        if not assessment.core_inclusion_required_projects:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "no Core capacity project requires Scenario binding",
                {"capacity_scenario_binding_required": False},
            )
        consumption = context.data.get("capacity_bridge_consumption_result")
        compiled = context.data.get("compiled_assumption_set")
        scenario_set = context.data.get("bound_scenario_set")
        if not isinstance(consumption, CapacityBridgeConsumptionResult):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "CapacityBridgeConsumptionResult missing before Scenario binding",
                blocking=True,
            )
        if not isinstance(compiled, CompiledAssumptionSet):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "CompiledAssumptionSet missing before capacity Scenario binding",
                blocking=True,
            )
        if not isinstance(scenario_set, BoundScenarioSet):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "BoundScenarioSet missing before capacity Scenario binding",
                blocking=True,
            )
        try:
            result = validate_capacity_scenario_binding(
                assessment=assessment,
                consumption=consumption,
                compiled=compiled,
                scenario_set=scenario_set,
            )
        except (TypeError, ValueError) as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"capacity Scenario binding failed: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "Core capacity assumptions were compiled and retained in one bound scenario",
            {
                "capacity_scenario_binding_required": True,
                "capacity_scenario_binding_result": result,
                "capacity_scenario_binding_hash": result.binding_hash,
            },
        )

    return run


def validate_capacity_valuation_binding(
    *,
    assessment: CapacityCommitmentAssessment,
    scenario_binding: CapacityScenarioBindingResult,
    valuation: GenericValuationResult,
) -> CapacityValuationBindingResult:
    if scenario_binding.assessment_hash != assessment.assessment_hash:
        raise ValueError("capacity Scenario binding assessment hash is stale")
    scenario = next(
        (
            item
            for item in valuation.scenarios
            if item.scenario_id == scenario_binding.core_scenario_id
        ),
        None,
    )
    if scenario is None:
        raise ValueError("valuation omitted the capacity Core scenario")
    required_paths = {
        path for _, path in scenario_binding.project_economic_paths
    }
    consumed = set(scenario.economic_path_ids)
    missing = tuple(sorted(required_paths - consumed))
    if missing:
        raise ValueError(
            "deterministic valuation omitted capacity economic paths: "
            + ", ".join(missing)
        )
    payload = {
        "contract": "capacity_valuation_binding/v1",
        "assessment_hash": assessment.assessment_hash,
        "scenario_binding_hash": scenario_binding.binding_hash,
        "valuation_hash": valuation.valuation_hash,
        "core_scenario_id": scenario_binding.core_scenario_id,
        "project_paths": sorted(required_paths),
    }
    return CapacityValuationBindingResult(
        assessment_hash=assessment.assessment_hash,
        scenario_binding_hash=scenario_binding.binding_hash,
        valuation_hash=valuation.valuation_hash,
        core_scenario_id=scenario_binding.core_scenario_id,
        consumed_project_paths=tuple(sorted(required_paths)),
        binding_hash=_stable_hash(payload),
    )


def capacity_valuation_binding_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        assessment = context.data.get("capacity_commitment_assessment")
        if not isinstance(assessment, CapacityCommitmentAssessment):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "CapacityCommitmentAssessment missing before valuation binding",
                blocking=True,
            )
        if not assessment.core_inclusion_required_projects:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "no Core capacity project requires valuation binding",
                {"capacity_valuation_binding_required": False},
            )
        scenario_binding = context.data.get("capacity_scenario_binding_result")
        valuation = context.data.get("generic_valuation_result")
        if not isinstance(scenario_binding, CapacityScenarioBindingResult):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "CapacityScenarioBindingResult missing before valuation binding",
                blocking=True,
            )
        if not isinstance(valuation, GenericValuationResult):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "GenericValuationResult missing before capacity valuation binding",
                blocking=True,
            )
        try:
            result = validate_capacity_valuation_binding(
                assessment=assessment,
                scenario_binding=scenario_binding,
                valuation=valuation,
            )
        except (TypeError, ValueError) as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"capacity valuation binding failed: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "deterministic valuation consumed every Core capacity project path",
            {
                "capacity_valuation_binding_required": True,
                "capacity_valuation_binding_result": result,
                "capacity_valuation_binding_hash": result.binding_hash,
            },
        )

    return run


def capacity_per_binding_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        assessment = context.data.get("capacity_commitment_assessment")
        if not isinstance(assessment, CapacityCommitmentAssessment):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "CapacityCommitmentAssessment missing before PER binding",
                blocking=True,
            )
        if not assessment.core_inclusion_required_projects:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "no Core capacity project requires PER binding",
                {"capacity_per_binding_required": False},
            )
        if not bool(context.data.get("warranted_per_applicable", False)):
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "Warranted PER is not applicable to the selected capacity method",
                {"capacity_per_binding_required": False},
            )
        per_result = context.data.get("live_warranted_per_result")
        per_snapshot_hash = context.data.get("per_snapshot_hash")
        if per_result is None or not isinstance(per_snapshot_hash, str) or not per_snapshot_hash:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "live PER result/hash missing before capacity PER binding",
                blocking=True,
            )
        expansion_ids = tuple(getattr(per_result, "expansion_evidence_ids", ()) or ())
        core_ids = {
            evidence_id
            for project in _core_projects(assessment)
            for evidence_id in project.qualifying_evidence_ids
        }
        reused = tuple(sorted(core_ids.intersection(expansion_ids)))
        if reused:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "Core capacity Evidence cannot be reused to open Expansion PER: "
                + ", ".join(reused),
                blocking=True,
            )
        payload = {
            "contract": "capacity_per_binding/v1",
            "assessment_hash": assessment.assessment_hash,
            "per_snapshot_hash": per_snapshot_hash,
            "expansion_evidence_ids": sorted(expansion_ids),
        }
        result = CapacityPERBindingResult(
            assessment_hash=assessment.assessment_hash,
            per_snapshot_hash=per_snapshot_hash,
            expansion_evidence_ids=tuple(sorted(expansion_ids)),
            binding_hash=_stable_hash(payload),
        )
        return StageExecutionResult(
            StageStatus.PASS,
            "PER uses the frozen capacity assessment and separate Expansion Evidence",
            {
                "capacity_per_binding_required": True,
                "capacity_per_binding_result": result,
                "capacity_per_binding_hash": result.binding_hash,
            },
        )

    return run


def capacity_audit_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        assessment = context.data.get("capacity_commitment_assessment")
        if not isinstance(assessment, CapacityCommitmentAssessment):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "capacity omission audit requires CapacityCommitmentAssessment",
                blocking=True,
            )
        projects = _core_projects(assessment)
        if not projects:
            payload = {
                "contract": "capacity_audit/v1",
                "assessment_hash": assessment.assessment_hash,
                "applicable": False,
            }
            result = CapacityAuditResult(
                assessment_hash=assessment.assessment_hash,
                checks=(("capacity_not_applicable_or_no_incremental_project", True),),
                scenario_binding_hash=None,
                valuation_binding_hash=None,
                per_binding_hash=None,
                audit_hash=_stable_hash(payload),
            )
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "no incremental land-controlled capacity project requires audit",
                {
                    "capacity_audit_result": result,
                    "capacity_audit_hash": result.audit_hash,
                },
            )
        consumption = context.data.get("capacity_bridge_consumption_result")
        scenario = context.data.get("capacity_scenario_binding_result")
        valuation = context.data.get("capacity_valuation_binding_result")
        per = context.data.get("capacity_per_binding_result")
        checks = (
            (
                "material_capacity_evidence_consumed",
                isinstance(consumption, CapacityBridgeConsumptionResult),
            ),
            (
                "capacity_commitment_hash_binding",
                isinstance(scenario, CapacityScenarioBindingResult)
                and scenario.assessment_hash == assessment.assessment_hash,
            ),
            (
                "core_capacity_floor_respected",
                isinstance(valuation, CapacityValuationBindingResult)
                and valuation.assessment_hash == assessment.assessment_hash,
            ),
            (
                "baseline_capacity_not_double_counted",
                all(
                    project.baseline_inclusion
                    is BaselineInclusionStatus.NOT_IN_BASELINE
                    for project in projects
                ),
            ),
            (
                "cancelled_capacity_not_included",
                all(
                    project.disposition is CapacityProjectDisposition.ACTIVE
                    for project in projects
                ),
            ),
            (
                "dcf_per_capacity_consistency",
                not bool(context.data.get("warranted_per_applicable", False))
                or isinstance(per, CapacityPERBindingResult),
            ),
            (
                "capacity_double_count",
                isinstance(scenario, CapacityScenarioBindingResult)
                and len({path for _, path in scenario.project_economic_paths})
                == len(scenario.project_economic_paths),
            ),
        )
        failed = tuple(name for name, passed in checks if not passed)
        if failed:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "capacity audit failed: " + ", ".join(failed),
                {"capacity_audit_checks": checks},
                blocking=True,
            )
        payload = {
            "contract": "capacity_audit/v1",
            "assessment_hash": assessment.assessment_hash,
            "checks": checks,
            "consumption_hash": consumption.consumption_hash,
            "scenario_binding_hash": scenario.binding_hash,
            "valuation_binding_hash": valuation.binding_hash,
            "per_binding_hash": per.binding_hash if isinstance(per, CapacityPERBindingResult) else None,
        }
        result = CapacityAuditResult(
            assessment_hash=assessment.assessment_hash,
            checks=checks,
            scenario_binding_hash=scenario.binding_hash,
            valuation_binding_hash=valuation.binding_hash,
            per_binding_hash=(
                per.binding_hash if isinstance(per, CapacityPERBindingResult) else None
            ),
            audit_hash=_stable_hash(payload),
        )
        return StageExecutionResult(
            StageStatus.PASS,
            "capacity omission, baseline and cross-method double-count checks passed",
            {
                "capacity_audit_result": result,
                "capacity_audit_checks": checks,
                "capacity_audit_hash": result.audit_hash,
            },
        )

    return run
