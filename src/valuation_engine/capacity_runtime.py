from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from .assumption_compiler import CompiledAssumptionSet
from .capacity_commitment import (
    BaselineInclusionStatus,
    CapacityCommitmentAssessment,
)
from .capacity_consumption import CapacityBridgeConsumptionResult
from .control_plane import StageStatus
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .per_adapters import LiveWarrantedPERStageResult
from .records import AuditFinding, AuditReport
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


@dataclass(frozen=True)
class CapacityScenarioBindingResult:
    assessment_hash: str
    consumption_hash: str
    core_scenario_id: str | None
    required_role_paths: tuple[str, ...]
    compiled_assumption_keys: tuple[str, ...]
    binding_hash: str

    def __post_init__(self) -> None:
        if not self.assessment_hash or not self.consumption_hash or not self.binding_hash:
            raise ValueError("capacity scenario binding requires component hashes")
        if self.required_role_paths and not self.core_scenario_id:
            raise ValueError("capacity scenario binding with role paths requires core_scenario_id")


@dataclass(frozen=True)
class CapacityValuationBindingResult:
    assessment_hash: str
    scenario_binding_hash: str
    valuation_hash: str
    core_scenario_id: str | None
    required_role_paths: tuple[str, ...]
    consumed_role_paths: tuple[str, ...]
    binding_hash: str

    def __post_init__(self) -> None:
        if not all(
            (
                self.assessment_hash,
                self.scenario_binding_hash,
                self.valuation_hash,
                self.binding_hash,
            )
        ):
            raise ValueError("capacity valuation binding requires component hashes")


@dataclass(frozen=True)
class CapacityPERBindingResult:
    assessment_hash: str
    valuation_binding_hash: str
    applicable: bool
    per_snapshot_hash: str | None
    expansion_evidence_ids: tuple[str, ...]
    core_evidence_overlap: tuple[str, ...]
    binding_hash: str

    def __post_init__(self) -> None:
        if not self.assessment_hash or not self.valuation_binding_hash or not self.binding_hash:
            raise ValueError("capacity PER binding requires component hashes")
        if self.applicable and not self.per_snapshot_hash:
            raise ValueError("applicable capacity PER binding requires per_snapshot_hash")


@dataclass(frozen=True)
class CapacityAuditResult:
    report: AuditReport
    audit_hash: str

    @property
    def passed(self) -> bool:
        return self.report.passed


def _core_projects(assessment: CapacityCommitmentAssessment):
    return tuple(
        project
        for segment in assessment.segments
        for project in segment.projects
        if project.core_inclusion_required
    )


def _not_applicable_consumption_hash(assessment: CapacityCommitmentAssessment) -> str:
    return _stable_hash(
        {
            "contract": "capacity_bridge_consumption/not_applicable",
            "assessment_hash": assessment.assessment_hash,
        }
    )


