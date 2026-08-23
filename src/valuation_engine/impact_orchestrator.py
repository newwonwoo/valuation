from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from statistics import fmean
from typing import Callable

from .decision_impact import (
    ImpactPolicy,
    ModuleHistoryEntry,
    ModuleImpactAssessment,
    ModuleImpactTrace,
    ResearchEffort,
    ResearchIntensity,
    compare_module_counterfactual,
    recommend_research_intensity,
)
from .decision_impact import DecisionOutcome


class ExperimentKind(str, Enum):
    BASELINE = "baseline"
    SINGLE_ABLATION = "single_ablation"
    PAIR_ABLATION = "pair_ablation"


class LoadoutDisposition(str, Enum):
    DEPLOY_ALWAYS = "deploy_always"
    DEPLOY_CONDITIONAL = "deploy_conditional"
    SAMPLE = "sample"
    KEEP_GUARDRAIL = "keep_guardrail"
    RETIRE_REVIEW = "retire_review"
    SKIP_NOT_APPLICABLE = "skip_not_applicable"


@dataclass(frozen=True)
class ModuleExperimentSpec:
    module_id: str
    applicable: bool = True
    mandatory_guardrail: bool = False
    interaction_group: str | None = None
    condition_met: bool = True
    sample_due: bool = True
    research_performed: bool = True
    effort: ResearchEffort = field(default_factory=ResearchEffort)

    def __post_init__(self) -> None:
        if not self.module_id:
            raise ValueError("module_id is required")
        if self.mandatory_guardrail and not self.applicable:
            raise ValueError("mandatory guardrail cannot be marked non-applicable")


@dataclass(frozen=True)
class ExperimentRequest:
    kind: ExperimentKind
    active_modules: tuple[str, ...]
    removed_modules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.active_modules) != len(set(self.active_modules)):
            raise ValueError("active_modules must be unique")
        if len(self.removed_modules) != len(set(self.removed_modules)):
            raise ValueError("removed_modules must be unique")
        if set(self.active_modules) & set(self.removed_modules):
            raise ValueError("active and removed modules must be disjoint")
        if self.kind is ExperimentKind.BASELINE and self.removed_modules:
            raise ValueError("baseline cannot remove modules")
        if self.kind is ExperimentKind.SINGLE_ABLATION and len(self.removed_modules) != 1:
            raise ValueError("single ablation requires exactly one removed module")
        if self.kind is ExperimentKind.PAIR_ABLATION and len(self.removed_modules) != 2:
            raise ValueError("pair ablation requires exactly two removed modules")


@dataclass(frozen=True)
class ExperimentArtifact:
    outcome: DecisionOutcome
    traces: tuple[ModuleImpactTrace, ...] = ()
    guardrail_violations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        trace_ids = tuple(trace.module_id for trace in self.traces)
        if len(trace_ids) != len(set(trace_ids)):
            raise ValueError("experiment traces must have unique module_id values")
        for trace in self.traces:
            trace.validate()

    def trace_for(self, module_id: str) -> ModuleImpactTrace | None:
        return next((trace for trace in self.traces if trace.module_id == module_id), None)


ExperimentRunner = Callable[[ExperimentRequest], ExperimentArtifact]


@dataclass(frozen=True)
class ModuleAblationResult:
    module_id: str
    assessment: ModuleImpactAssessment
    baseline_trace: ModuleImpactTrace | None
    counterfactual: DecisionOutcome


@dataclass(frozen=True)
class PairInteractionResult:
    module_a: str
    module_b: str
    interaction_group: str
    individual_delta_a: float
    individual_delta_b: float
    joint_delta: float
    interaction_residual: float
    interaction_pct_of_baseline: float
    material: bool


@dataclass(frozen=True)
class AblationReport:
    baseline: ExperimentArtifact
    module_results: tuple[ModuleAblationResult, ...]
    pair_interactions: tuple[PairInteractionResult, ...]
    skipped_not_applicable: tuple[str, ...]
    missing_baseline_traces: tuple[str, ...]

    def result_for(self, module_id: str) -> ModuleAblationResult:
        result = next((row for row in self.module_results if row.module_id == module_id), None)
        if result is None:
            raise ValueError(f"unknown ablation result: {module_id}")
        return result


@dataclass(frozen=True)
class ModuleHistory:
    module_id: str
    entries: tuple[ModuleHistoryEntry, ...]


@dataclass(frozen=True)
class ModuleEfficiencySummary:
    module_id: str
    observations: int
    material_rate: float
    mean_documents_reviewed: float
    mean_elapsed_seconds: float
    total_source_queries: int
    total_llm_calls: int
    recommended_intensity: ResearchIntensity


@dataclass(frozen=True)
class LoadoutRecommendation:
    module_id: str
    intensity: ResearchIntensity
    disposition: LoadoutDisposition
    deploy_by_default: bool
    requires_user_approval_to_retire: bool
    rationale: str


