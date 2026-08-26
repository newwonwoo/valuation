from __future__ import annotations

from dataclasses import dataclass

from .ablation import (
    LoadoutAction,
    ResearchLoadoutRecommendation,
)
from .decision_impact import ResearchIntensity
from .module_plan import ModuleRequirementPlan


@dataclass(frozen=True)
class AdaptiveResearchLoadout:
    mandatory_units: tuple[str, ...]
    active_units: tuple[str, ...]
    conditional_units: tuple[str, ...]
    sample_units: tuple[str, ...]
    governance_review_units: tuple[str, ...]
    unchanged_units: tuple[str, ...]
    recommendations: tuple[ResearchLoadoutRecommendation, ...]

    def validate(self) -> None:
        mandatory = set(self.mandatory_units)
        if not mandatory.issubset(self.active_units):
            raise ValueError("adaptive loadout may not remove a canonical mandatory unit")
        disjoint = (
            set(self.conditional_units),
            set(self.sample_units),
            set(self.unchanged_units),
        )
        if any(mandatory.intersection(bucket) for bucket in disjoint):
            raise ValueError("mandatory units cannot be deferred, sampled or left unresolved")
        if set(self.conditional_units).intersection(self.sample_units):
            raise ValueError("unit cannot be both conditional and sampled")


def _ordered_unique(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


def build_adaptive_research_loadout(
    plan: ModuleRequirementPlan,
    *,
    recommendations: tuple[ResearchLoadoutRecommendation, ...] = (),
    optional_units: tuple[str, ...] = (),
    trigger_state: dict[str, bool] | None = None,
    unit_aliases: dict[str, str] | None = None,
) -> AdaptiveResearchLoadout:
    """Apply learned deployment policy without mutating canonical research requirements.

    A recommendation may change scheduling intensity only. Mandatory common-core modules and
    scanners remain active even when history proposes down-ranking; such cases are sent to
    governance review rather than silently removed.
    """
    plan.validate()
    trigger_state = trigger_state or {}
    aliases = unit_aliases or {}
    mandatory_units = _ordered_unique(plan.common_core_modules + plan.mandatory_scanners)
    mandatory_keys = {item.casefold(): item for item in mandatory_units}

    active = list(mandatory_units)
    conditional: list[str] = []
    sample: list[str] = []
    governance: list[str] = []
    unchanged: list[str] = []
    seen_recommendations: set[str] = set()

    def resolve(module_id: str) -> str:
        return aliases.get(module_id, module_id)

    for recommendation in recommendations:
        module_id = resolve(recommendation.module_id)
        key = module_id.casefold()
        if key in seen_recommendations:
            raise ValueError(f"duplicate adaptive recommendation for {module_id}")
        seen_recommendations.add(key)
        mandatory_name = mandatory_keys.get(key)
        if mandatory_name is not None:
            if mandatory_name not in active:
                active.append(mandatory_name)
            if recommendation.action is LoadoutAction.PROPOSE_DOWNRANK:
                governance.append(mandatory_name)
            continue

        if recommendation.action in {LoadoutAction.KEEP_ALWAYS, LoadoutAction.KEEP_GUARDRAIL}:
            active.append(module_id)
        elif recommendation.action is LoadoutAction.ACTIVATE_IF_TRIGGERED:
            if trigger_state.get(module_id, trigger_state.get(recommendation.module_id, False)):
                active.append(module_id)
            else:
                conditional.append(module_id)
        elif recommendation.action is LoadoutAction.SAMPLE:
            sample.append(module_id)
        elif recommendation.action is LoadoutAction.PROPOSE_DOWNRANK:
            governance.append(module_id)
        elif (
            recommendation.action is LoadoutAction.NO_CHANGE
            and recommendation.intensity is ResearchIntensity.CONDITIONAL
        ):
            conditional.append(module_id)
        else:
            unchanged.append(module_id)

    recommended_keys = seen_recommendations
    for module_id in optional_units:
        resolved = resolve(module_id)
        if resolved.casefold() not in mandatory_keys and resolved.casefold() not in recommended_keys:
            unchanged.append(resolved)

    result = AdaptiveResearchLoadout(
        mandatory_units=mandatory_units,
        active_units=_ordered_unique(active),
        conditional_units=_ordered_unique(conditional),
        sample_units=_ordered_unique(sample),
        governance_review_units=_ordered_unique(governance),
        unchanged_units=_ordered_unique(unchanged),
        recommendations=recommendations,
    )
    result.validate()
    return result