def capacity_scenario_binding_adapter(
    *,
    core_scenario_id: str | None = None,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        assessment = context.data.get("capacity_commitment_assessment")
        if not isinstance(assessment, CapacityCommitmentAssessment):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "typed CapacityCommitmentAssessment is required before Scenario Build completes",
                blocking=True,
            )
        projects = _core_projects(assessment)
        if not projects:
            consumption_hash = _not_applicable_consumption_hash(assessment)
            result = CapacityScenarioBindingResult(
                assessment_hash=assessment.assessment_hash,
                consumption_hash=consumption_hash,
                core_scenario_id=None,
                required_role_paths=(),
                compiled_assumption_keys=(),
                binding_hash=_stable_hash(
                    {
                        "contract": "capacity_scenario_binding/v1",
                        "assessment_hash": assessment.assessment_hash,
                        "consumption_hash": consumption_hash,
                        "applicable": False,
                    }
                ),
            )
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "no Core-inclusion capacity project requires scenario binding",
                {
                    "capacity_scenario_binding_result": result,
                    "capacity_scenario_binding_hash": result.binding_hash,
                },
            )

        consumption = context.data.get("capacity_bridge_consumption_result")
        compiled = context.data.get("compiled_assumption_set")
        scenario_set = context.data.get("bound_scenario_set")
        if not isinstance(consumption, CapacityBridgeConsumptionResult):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "CapacityBridgeConsumptionResult is required for Core capacity scenarios",
                blocking=True,
            )
        if not isinstance(compiled, CompiledAssumptionSet):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "CompiledAssumptionSet is required for capacity scenario binding",
                blocking=True,
            )
        if not isinstance(scenario_set, BoundScenarioSet):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "BoundScenarioSet is required for capacity scenario binding",
                blocking=True,
            )
        if consumption.assessment_hash != assessment.assessment_hash:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "capacity consumption assessment hash is stale",
                blocking=True,
            )

        selected_scenario_id = core_scenario_id
        if selected_scenario_id is None:
            if len(scenario_set.scenarios) != 1:
                return StageExecutionResult(
                    StageStatus.RECOVERY_REQUIRED,
                    "capacity Core scenario is ambiguous; configure capacity_core_scenario_id",
                    blocking=True,
                )
            selected_scenario_id = scenario_set.scenarios[0].scenario_id
        try:
            core_scenario = scenario_set.get(selected_scenario_id)
        except KeyError:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"configured capacity Core scenario is not bound: {selected_scenario_id}",
                blocking=True,
            )

        required_paths = tuple(row[4] for row in consumption.role_bindings)
        assumption_keys: list[str] = []
        failures: list[str] = []
        for project_id, _segment_id, role, bridge_id, role_path in consumption.role_bindings:
            compiled_matches = tuple(
                item
                for item in compiled.assumptions
                if item.economic_path_id == role_path and item.bridge_id == bridge_id
            )
            bound_matches = tuple(
                item
                for item in core_scenario.assumptions
                if item.economic_path_id == role_path and item.bridge_id == bridge_id
            )
            if len(compiled_matches) != 1:
                failures.append(
                    f"{project_id}/{role}: expected one compiled assumption, got {len(compiled_matches)}"
                )
                continue
            if len(bound_matches) != 1:
                failures.append(
                    f"{project_id}/{role}: expected one Core-bound assumption, got {len(bound_matches)}"
                )
                continue
            assumption_keys.append(compiled_matches[0].key)
        if failures:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "Core capacity assumptions were omitted or duplicated: " + " | ".join(failures),
                blocking=True,
            )

        payload = {
            "contract": "capacity_scenario_binding/v1",
            "assessment_hash": assessment.assessment_hash,
            "consumption_hash": consumption.consumption_hash,
            "scenario_set_hash": scenario_set.scenario_set_hash,
            "core_scenario_id": selected_scenario_id,
            "required_role_paths": required_paths,
            "compiled_assumption_keys": assumption_keys,
        }
        result = CapacityScenarioBindingResult(
            assessment_hash=assessment.assessment_hash,
            consumption_hash=consumption.consumption_hash,
            core_scenario_id=selected_scenario_id,
            required_role_paths=required_paths,
            compiled_assumption_keys=tuple(assumption_keys),
            binding_hash=_stable_hash(payload),
        )
        return StageExecutionResult(
            StageStatus.PASS,
            "every required capacity, CAPEX and ramp path compiled into the Core scenario",
            {
                "capacity_scenario_binding_result": result,
                "capacity_scenario_binding_hash": result.binding_hash,
            },
        )

    return run