@dataclass(frozen=True)
class AdaptiveLoadoutPlan:
    recommendations: tuple[LoadoutRecommendation, ...]

    @property
    def deployed_modules(self) -> tuple[str, ...]:
        return tuple(row.module_id for row in self.recommendations if row.deploy_by_default)

    @property
    def retire_review_modules(self) -> tuple[str, ...]:
        return tuple(
            row.module_id
            for row in self.recommendations
            if row.disposition is LoadoutDisposition.RETIRE_REVIEW
        )


def run_automatic_ablation(
    specs: tuple[ModuleExperimentSpec, ...],
    runner: ExperimentRunner,
    *,
    policy: ImpactPolicy | None = None,
    measure_pair_interactions: bool = True,
    max_pair_runs: int = 20,
) -> AblationReport:
    """Run one baseline, leave-one-module-out counterfactuals, and bounded pair ablations.

    The runner must be deterministic for the same request and must not use target-market
    information to tune intrinsic assumptions. Mandatory guardrails are removed only inside
    controlled counterfactual experiments; a detected violation keeps them permanently.
    """
    policy = policy or ImpactPolicy()
    if not specs:
        raise ValueError("at least one module experiment spec is required")
    if max_pair_runs < 0:
        raise ValueError("max_pair_runs must be non-negative")

    module_ids = tuple(spec.module_id for spec in specs)
    if len(module_ids) != len(set(module_ids)):
        raise ValueError("module experiment specs must have unique module_id values")

    applicable = tuple(spec for spec in specs if spec.applicable)
    skipped = tuple(spec.module_id for spec in specs if not spec.applicable)
    active_modules = tuple(sorted(spec.module_id for spec in applicable))
    baseline = runner(ExperimentRequest(ExperimentKind.BASELINE, active_modules))

    results: list[ModuleAblationResult] = []
    missing_traces: list[str] = []

    for spec in applicable:
        remaining = tuple(module_id for module_id in active_modules if module_id != spec.module_id)
        artifact = runner(
            ExperimentRequest(
                ExperimentKind.SINGLE_ABLATION,
                remaining,
                (spec.module_id,),
            )
        )
        trace = baseline.trace_for(spec.module_id)
        if trace is None:
            missing_traces.append(spec.module_id)
        assessment = compare_module_counterfactual(
            spec.module_id,
            baseline=baseline.outcome,
            counterfactual=artifact.outcome,
            guardrail_violation_detected=spec.module_id in artifact.guardrail_violations,
            policy=policy,
        )
        results.append(ModuleAblationResult(spec.module_id, assessment, trace, artifact.outcome))

    interactions: list[PairInteractionResult] = []
    if measure_pair_interactions and max_pair_runs:
        eligible_pairs: list[tuple[ModuleExperimentSpec, ModuleExperimentSpec]] = []
        for index, left in enumerate(applicable):
            if not left.interaction_group:
                continue
            for right in applicable[index + 1 :]:
                if right.interaction_group == left.interaction_group:
                    eligible_pairs.append((left, right))
        eligible_pairs.sort(key=lambda pair: (pair[0].module_id, pair[1].module_id))

        baseline_value = baseline.outcome.intrinsic_value_per_share
        result_map = {row.module_id: row for row in results}
        for left, right in eligible_pairs[:max_pair_runs]:
            remaining = tuple(
                module_id
                for module_id in active_modules
                if module_id not in {left.module_id, right.module_id}
            )
            artifact = runner(
                ExperimentRequest(
                    ExperimentKind.PAIR_ABLATION,
                    remaining,
                    tuple(sorted((left.module_id, right.module_id))),
                )
            )
            joint_value = artifact.outcome.intrinsic_value_per_share
            delta_a = result_map[left.module_id].assessment.value_delta_abs
            delta_b = result_map[right.module_id].assessment.value_delta_abs
            if baseline_value is None or joint_value is None or delta_a is None or delta_b is None:
                continue
            joint_delta = baseline_value - joint_value
            residual = joint_delta - delta_a - delta_b
            residual_pct = residual / baseline_value
            interactions.append(
                PairInteractionResult(
                    module_a=left.module_id,
                    module_b=right.module_id,
                    interaction_group=left.interaction_group,
                    individual_delta_a=delta_a,
                    individual_delta_b=delta_b,
                    joint_delta=joint_delta,
                    interaction_residual=residual,
                    interaction_pct_of_baseline=residual_pct,
                    material=abs(residual_pct) >= policy.value_materiality_pct,
                )
            )

    return AblationReport(
        baseline=baseline,
        module_results=tuple(results),
        pair_interactions=tuple(interactions),
        skipped_not_applicable=skipped,
        missing_baseline_traces=tuple(sorted(missing_traces)),
    )


