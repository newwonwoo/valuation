from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .decision_impact import (
    DecisionOutcome,
    ImpactPolicy,
    ModuleHistoryEntry,
    ModuleImpactAssessment,
    ResearchEffort,
    ResearchIntensity,
    compare_module_counterfactual,
    recommend_research_intensity,
)


class AblationStatus(str, Enum):
    MEASURED = "measured"
    NOT_APPLICABLE = "not_applicable"
    NOT_MEASURABLE = "not_measurable"
    FAILED = "failed"


class LoadoutAction(str, Enum):
    KEEP_ALWAYS = "keep_always"
    KEEP_GUARDRAIL = "keep_guardrail"
    ACTIVATE_IF_TRIGGERED = "activate_if_triggered"
    SAMPLE = "sample"
    PROPOSE_DOWNRANK = "propose_downrank"
    NO_CHANGE = "no_change"


@dataclass(frozen=True)
class ModuleAblationSpec:
    module_id: str
    applicable: bool = True
    mandatory_guardrail: bool = False
    counterfactual_supported: bool = True
    research_effort: ResearchEffort = ResearchEffort()
    trigger_active: bool = True
    expected_impact_paths: tuple[str, ...] = ()
    correlation_group: str | None = None

    def __post_init__(self) -> None:
        if not self.module_id:
            raise ValueError("module ablation spec requires module_id")
        if self.applicable and not self.expected_impact_paths and not self.mandatory_guardrail:
            raise ValueError("applicable non-guardrail module requires expected impact paths")


@dataclass(frozen=True)
class ModuleAblationObservation:
    module_id: str
    status: AblationStatus
    assessment: ModuleImpactAssessment | None
    effort: ResearchEffort
    applicable: bool
    mandatory_guardrail: bool
    note: str = ""


@dataclass(frozen=True)
class JointAblationObservation:
    group_id: str
    module_ids: tuple[str, ...]
    status: AblationStatus
    assessment: ModuleImpactAssessment | None
    note: str = ""


@dataclass(frozen=True)
class ResearchLoadoutRecommendation:
    module_id: str
    intensity: ResearchIntensity
    action: LoadoutAction
    rationale: str


@dataclass(frozen=True)
class AblationBatchResult:
    baseline: DecisionOutcome
    module_observations: tuple[ModuleAblationObservation, ...]
    joint_observations: tuple[JointAblationObservation, ...]
    loadout_recommendations: tuple[ResearchLoadoutRecommendation, ...]

    @property
    def measured_modules(self) -> tuple[str, ...]:
        return tuple(
            item.module_id
            for item in self.module_observations
            if item.status is AblationStatus.MEASURED
        )


CounterfactualRunner = Callable[[str], DecisionOutcome]
JointCounterfactualRunner = Callable[[tuple[str, ...]], DecisionOutcome]
GuardrailProbe = Callable[[str], bool]


def _loadout_action(
    *,
    intensity: ResearchIntensity,
    mandatory_guardrail: bool,
    trigger_active: bool,
) -> tuple[LoadoutAction, str]:
    if mandatory_guardrail or intensity is ResearchIntensity.KEEP_GUARDRAIL:
        return LoadoutAction.KEEP_GUARDRAIL, "mandatory guardrail remains in every applicable run"
    if intensity is ResearchIntensity.ALWAYS:
        return LoadoutAction.KEEP_ALWAYS, "repeated material impact supports mandatory deployment"
    if intensity is ResearchIntensity.CONDITIONAL:
        if trigger_active:
            return LoadoutAction.ACTIVATE_IF_TRIGGERED, "conditional module is active because its trigger is present"
        return LoadoutAction.NO_CHANGE, "conditional module remains dormant until its trigger is present"
    if intensity is ResearchIntensity.SAMPLE_ONLY:
        return LoadoutAction.SAMPLE, "retain occasional sampling until evidence is sufficient"
    if intensity is ResearchIntensity.RETIRE_CANDIDATE:
        return LoadoutAction.PROPOSE_DOWNRANK, "repeated high-cost low-impact observations justify governance review"
    return LoadoutAction.NO_CHANGE, "no loadout change recommended"


def build_loadout_recommendations(
    observations: tuple[ModuleAblationObservation, ...],
    *,
    prior_history: dict[str, tuple[ModuleHistoryEntry, ...]] | None = None,
    trigger_state: dict[str, bool] | None = None,
    policy: ImpactPolicy | None = None,
) -> tuple[ResearchLoadoutRecommendation, ...]:
    """Build next-run loadout proposals without mutating canonical requirements."""
    prior_history = prior_history or {}
    trigger_state = trigger_state or {}
    recommendations: list[ResearchLoadoutRecommendation] = []

    for observation in observations:
        if not observation.applicable:
            continue
        history = list(prior_history.get(observation.module_id, ()))
        if observation.assessment is not None:
            history.append(
                ModuleHistoryEntry(
                    assessment=observation.assessment,
                    effort=observation.effort,
                    applicable=True,
                    research_performed=True,
                    mandatory_guardrail=observation.mandatory_guardrail,
                )
            )
        if not history:
            continue
        intensity = recommend_research_intensity(tuple(history), policy=policy)
        action, rationale = _loadout_action(
            intensity=intensity,
            mandatory_guardrail=observation.mandatory_guardrail,
            trigger_active=trigger_state.get(observation.module_id, True),
        )
        recommendations.append(
            ResearchLoadoutRecommendation(
                module_id=observation.module_id,
                intensity=intensity,
                action=action,
                rationale=rationale,
            )
        )
    return tuple(recommendations)