def capacity_valuation_binding_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        assessment = context.data.get("capacity_commitment_assessment")
        scenario_binding = context.data.get("capacity_scenario_binding_result")
        valuation = context.data.get("generic_valuation_result")
        if not isinstance(assessment, CapacityCommitmentAssessment):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "CapacityCommitmentAssessment is required before capacity valuation binding",
                blocking=True,
            )
        if not isinstance(scenario_binding, CapacityScenarioBindingResult):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "CapacityScenarioBindingResult is required before capacity valuation binding",
                blocking=True,
            )
        if not isinstance(valuation, GenericValuationResult):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "GenericValuationResult is required before capacity valuation binding",
                blocking=True,
            )
        if scenario_binding.assessment_hash != assessment.assessment_hash:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "capacity scenario binding is stale relative to the assessment",
                blocking=True,
            )

        projects = _core_projects(assessment)
        if not projects:
            consumed: tuple[str, ...] = ()
        else:
            by_scenario = {
                item.scenario_id: item for item in valuation.scenarios
            }
            scenario_value = by_scenario.get(str(scenario_binding.core_scenario_id))
            if scenario_value is None:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "deterministic valuation omitted the configured capacity Core scenario",
                    blocking=True,
                )
            required = set(scenario_binding.required_role_paths)
            consumed = tuple(
                path for path in scenario_value.economic_path_ids if path in required
            )
            missing = tuple(sorted(required - set(consumed)))
            if missing:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "deterministic valuation did not consume Core capacity paths: "
                    + ", ".join(missing),
                    blocking=True,
                )

        payload = {
            "contract": "capacity_valuation_binding/v1",
            "assessment_hash": assessment.assessment_hash,
            "scenario_binding_hash": scenario_binding.binding_hash,
            "valuation_hash": valuation.valuation_hash,
            "core_scenario_id": scenario_binding.core_scenario_id,
            "required_role_paths": scenario_binding.required_role_paths,
            "consumed_role_paths": consumed,
        }
        result = CapacityValuationBindingResult(
            assessment_hash=assessment.assessment_hash,
            scenario_binding_hash=scenario_binding.binding_hash,
            valuation_hash=valuation.valuation_hash,
            core_scenario_id=scenario_binding.core_scenario_id,
            required_role_paths=scenario_binding.required_role_paths,
            consumed_role_paths=tuple(consumed),
            binding_hash=_stable_hash(payload),
        )
        return StageExecutionResult(
            StageStatus.SKIPPED_NOT_APPLICABLE if not projects else StageStatus.PASS,
            (
                "no Core-inclusion capacity project requires valuation binding"
                if not projects
                else "deterministic valuation consumed every Core capacity economic path"
            ),
            {
                "capacity_valuation_binding_result": result,
                "capacity_valuation_binding_hash": result.binding_hash,
            },
        )

    return run


def capacity_per_binding_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        assessment = context.data.get("capacity_commitment_assessment")
        valuation_binding = context.data.get("capacity_valuation_binding_result")
        if not isinstance(assessment, CapacityCommitmentAssessment):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "CapacityCommitmentAssessment is required before capacity PER binding",
                blocking=True,
            )
        if not isinstance(valuation_binding, CapacityValuationBindingResult):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "CapacityValuationBindingResult is required before capacity PER binding",
                blocking=True,
            )
        if valuation_binding.assessment_hash != assessment.assessment_hash:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "capacity valuation binding is stale relative to the assessment",
                blocking=True,
            )

        per_applicable = bool(context.data.get("warranted_per_applicable", False))
        per_result = context.data.get("live_warranted_per_result")
        if per_applicable and not isinstance(per_result, LiveWarrantedPERStageResult):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "applicable Warranted PER requires LiveWarrantedPERStageResult",
                blocking=True,
            )

        expansion_ids = (
            per_result.expansion_evidence_ids
            if isinstance(per_result, LiveWarrantedPERStageResult)
            else ()
        )
        core_evidence = {
            evidence_id
            for project in _core_projects(assessment)
            for evidence_id in project.qualifying_evidence_ids
        }
        overlap = tuple(sorted(core_evidence.intersection(expansion_ids)))
        if overlap:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "Core capacity Evidence was reused to open Expansion-Adjusted PER: "
                + ", ".join(overlap),
                blocking=True,
            )

        per_hash = (
            per_result.snapshot_hash
            if isinstance(per_result, LiveWarrantedPERStageResult)
            else None
        )
        payload = {
            "contract": "capacity_per_binding/v1",
            "assessment_hash": assessment.assessment_hash,
            "valuation_binding_hash": valuation_binding.binding_hash,
            "applicable": per_applicable,
            "per_snapshot_hash": per_hash,
            "expansion_evidence_ids": expansion_ids,
            "core_evidence_overlap": overlap,
        }
        result = CapacityPERBindingResult(
            assessment_hash=assessment.assessment_hash,
            valuation_binding_hash=valuation_binding.binding_hash,
            applicable=per_applicable,
            per_snapshot_hash=per_hash,
            expansion_evidence_ids=tuple(expansion_ids),
            core_evidence_overlap=overlap,
            binding_hash=_stable_hash(payload),
        )
        return StageExecutionResult(
            StageStatus.PASS if per_applicable else StageStatus.SKIPPED_NOT_APPLICABLE,
            (
                "Warranted PER is bound to the same capacity assessment without Core-Evidence reuse"
                if per_applicable
                else "Warranted PER is not applicable; capacity PER double-count path is closed"
            ),
            {
                "capacity_per_binding_result": result,
                "capacity_per_binding_hash": result.binding_hash,
            },
        )

    return run