def history_entries_from_report(
    report: AblationReport,
    specs: tuple[ModuleExperimentSpec, ...],
) -> tuple[ModuleHistoryEntry, ...]:
    spec_map = {spec.module_id: spec for spec in specs}
    entries: list[ModuleHistoryEntry] = []
    for row in report.module_results:
        spec = spec_map[row.module_id]
        entries.append(
            ModuleHistoryEntry(
                assessment=row.assessment,
                effort=spec.effort,
                applicable=spec.applicable,
                research_performed=spec.research_performed,
                mandatory_guardrail=spec.mandatory_guardrail,
            )
        )
    return tuple(entries)


def summarize_module_history(
    history: ModuleHistory,
    *,
    policy: ImpactPolicy | None = None,
) -> ModuleEfficiencySummary:
    policy = policy or ImpactPolicy()
    applicable = tuple(entry for entry in history.entries if entry.applicable)
    if not applicable:
        return ModuleEfficiencySummary(
            module_id=history.module_id,
            observations=0,
            material_rate=0.0,
            mean_documents_reviewed=0.0,
            mean_elapsed_seconds=0.0,
            total_source_queries=0,
            total_llm_calls=0,
            recommended_intensity=ResearchIntensity.SAMPLE_ONLY,
        )
    researched = tuple(entry for entry in applicable if entry.research_performed)
    material_rate = sum(1 for entry in applicable if entry.assessment.material) / len(applicable)
    return ModuleEfficiencySummary(
        module_id=history.module_id,
        observations=len(applicable),
        material_rate=material_rate,
        mean_documents_reviewed=(
            fmean(entry.effort.documents_reviewed for entry in researched) if researched else 0.0
        ),
        mean_elapsed_seconds=(
            fmean(entry.effort.elapsed_seconds for entry in researched) if researched else 0.0
        ),
        total_source_queries=sum(entry.effort.source_queries for entry in researched),
        total_llm_calls=sum(entry.effort.llm_calls for entry in researched),
        recommended_intensity=recommend_research_intensity(applicable, policy=policy),
    )


def build_adaptive_loadout(
    specs: tuple[ModuleExperimentSpec, ...],
    histories: tuple[ModuleHistory, ...],
    *,
    policy: ImpactPolicy | None = None,
) -> AdaptiveLoadoutPlan:
    """Translate impact history into the next Control Plane loadout.

    RETIRE_CANDIDATE never deletes a unit. It produces a user-review item. Mandatory
    guardrails always deploy. Sampling remains explicit so low-observed-impact modules can
    still be rechecked for regime changes.
    """
    policy = policy or ImpactPolicy()
    history_map = {history.module_id: history for history in histories}
    recommendations: list[LoadoutRecommendation] = []

    for spec in specs:
        if not spec.applicable:
            recommendations.append(
                LoadoutRecommendation(
                    spec.module_id,
                    ResearchIntensity.SAMPLE_ONLY,
                    LoadoutDisposition.SKIP_NOT_APPLICABLE,
                    False,
                    False,
                    "module is not applicable to the current Industry DNA / mission",
                )
            )
            continue

        entries = history_map.get(spec.module_id, ModuleHistory(spec.module_id, ())).entries
        if spec.mandatory_guardrail:
            intensity = ResearchIntensity.KEEP_GUARDRAIL
        else:
            intensity = recommend_research_intensity(entries, policy=policy)

        if intensity is ResearchIntensity.ALWAYS:
            disposition = LoadoutDisposition.DEPLOY_ALWAYS
            deploy = True
            rationale = "repeatedly material across applicable runs"
        elif intensity is ResearchIntensity.CONDITIONAL:
            disposition = LoadoutDisposition.DEPLOY_CONDITIONAL
            deploy = spec.condition_met
            rationale = "material in a subset of runs; deploy when the activation condition is met"
        elif intensity is ResearchIntensity.KEEP_GUARDRAIL:
            disposition = LoadoutDisposition.KEEP_GUARDRAIL
            deploy = True
            rationale = "mandatory guardrail; ordinary value delta is not a retirement criterion"
        elif intensity is ResearchIntensity.RETIRE_CANDIDATE:
            disposition = LoadoutDisposition.RETIRE_REVIEW
            deploy = False
            rationale = "repeated high-effort, low-observed-impact history; user review required"
        else:
            disposition = LoadoutDisposition.SAMPLE
            deploy = spec.sample_due
            rationale = "insufficient or low-impact history; retain periodic sampling for regime change"

        recommendations.append(
            LoadoutRecommendation(
                module_id=spec.module_id,
                intensity=intensity,
                disposition=disposition,
                deploy_by_default=deploy,
                requires_user_approval_to_retire=(
                    disposition is LoadoutDisposition.RETIRE_REVIEW
                ),
                rationale=rationale,
            )
        )

    return AdaptiveLoadoutPlan(tuple(recommendations))
