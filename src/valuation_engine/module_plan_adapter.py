from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Mapping

from .ablation import ResearchLoadoutRecommendation
from .adaptive_loadout import build_adaptive_research_loadout
from .control_plane import StageStatus
from .industry_dna import IndustryDNAProfile
from .module_plan import (
    ModuleRequirementPlan,
    build_module_requirement_plan,
)
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult


def _ordered_unique(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


def extend_module_required_evidence(
    plan: ModuleRequirementPlan,
    additions: Mapping[str, tuple[str, ...]] | None,
) -> ModuleRequirementPlan:
    """Add a typed company-specific Evidence contract without weakening canonical routing.

    The canonical archetype plan remains the floor. Company/provider-specific model inputs,
    peer-selection Evidence or source-backed normalization facts must be declared here before
    the collection planner can authorize a collector to emit them.
    """
    if not additions:
        return plan
    if not isinstance(additions, Mapping) or not all(
        isinstance(segment_id, str)
        and segment_id
        and isinstance(metrics, tuple)
        and all(isinstance(metric, str) and metric for metric in metrics)
        for segment_id, metrics in additions.items()
    ):
        raise TypeError(
            "additional_required_evidence must be a segment_id→tuple[str, ...] mapping"
        )

    known_segments = {segment.segment_id for segment in plan.segments}
    unknown_segments = tuple(sorted(set(additions) - known_segments))
    if unknown_segments:
        raise ValueError(
            "additional Evidence references unknown segments: "
            + ", ".join(unknown_segments)
        )

    segments = tuple(
        replace(
            segment,
            required_evidence=_ordered_unique(
                (*segment.required_evidence, *additions.get(segment.segment_id, ()))
            ),
            required_kpis=_ordered_unique(
                (*segment.required_kpis, *additions.get(segment.segment_id, ()))
            ),
        )
        for segment in plan.segments
    )
    result = replace(
        plan,
        segments=segments,
        required_evidence=_ordered_unique(
            metric for segment in segments for metric in segment.required_evidence
        ),
        required_kpis=_ordered_unique(
            metric for segment in segments for metric in segment.required_kpis
        ),
    )
    result.validate()
    return result


def _auto_method_evidence(
    context: OrchestratorContext,
    plan: ModuleRequirementPlan,
) -> Mapping[str, tuple[str, ...]] | None:
    """Resolve candidate-method evidence only after canonical Industry DNA routing.

    This is preparation, not method selection. The formal
    VALUATION_METHOD_INTENT stage still owns economic method resolution and
    fails closed when multiple implemented candidates remain.
    """
    from .auto_method_routing import (
        AUTO_METHOD_ROUTING_FLAG,
        AUTO_METHOD_ROUTING_FORECAST_YEARS,
        auto_required_evidence_map,
    )

    enabled = context.data.get(AUTO_METHOD_ROUTING_FLAG, False)
    if enabled is False:
        return None
    if enabled is not True:
        raise TypeError(f"{AUTO_METHOD_ROUTING_FLAG} must be bool")
    forecast_years = context.data.get(AUTO_METHOD_ROUTING_FORECAST_YEARS)
    if not isinstance(forecast_years, int) or isinstance(forecast_years, bool):
        raise TypeError(
            f"{AUTO_METHOD_ROUTING_FORECAST_YEARS} must be an integer"
        )
    return auto_required_evidence_map(
        plan,
        forecast_years=forecast_years,
    )


def module_requirement_plan_adapter(
    *,
    registry_path: str | Path,
    control_requirements_path: str | Path,
    additional_required_evidence: Mapping[str, tuple[str, ...]] | None = None,
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
        requested_optional_scanners = context.data.get("optional_scanner_ids", ())
        trigger_state = context.data.get("research_trigger_state", {})
        aliases = context.data.get("research_unit_aliases", {})
        if not isinstance(optional_units, tuple) or not all(isinstance(item, str) for item in optional_units):
            return StageExecutionResult(StageStatus.BLOCKED, "optional_research_units must be a string tuple", blocking=True)
        if not isinstance(requested_optional_scanners, tuple) or not all(
            isinstance(item, str) and item for item in requested_optional_scanners
        ):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "optional_scanner_ids must be a non-empty string tuple",
                blocking=True,
            )
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
            plan = extend_module_required_evidence(
                plan,
                _auto_method_evidence(context, plan),
            )
            plan = extend_module_required_evidence(
                plan,
                additional_required_evidence,
            )
            unknown_optional_scanners = tuple(
                scanner_id
                for scanner_id in dict.fromkeys(requested_optional_scanners)
                if scanner_id not in plan.optional_scanners
            )
            if unknown_optional_scanners:
                raise ValueError(
                    "optional scanner activation is outside the Module Requirement Plan: "
                    + ", ".join(unknown_optional_scanners)
                )
            active_optional_scanners = tuple(dict.fromkeys(requested_optional_scanners))
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
                "optional_scanners": plan.optional_scanners,
                "active_optional_scanners": active_optional_scanners,
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
