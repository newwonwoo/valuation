from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json

from .ablation import AblationStatus, ModuleAblationSpec, run_module_ablations
from .assumption_compiler import CompiledAssumptionSet
from .control_plane import DoctrineCoverageEntry, StageStatus
from .decision_impact import DecisionOutcome, ResearchEffort
from .distributional_runtime import DistributionalPrimaryValuationResult
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .unit_contracts import UnitContractRegistry


_ELIGIBLE_UNIT_TYPES = {
    "source_adapter",
    "normalizer",
    "router",
    "scanner",
    "gate",
    "llm_role",
    "bridge",
    "compiler",
    "scenario_engine",
    "risk_engine",
    "valuation_engine",
    "aggregator",
}


def _baseline(
    *,
    compiled: CompiledAssumptionSet,
    valuation: DistributionalPrimaryValuationResult,
    selected_methods: tuple[str, ...],
) -> DecisionOutcome:
    intrinsic: float | None = None
    tags: tuple[str, ...]
    status: str
    if valuation.pathwise_distribution is not None:
        mean = valuation.pathwise_distribution.mean
        intrinsic = float(mean) if mean > 0 else None
        tags = ("distributional_pathwise_intrinsic",)
        status = "COMPLETED"
    else:
        tags = ("governed_prior_intrinsic_range", "point_target_withheld")
        status = "RANGE_INTRINSIC"
    return DecisionOutcome(
        status=status,
        intrinsic_value_per_share=intrinsic,
        assumption_hash=compiled.assumption_set_hash,
        route_hash=valuation.route_authorization.authorization_hash,
        selected_methods=selected_methods,
        conclusion_tags=tags,
    )


def run_distributional_decision_impact(
    context: OrchestratorContext,
    *,
    registry: UnitContractRegistry,
) -> tuple[object, str, bool]:
    compiled = context.data.get("compiled_assumption_set")
    valuation = context.data.get("distributional_primary_result")
    coverage = context.data.get("pre_audit_doctrine_coverage")
    selected_methods = context.data.get("selected_methods", ())
    if not isinstance(compiled, CompiledAssumptionSet):
        raise ValueError("distributional decision impact requires CompiledAssumptionSet")
    if not isinstance(valuation, DistributionalPrimaryValuationResult):
        raise ValueError("distributional decision impact requires primary distributional result")
    if not isinstance(coverage, tuple) or not all(
        isinstance(item, DoctrineCoverageEntry) for item in coverage
    ):
        raise ValueError("distributional decision impact requires pre-audit doctrine coverage")
    if not isinstance(selected_methods, tuple) or not all(
        isinstance(item, str) and item for item in selected_methods
    ):
        raise ValueError("selected_methods must be a non-empty string tuple")

    baseline = _baseline(
        compiled=compiled,
        valuation=valuation,
        selected_methods=selected_methods,
    )
    coverage_by_id = {item.module_id: item for item in coverage}
    specs: list[ModuleAblationSpec] = []
    for contract in registry.units:
        entry = coverage_by_id.get(contract.unit_id)
        if entry is None or contract.unit_type not in _ELIGIBLE_UNIT_TYPES:
            continue
        applicable = entry.status in {
            StageStatus.PASS,
            StageStatus.WARNING,
            StageStatus.RECOVERED,
        }
        mandatory_guardrail = contract.unit_type == "gate"
        supported = contract.unit_id == "DETERMINISTIC_VALUATION"
        specs.append(
            ModuleAblationSpec(
                module_id=contract.unit_id,
                applicable=applicable,
                mandatory_guardrail=mandatory_guardrail,
                counterfactual_supported=supported,
                research_effort=ResearchEffort(),
                expected_impact_paths=contract.final_outputs,
            )
        )

    def run_without_module(module_id: str) -> DecisionOutcome:
        if module_id != "DETERMINISTIC_VALUATION":
            return baseline
        return DecisionOutcome(
            status="VALUATION_BLOCKED",
            assumption_hash=compiled.assumption_set_hash,
            route_hash=valuation.route_authorization.authorization_hash,
            selected_methods=selected_methods,
            conclusion_tags=("distributional_primary_valuation_removed",),
            blocked_reasons=("registered primary valuation unit removed",),
        )

    batch = run_module_ablations(
        baseline=baseline,
        specs=tuple(specs),
        run_without_module=run_without_module,
    )
    failed = tuple(
        item.module_id
        for item in batch.module_observations
        if item.status is AblationStatus.FAILED
    )
    payload = {
        "contract": "distributional_decision_impact/v1",
        "baseline": asdict(batch.baseline),
        "observations": [
            {
                "module_id": item.module_id,
                "status": item.status.value,
                "applicable": item.applicable,
                "mandatory_guardrail": item.mandatory_guardrail,
                "assessment": asdict(item.assessment) if item.assessment is not None else None,
                "note": item.note,
            }
            for item in batch.module_observations
        ],
    }
    impact_hash = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    return batch, impact_hash, not failed


def distributional_decision_impact_adapter(
    *,
    registry: UnitContractRegistry,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            batch, impact_hash, completed = run_distributional_decision_impact(
                context,
                registry=registry,
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"distributional decision-impact measurement failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        outputs = {
            "decision_impact_batch": batch,
            "decision_impact_hash": impact_hash,
            "decision_impact_completed": completed,
        }
        if not completed:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "distributional decision-impact measurement contains failed counterfactuals",
                outputs,
                blocking=True,
            )
        not_measurable = tuple(
            item.module_id
            for item in batch.module_observations
            if item.status is AblationStatus.NOT_MEASURABLE
        )
        return StageExecutionResult(
            StageStatus.PASS,
            "distributional primary-value removal counterfactual measured"
            + (
                "; explicit NOT_MEASURABLE modules: " + ", ".join(not_measurable)
                if not_measurable
                else ""
            ),
            outputs,
        )

    return run


__all__ = [
    "distributional_decision_impact_adapter",
    "run_distributional_decision_impact",
]