def run_module_ablations(
    *,
    baseline: DecisionOutcome,
    specs: tuple[ModuleAblationSpec, ...],
    run_without_module: CounterfactualRunner,
    guardrail_probe: GuardrailProbe | None = None,
    prior_history: dict[str, tuple[ModuleHistoryEntry, ...]] | None = None,
    policy: ImpactPolicy | None = None,
) -> AblationBatchResult:
    """Run leave-one-module-out counterfactuals against one evidence snapshot.

    The supplied runner must keep all unrelated evidence and assumptions fixed. This layer
    measures impact only; it never rewrites the current run or canonical loadout.
    """
    seen: set[str] = set()
    observations: list[ModuleAblationObservation] = []

    for spec in specs:
        if spec.module_id in seen:
            raise ValueError(f"duplicate ablation module_id: {spec.module_id}")
        seen.add(spec.module_id)

        if not spec.applicable:
            observations.append(
                ModuleAblationObservation(
                    module_id=spec.module_id,
                    status=AblationStatus.NOT_APPLICABLE,
                    assessment=None,
                    effort=spec.research_effort,
                    applicable=False,
                    mandatory_guardrail=spec.mandatory_guardrail,
                    note="module not applicable to this run",
                )
            )
            continue

        if not spec.counterfactual_supported:
            observations.append(
                ModuleAblationObservation(
                    module_id=spec.module_id,
                    status=AblationStatus.NOT_MEASURABLE,
                    assessment=None,
                    effort=spec.research_effort,
                    applicable=True,
                    mandatory_guardrail=spec.mandatory_guardrail,
                    note="counterfactual adapter not implemented",
                )
            )
            continue

        try:
            counterfactual = run_without_module(spec.module_id)
            violation = guardrail_probe(spec.module_id) if guardrail_probe is not None else False
            assessment = compare_module_counterfactual(
                spec.module_id,
                baseline=baseline,
                counterfactual=counterfactual,
                guardrail_violation_detected=violation,
                policy=policy,
            )
            observations.append(
                ModuleAblationObservation(
                    module_id=spec.module_id,
                    status=AblationStatus.MEASURED,
                    assessment=assessment,
                    effort=spec.research_effort,
                    applicable=True,
                    mandatory_guardrail=spec.mandatory_guardrail,
                )
            )
        except Exception as exc:
            observations.append(
                ModuleAblationObservation(
                    module_id=spec.module_id,
                    status=AblationStatus.FAILED,
                    assessment=None,
                    effort=spec.research_effort,
                    applicable=True,
                    mandatory_guardrail=spec.mandatory_guardrail,
                    note=f"{type(exc).__name__}: {exc}",
                )
            )

    recommendations = build_loadout_recommendations(
        tuple(observations),
        prior_history=prior_history,
        trigger_state={spec.module_id: spec.trigger_active for spec in specs},
        policy=policy,
    )
    return AblationBatchResult(
        baseline=baseline,
        module_observations=tuple(observations),
        joint_observations=(),
        loadout_recommendations=recommendations,
    )


def run_joint_ablations(
    batch: AblationBatchResult,
    *,
    specs: tuple[ModuleAblationSpec, ...],
    run_without_modules: JointCounterfactualRunner,
    policy: ImpactPolicy | None = None,
) -> AblationBatchResult:
    """Test correlated modules together before interpreting leave-one-out zeros as waste."""
    groups: dict[str, list[str]] = {}
    for spec in specs:
        if spec.applicable and spec.correlation_group:
            groups.setdefault(spec.correlation_group, []).append(spec.module_id)

    joint: list[JointAblationObservation] = []
    for group_id, module_ids in sorted(groups.items()):
        unique = tuple(sorted(set(module_ids)))
        if len(unique) < 2:
            continue
        try:
            counterfactual = run_without_modules(unique)
            assessment = compare_module_counterfactual(
                f"JOINT:{group_id}",
                baseline=batch.baseline,
                counterfactual=counterfactual,
                policy=policy,
            )
            joint.append(
                JointAblationObservation(
                    group_id=group_id,
                    module_ids=unique,
                    status=AblationStatus.MEASURED,
                    assessment=assessment,
                )
            )
        except Exception as exc:
            joint.append(
                JointAblationObservation(
                    group_id=group_id,
                    module_ids=unique,
                    status=AblationStatus.FAILED,
                    assessment=None,
                    note=f"{type(exc).__name__}: {exc}",
                )
            )

    return AblationBatchResult(
        baseline=batch.baseline,
        module_observations=batch.module_observations,
        joint_observations=tuple(joint),
        loadout_recommendations=batch.loadout_recommendations,
    )


def retirement_proposals_allowed(batch: AblationBatchResult) -> tuple[str, ...]:
    """Return governance-review candidates, never automatic removals.

    If a joint correlation-group ablation is material, no member is retired solely from a
    leave-one-out zero because a sibling may be carrying the same economic path.
    """
    material_joint_members = {
        module_id
        for joint in batch.joint_observations
        if joint.assessment is not None and joint.assessment.material
        for module_id in joint.module_ids
    }
    return tuple(
        rec.module_id
        for rec in batch.loadout_recommendations
        if rec.action is LoadoutAction.PROPOSE_DOWNRANK and rec.module_id not in material_joint_members
    )
