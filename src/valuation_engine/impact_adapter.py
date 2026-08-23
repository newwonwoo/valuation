from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from typing import Callable

from .ablation import (
    AblationBatchResult,
    AblationStatus,
    ModuleAblationSpec,
    retirement_proposals_allowed,
    run_module_ablations,
)
from .assumption_compiler import CompiledAssumptionSet
from .control_plane import DoctrineCoverageEntry, StageStatus
from .decision_impact import DecisionOutcome, ModuleHistoryEntry, ResearchEffort
from .orchestrator import OrchestratorContext
from .unit_contracts import UnitContractRegistry
from .valuation_execution import GenericValuationResult


CounterfactualFactory = Callable[[OrchestratorContext], DecisionOutcome]
GuardrailProbeFactory = Callable[[OrchestratorContext], bool]


@dataclass(frozen=True)
class GenericDecisionImpactConfig:
    counterfactual_runners: dict[str, CounterfactualFactory] = field(default_factory=dict)
    guardrail_probes: dict[str, GuardrailProbeFactory] = field(default_factory=dict)
    research_effort: dict[str, ResearchEffort] = field(default_factory=dict)
    prior_history: dict[str, tuple[ModuleHistoryEntry, ...]] = field(default_factory=dict)
    include_unit_ids: tuple[str, ...] = ()
    mandatory_guardrail_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class GenericDecisionImpactResult:
    baseline: DecisionOutcome
    batch: AblationBatchResult
    impact_hash: str
    failed_modules: tuple[str, ...]
    not_measurable_modules: tuple[str, ...]
    retirement_review_candidates: tuple[str, ...]

    @property
    def completed(self) -> bool:
        return not self.failed_modules


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


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_jsonable(item) for item in value]
    return value.value if hasattr(value, "value") else value


def _stable_hash(value) -> str:
    payload = json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def build_generic_decision_outcome(context: OrchestratorContext) -> DecisionOutcome:
    compiled = context.data.get("compiled_assumption_set")
    valuation = context.data.get("generic_valuation_result")
    if not isinstance(compiled, CompiledAssumptionSet):
        raise ValueError("decision impact requires CompiledAssumptionSet")
    if not isinstance(valuation, GenericValuationResult):
        raise ValueError("decision impact requires GenericValuationResult")

    selected_methods = context.data.get("selected_methods", ())
    if not isinstance(selected_methods, tuple) or not all(isinstance(item, str) for item in selected_methods):
        raise ValueError("selected_methods must be a string tuple")
    route_hash = context.data.get("route_hash")
    if route_hash is None:
        route_hash = _stable_hash({"selected_methods": selected_methods, "target_id": compiled.target_id})
    conclusion_tags = context.data.get("conclusion_tags", ())
    if not isinstance(conclusion_tags, tuple) or not all(isinstance(item, str) for item in conclusion_tags):
        raise ValueError("conclusion_tags must be a string tuple")
    timing_days = context.data.get("timing_days")
    intrinsic = (
        float(valuation.expected_value_per_share)
        if valuation.expected_value_per_share is not None
        else None
    )
    return DecisionOutcome(
        status=str(context.data.get("decision_status", "COMPLETED")),
        intrinsic_value_per_share=intrinsic,
        assumption_hash=compiled.assumption_set_hash,
        route_hash=str(route_hash),
        selected_methods=selected_methods,
        conclusion_tags=conclusion_tags,
        timing_days=float(timing_days) if timing_days is not None else None,
        blocked_reasons=tuple(context.data.get("decision_blocked_reasons", ())),
    )


def _without_deterministic_valuation(context: OrchestratorContext) -> DecisionOutcome:
    """Built-in reproducible counterfactual shared by every Generic Run.

    Removing the registered deterministic valuation unit cannot produce an alternative fair
    value; it produces an explicit VALUATION_BLOCKED decision while preserving the upstream
    assumption/route/method identity. This measures decision criticality without inventing a
    zero value or changing the canonical baseline run.
    """
    compiled = context.data.get("compiled_assumption_set")
    if not isinstance(compiled, CompiledAssumptionSet):
        raise ValueError("deterministic-valuation counterfactual requires CompiledAssumptionSet")
    selected_methods = context.data.get("selected_methods", ())
    if not isinstance(selected_methods, tuple) or not all(isinstance(item, str) for item in selected_methods):
        raise ValueError("selected_methods must be a string tuple")
    route_hash = context.data.get("route_hash")
    if route_hash is None:
        route_hash = _stable_hash({"selected_methods": selected_methods, "target_id": compiled.target_id})
    return DecisionOutcome(
        status="VALUATION_BLOCKED",
        assumption_hash=compiled.assumption_set_hash,
        route_hash=str(route_hash),
        selected_methods=selected_methods,
        conclusion_tags=("deterministic_valuation_removed",),
        blocked_reasons=("registered deterministic valuation unit removed",),
    )


