from __future__ import annotations

import pytest

from valuation_engine.module_plan import ModuleRequirementPlan, SegmentModuleRequirementPlan
from valuation_engine.rocket_context_engine import build_rocket_context_plan


def _plan() -> ModuleRequirementPlan:
    segment = SegmentModuleRequirementPlan(
        segment_id="memory",
        sector_adapter="semiconductor",
        archetypes=("capacity_manufacturing",),
        required_evidence=("capacity",),
        required_kpis=("utilization",),
        mandatory_scanners=("CAPACITY_RAMP", "UTILIZATION"),
        kill_conditions=("ramp fails",),
        normalization_rules=("normalize utilization",),
        beta_peer_features=("cyclicality",),
        per_peer_features=("mix",),
        scenario_variables=("capacity", "utilization"),
        funding_scans=(),
        terminal_policies=("normalize",),
        double_count_traps=("capacity option",),
        forbidden_methods=("peak_margin_perpetuity",),
        allowed_valuation_methods=("driver_dcf",),
        optional_scanners=("QUALIFICATION",),
    )
    result = ModuleRequirementPlan(
        segments=(segment,),
        common_core_modules=("evidence_gate",),
        required_evidence=("capacity",),
        required_kpis=("utilization",),
        mandatory_scanners=("CAPACITY_RAMP", "UTILIZATION"),
        kill_conditions=("ramp fails",),
        scenario_variables=("capacity", "utilization"),
        double_count_traps=("capacity option",),
        forbidden_methods=("peak_margin_perpetuity",),
        optional_scanners=("QUALIFICATION",),
    )
    result.validate()
    return result


def test_rocket_context_plan_is_compiled_from_typed_module_plan():
    result = build_rocket_context_plan(
        target_id="000660",
        module_plan=_plan(),
        active_optional_scanners=("QUALIFICATION",),
    )
    result.validate()
    assert result.mandatory_scanners == ("CAPACITY_RAMP", "UTILIZATION")
    assert result.ordered_scanners == (
        "CAPACITY_RAMP",
        "UTILIZATION",
        "QUALIFICATION",
    )
    assert result.plan_hash


def test_llm_or_external_caller_cannot_activate_undeclared_scanner():
    with pytest.raises(PermissionError, match="outside canonical"):
        build_rocket_context_plan(
            target_id="000660",
            module_plan=_plan(),
            active_optional_scanners=("POLITICAL_SCANNER_NOT_IN_PLAN",),
        )
