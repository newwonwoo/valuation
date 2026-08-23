from __future__ import annotations

from pathlib import Path

from .ablation import ResearchLoadoutRecommendation
from .adaptive_loadout import build_adaptive_research_loadout
from .control_plane import StageStatus
from .industry_dna import IndustryDNAProfile
from .module_plan import build_module_requirement_plan
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult


def module_requirement_plan_adapter(
    *,
    registry_path: str | Path,
    control_requirements_path: str | Path,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        profiles = context.data.get("industry_dna_profiles")
        if not isinstance(profiles, tuple) or not profiles or not all(
            isinstance(item, IndustryDNAProfile) for item in profiles
        ):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "typed IndustryDNA profiles missing before Module Requirement Plan",
                blocking=True,
            )
        recommendations = context.data.get("prior_research_loadout_recommendations", ())
        if not isinstance(recommendations, tuple) or not all(
            isinstance(item, ResearchLoadoutRecommendation) for item in recommendations
        ):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "prior research loadout recommendations have invalid type",
                blocking=True,
            )
        optional_units = context.data.get("optional_research_units", ())
        trigger_state = context.data.get("research_trigger_state", {})
        aliases = context.data.get("research_unit_aliases", {})
        if not isinstance(optional_units, tuple) or not all(isinstance(item, str) for item in optional_units):
            return StageExecutionResult(StageStatus.BLOCKED, "optional_research_units must be a string tuple", blocking=True)
        if not isinstance(trigger_state, dict) or not all(isinstance(k, str) and isinstance(v, bool) for k, v in trigger_state.items()):
            return StageExecutionResult(StageStatus.BLOCKED, "research_trigger_state must be str→bool", blocking=True)
        if not isinstance(aliases, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in aliases.items()):
            return StageExecutionResult(StageStatus.BLOCKED, "research_unit_aliases must be str→str", blocking=True)
        try:
            plan = build_module_requirement_plan(
                profiles,
                registry_path=registry_path,
                control_requirements_path=control_requirements_path,
            )
            loadout = build_adaptive_research_loadout(
                plan,
                recommendations=recommendations,
                optional_units=optional_units,
                trigger_state=trigger_state,
                unit_aliases=aliases,
            )
            expected_modules = tuple(
                dict.fromkeys(
                    (
                        *plan.common_core_modules,
                        *(archetype for segment in plan.segments for archetype in segment.archetypes),
                    )
                )
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"Module Requirement Plan compilation failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "compiled canonical Module Requirement Plan and non-destructive learned research loadout",
            {
                "module_requirement_plan": plan,
                "required_evidence": plan.required_evidence,
                "required_kpis": plan.required_kpis,
                "mandatory_scanners": plan.mandatory_scanners,
                "kill_conditions": plan.kill_conditions,
                "scenario_variables": plan.scenario_variables,
                "expected_module_ids": expected_modules,
                "adaptive_research_loadout": loadout,
                "mandatory_research_units": loadout.mandatory_units,
                "active_research_units": loadout.active_units,
                "conditional_research_units": loadout.conditional_units,
                "sample_research_units": loadout.sample_units,
                "research_governance_review_units": loadout.governance_review_units,
            },
        )

    return run