def run_generic_decision_impact(
    context: OrchestratorContext,
    *,
    registry: UnitContractRegistry,
    config: GenericDecisionImpactConfig | None = None,
) -> GenericDecisionImpactResult:
    """Measure active pre-audit units without mutating the canonical intrinsic run.

    Units with no reproducible counterfactual adapter are explicitly NOT_MEASURABLE rather
    than being misclassified as zero-impact. Guardrail probes may establish criticality even
    when the numerical counterfactual is intentionally identical to baseline. The exact
    deterministic valuation unit always has a built-in removal counterfactual.
    """
    config = config or GenericDecisionImpactConfig()
    baseline = build_generic_decision_outcome(context)
    coverage = context.data.get("pre_audit_doctrine_coverage")
    if not isinstance(coverage, tuple) or not all(isinstance(item, DoctrineCoverageEntry) for item in coverage):
        raise ValueError("decision impact requires generated pre-audit doctrine coverage")
    coverage_by_id = {item.module_id: item for item in coverage}
    include = set(config.include_unit_ids)
    mandatory = set(config.mandatory_guardrail_ids)
    counterfactual_runners = dict(config.counterfactual_runners)
    counterfactual_runners.setdefault("DETERMINISTIC_VALUATION", _without_deterministic_valuation)

    specs: list[ModuleAblationSpec] = []
    for contract in registry.units:
        entry = coverage_by_id.get(contract.unit_id)
        if entry is None or contract.unit_type not in _ELIGIBLE_UNIT_TYPES:
            continue
        if include and contract.unit_id not in include:
            continue
        applicable = entry.status in {StageStatus.PASS, StageStatus.WARNING, StageStatus.RECOVERED}
        mandatory_guardrail = contract.unit_type == "gate" or contract.unit_id in mandatory
        supported = (
            contract.unit_id in counterfactual_runners
            or contract.unit_id in config.guardrail_probes
        )
        specs.append(
            ModuleAblationSpec(
                module_id=contract.unit_id,
                applicable=applicable,
                mandatory_guardrail=mandatory_guardrail,
                counterfactual_supported=supported,
                research_effort=config.research_effort.get(contract.unit_id, ResearchEffort()),
                expected_impact_paths=contract.final_outputs,
            )
        )

    def run_without_module(module_id: str) -> DecisionOutcome:
        runner = counterfactual_runners.get(module_id)
        return runner(context) if runner is not None else baseline

    def guardrail_probe(module_id: str) -> bool:
        probe = config.guardrail_probes.get(module_id)
        return bool(probe(context)) if probe is not None else False

    batch = run_module_ablations(
        baseline=baseline,
        specs=tuple(specs),
        run_without_module=run_without_module,
        guardrail_probe=guardrail_probe,
        prior_history=config.prior_history,
    )
    failed = tuple(
        item.module_id for item in batch.module_observations if item.status is AblationStatus.FAILED
    )
    not_measurable = tuple(
        item.module_id
        for item in batch.module_observations
        if item.status is AblationStatus.NOT_MEASURABLE
    )
    payload = {
        "baseline": asdict(baseline),
        "observations": [asdict(item) for item in batch.module_observations],
        "joint": [asdict(item) for item in batch.joint_observations],
        "recommendations": [asdict(item) for item in batch.loadout_recommendations],
    }
    return GenericDecisionImpactResult(
        baseline=baseline,
        batch=batch,
        impact_hash=_stable_hash(payload),
        failed_modules=failed,
        not_measurable_modules=not_measurable,
        retirement_review_candidates=retirement_proposals_allowed(batch),
    )