def capacity_consistency_gate_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        assessment = context.data.get("capacity_commitment_assessment")
        scenario_binding = context.data.get("capacity_scenario_binding_result")
        valuation_binding = context.data.get("capacity_valuation_binding_result")
        per_binding = context.data.get("capacity_per_binding_result")
        if not isinstance(assessment, CapacityCommitmentAssessment):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "CapacityCommitmentAssessment is required for capacity consistency",
                blocking=True,
            )
        if not isinstance(scenario_binding, CapacityScenarioBindingResult):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "CapacityScenarioBindingResult is required for capacity consistency",
                blocking=True,
            )
        if not isinstance(valuation_binding, CapacityValuationBindingResult):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "CapacityValuationBindingResult is required for capacity consistency",
                blocking=True,
            )
        if not isinstance(per_binding, CapacityPERBindingResult):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "CapacityPERBindingResult is required for capacity consistency",
                blocking=True,
            )

        hashes = {
            assessment.assessment_hash,
            scenario_binding.assessment_hash,
            valuation_binding.assessment_hash,
            per_binding.assessment_hash,
        }
        paths_match = (
            set(valuation_binding.required_role_paths)
            == set(valuation_binding.consumed_role_paths)
            == set(scenario_binding.required_role_paths)
        )
        if len(hashes) != 1 or not paths_match:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "capacity assessment/path identity drifted across Scenario, Valuation or PER",
                blocking=True,
            )
        result_hash = _stable_hash(
            {
                "contract": "capacity_consistency/v1",
                "assessment_hash": assessment.assessment_hash,
                "scenario_binding_hash": scenario_binding.binding_hash,
                "valuation_binding_hash": valuation_binding.binding_hash,
                "per_binding_hash": per_binding.binding_hash,
            }
        )
        return StageExecutionResult(
            StageStatus.PASS,
            "capacity assessment, scenario, valuation and PER identities are consistent",
            {"capacity_consistency_hash": result_hash},
        )

    return run


