from __future__ import annotations

from dataclasses import replace

from .scenario_binding import BoundScenarioSet
from .valuation_execution import (
    CompanyValuationPlan,
    SegmentValuationPlan,
    UnvaluedSegment,
)
from .valuation_plan_compiler import (
    CompanyValuationPlanInputs,
    ValuationPlanCompilation,
    ValuationPlanStatus,
)


_PARTIAL_ELIGIBLE_GAPS = {
    ValuationPlanStatus.ASSUMPTION_GAP,
    ValuationPlanStatus.CAPABILITY_GAP,
}


def promote_partial_valuation_plan(
    compilation: ValuationPlanCompilation,
    *,
    inputs: CompanyValuationPlanInputs,
    scenario_set: BoundScenarioSet,
) -> ValuationPlanCompilation:
    """Attach an executable PARTIAL_INTRINSIC plan when only segment-local gaps remain.

    The original compilation status and every SegmentPlanResolution are preserved. Promotion
    never converts an unresolved segment into zero. It only constructs a plan from READY
    segments and records every unresolved segment as UNVALUED_NOT_ZERO.
    """
    if compilation.ready:
        return compilation
    if compilation.plan is not None:
        raise ValueError("unresolved valuation compilation unexpectedly already has a plan")
    if any(
        item.status is ValuationPlanStatus.METHOD_CHOICE_REQUIRED
        for item in compilation.segment_resolutions
    ):
        return compilation

    ready_resolutions = tuple(
        item
        for item in compilation.segment_resolutions
        if item.status is ValuationPlanStatus.READY
        and item.selected_model_key is not None
    )
    unresolved = tuple(
        item
        for item in compilation.segment_resolutions
        if item.status is not ValuationPlanStatus.READY
    )
    if not ready_resolutions or not unresolved:
        return compilation
    if any(item.status not in _PARTIAL_ELIGIBLE_GAPS for item in unresolved):
        return compilation

    valued_segments = tuple(
        SegmentValuationPlan(
            asset_id=inputs.binding_for(item.segment_id).asset_id,
            segment_id=item.segment_id,
            model_key=item.selected_model_key,
            ownership_key=inputs.binding_for(item.segment_id).ownership_key,
            ev_to_equity_adjustment_key=(
                inputs.binding_for(item.segment_id).ev_to_equity_adjustment_key
            ),
        )
        for item in ready_resolutions
    )
    unresolved_missing = tuple(
        dict.fromkeys(
            value
            for item in unresolved
            for value in item.missing_assumptions
        )
    )
    missing_common = _missing_common_assumptions(
        scenario_set,
        inputs=inputs,
        valued_segments=valued_segments,
    )
    if missing_common:
        return replace(
            compilation,
            missing_assumptions=tuple(
                dict.fromkeys((*unresolved_missing, *missing_common))
            ),
        )

    unvalued_segments = tuple(
        UnvaluedSegment(
            asset_id=inputs.binding_for(item.segment_id).asset_id,
            segment_id=item.segment_id,
            resolution_status=item.status.value,
            rationale=item.rationale,
            missing_assumptions=item.missing_assumptions,
        )
        for item in unresolved
    )
    plan = CompanyValuationPlan(
        segments=valued_segments,
        reporting_unit=inputs.reporting_unit,
        diluted_shares_key=inputs.diluted_shares_key,
        parent_adjustments=inputs.parent_adjustments,
        unvalued_segments=unvalued_segments,
    )
    plan.validate()
    return replace(
        compilation,
        plan=plan,
        missing_assumptions=unresolved_missing,
    )


def partial_plan_executable(compilation: ValuationPlanCompilation) -> bool:
    plan = compilation.plan
    return (
        isinstance(plan, CompanyValuationPlan)
        and bool(plan.unvalued_segments)
        and bool(plan.segments)
    )


def _missing_common_assumptions(
    scenario_set: BoundScenarioSet,
    *,
    inputs: CompanyValuationPlanInputs,
    valued_segments: tuple[SegmentValuationPlan, ...],
) -> tuple[str, ...]:
    required: list[str] = [inputs.diluted_shares_key]
    required.extend(item.ownership_key for item in valued_segments)
    required.extend(
        item.ev_to_equity_adjustment_key
        for item in valued_segments
        if item.ev_to_equity_adjustment_key is not None
    )
    required.extend(item.assumption_key for item in inputs.parent_adjustments)
    missing: list[str] = []
    for scenario in scenario_set.scenarios:
        for key in dict.fromkeys(required):
            try:
                scenario.get(key)
            except KeyError:
                missing.append(f"{scenario.scenario_id}/{key}")
    return tuple(dict.fromkeys(missing))
