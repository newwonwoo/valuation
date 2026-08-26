import pytest

from valuation_engine.module_plan import (
    ModuleRequirementPlan,
    SegmentModuleRequirementPlan,
)
from valuation_engine.module_plan_adapter import extend_module_required_evidence


def plan() -> ModuleRequirementPlan:
    segment = SegmentModuleRequirementPlan(
        segment_id="core",
        sector_adapter="power.transformer_switchgear",
        archetypes=("capacity_manufacturing",),
        required_evidence=("capacity",),
        required_kpis=("capacity",),
        mandatory_scanners=("CAPACITY_RAMP",),
        kill_conditions=("ramp fails",),
        normalization_rules=("capacity normalization",),
        beta_peer_features=("fixed_cost",),
        per_peer_features=("incremental_roic",),
        scenario_variables=("capacity",),
        funding_scans=(),
        terminal_policies=("normalize",),
        double_count_traps=("growth_without_capex",),
        forbidden_methods=("peak_margin_perpetuity",),
        allowed_valuation_methods=("driver_dcf",),
    )
    return ModuleRequirementPlan(
        segments=(segment,),
        common_core_modules=("evidence_gate",),
        required_evidence=("capacity",),
        required_kpis=("capacity",),
        mandatory_scanners=("CAPACITY_RAMP",),
        kill_conditions=("ramp fails",),
        scenario_variables=("capacity",),
        double_count_traps=("growth_without_capex",),
        forbidden_methods=("peak_margin_perpetuity",),
    )


def test_company_specific_evidence_extends_but_never_replaces_canonical_floor():
    extended = extend_module_required_evidence(
        plan(),
        {"core": ("model_core_fcff_year_1", "beta_selection_L4")},
    )

    segment = extended.plan_for_segment("core")
    assert segment.required_evidence == (
        "capacity",
        "model_core_fcff_year_1",
        "beta_selection_L4",
    )
    assert segment.required_kpis == segment.required_evidence
    assert extended.required_evidence == segment.required_evidence
    assert extended.required_kpis == segment.required_kpis


def test_company_specific_evidence_rejects_unknown_segment():
    with pytest.raises(ValueError, match="unknown segments"):
        extend_module_required_evidence(
            plan(),
            {"not-a-segment": ("model_input",)},
        )


def test_company_specific_evidence_rejects_untyped_contract():
    with pytest.raises(TypeError, match="segment_id"):
        extend_module_required_evidence(
            plan(),
            {"core": ["model_input"]},  # type: ignore[arg-type]
        )