def capacity_audit_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        assessment = context.data.get("capacity_commitment_assessment")
        scenario_binding = context.data.get("capacity_scenario_binding_result")
        valuation_binding = context.data.get("capacity_valuation_binding_result")
        per_binding = context.data.get("capacity_per_binding_result")
        consistency_hash = context.data.get("capacity_consistency_hash")
        consumption = context.data.get("capacity_bridge_consumption_result")

        typed = all(
            (
                isinstance(assessment, CapacityCommitmentAssessment),
                isinstance(scenario_binding, CapacityScenarioBindingResult),
                isinstance(valuation_binding, CapacityValuationBindingResult),
                isinstance(per_binding, CapacityPERBindingResult),
                isinstance(consistency_hash, str) and bool(consistency_hash),
            )
        )
        if not typed:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "capacity runtime artifacts are missing; the gate may have been bypassed",
                blocking=True,
            )
        assert isinstance(assessment, CapacityCommitmentAssessment)
        assert isinstance(scenario_binding, CapacityScenarioBindingResult)
        assert isinstance(valuation_binding, CapacityValuationBindingResult)
        assert isinstance(per_binding, CapacityPERBindingResult)
        assert isinstance(consistency_hash, str)

        projects = _core_projects(assessment)
        expected_project_ids = {item.project_id for item in projects}
        consumed_project_ids = (
            set(consumption.consumed_project_ids)
            if isinstance(consumption, CapacityBridgeConsumptionResult)
            else set()
        )
        material_consumed = (
            not projects
            or (
                consumed_project_ids == expected_project_ids
                and bool(scenario_binding.required_role_paths)
            )
        )
        hashes_bound = len(
            {
                assessment.assessment_hash,
                scenario_binding.assessment_hash,
                valuation_binding.assessment_hash,
                per_binding.assessment_hash,
            }
        ) == 1
        floor_respected = (
            not projects
            or set(valuation_binding.required_role_paths)
            == set(valuation_binding.consumed_role_paths)
        )
        baseline_clean = all(
            not (
                project.baseline_inclusion is BaselineInclusionStatus.IN_BASELINE
                and project.core_inclusion_required
            )
            for segment in assessment.segments
            for project in segment.projects
        )
        per_consistent = (
            per_binding.valuation_binding_hash == valuation_binding.binding_hash
            and per_binding.assessment_hash == assessment.assessment_hash
        )
        no_double_count = not per_binding.core_evidence_overlap

        findings = (
            AuditFinding(
                "material_capacity_evidence_consumed",
                material_consumed,
                True,
                "every Core-required project has capacity/CAPEX/ramp consumption"
                if material_consumed
                else "Core-required capacity project was not fully consumed",
            ),
            AuditFinding(
                "capacity_commitment_hash_binding",
                hashes_bound,
                True,
                "one capacity assessment hash is shared across all downstream artifacts"
                if hashes_bound
                else "capacity assessment hash drift detected",
            ),
            AuditFinding(
                "core_capacity_floor_respected",
                floor_respected,
                True,
                "valuation consumed the complete mandatory Core capacity path"
                if floor_respected
                else "valuation omitted a mandatory Core capacity path",
            ),
            AuditFinding(
                "baseline_capacity_not_double_counted",
                baseline_clean,
                True,
                "baseline-included projects are not added again"
                if baseline_clean
                else "baseline capacity was marked for incremental Core inclusion",
            ),
            AuditFinding(
                "dcf_per_capacity_consistency",
                per_consistent,
                True,
                "PER is bound to the same capacity valuation identity"
                if per_consistent
                else "PER and DCF capacity identities diverged",
            ),
            AuditFinding(
                "capacity_double_count",
                no_double_count,
                True,
                "Core capacity Evidence is not reused as Expansion PER evidence"
                if no_double_count
                else "Core capacity Evidence was reused by Expansion PER",
            ),
        )
        report = AuditReport(findings)
        audit_hash = _stable_hash(
            {
                "contract": "capacity_audit/v1",
                "assessment_hash": assessment.assessment_hash,
                "scenario_binding_hash": scenario_binding.binding_hash,
                "valuation_binding_hash": valuation_binding.binding_hash,
                "per_binding_hash": per_binding.binding_hash,
                "consistency_hash": consistency_hash,
                "findings": [
                    {
                        "check": item.check,
                        "passed": item.passed,
                        "blocking": item.blocking,
                        "detail": item.detail,
                    }
                    for item in findings
                ],
            }
        )
        common = {
            "capacity_audit_result": CapacityAuditResult(report, audit_hash),
            "capacity_audit_report": report,
            "capacity_audit_hash": audit_hash,
            "capacity_audit_passed": report.passed,
        }
        if not report.passed:
            failed = tuple(
                item.check for item in findings if item.blocking and not item.passed
            )
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "capacity audit failed: " + ", ".join(failed),
                common,
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "capacity omission, baseline and double-count audit passed",
            common,
        )

    return run
