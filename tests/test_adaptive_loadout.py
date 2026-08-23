from pathlib import Path

from valuation_engine.ablation import LoadoutAction, ResearchLoadoutRecommendation
from valuation_engine.adaptive_loadout import build_adaptive_research_loadout
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.decision_impact import ResearchIntensity
from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile
from valuation_engine.module_plan import build_module_requirement_plan
from valuation_engine.module_plan_adapter import module_requirement_plan_adapter
from valuation_engine.orchestrator import run_controlled_workflow


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "archetype_module_registry.yaml"
CONTROLS = ROOT / "config" / "archetype_control_requirements.yaml"


def profile():
    return IndustryDNAProfile(
        segment_id="transformers",
        sector_adapter="power.transformer_switchgear",
        archetypes=(EconomicArchetype.CONTRACTED_BACKLOG, EconomicArchetype.CAPACITY_MANUFACTURING),
        revenue_recognition="delivery",
        price_formation="negotiated",
        asset_ownership="manufacturer",
        capital_intensity="high",
        regulation_intensity="medium",
        customer_structure="utilities_and_datacenters",
        reinvestment_model="capacity_expansion",
        cashflow_duration="multi_year",
        evidence_keys=("EV1",),
    )


def plan():
    return build_module_requirement_plan(
        (profile(),),
        registry_path=REGISTRY,
        control_requirements_path=CONTROLS,
    )


def recommendations():
    return (
        ResearchLoadoutRecommendation(
            "CAPACITY_RAMP",
            ResearchIntensity.RETIRE_CANDIDATE,
            LoadoutAction.PROPOSE_DOWNRANK,
            "historical low impact",
        ),
        ResearchLoadoutRecommendation(
            "PATENT_SIGNAL",
            ResearchIntensity.SAMPLE_ONLY,
            LoadoutAction.SAMPLE,
            "retain occasional sample",
        ),
        ResearchLoadoutRecommendation(
            "POLITICAL_TIMING",
            ResearchIntensity.CONDITIONAL,
            LoadoutAction.ACTIVATE_IF_TRIGGERED,
            "activate only when political trigger exists",
        ),
        ResearchLoadoutRecommendation(
            "AUDIT_GATE",
            ResearchIntensity.KEEP_GUARDRAIL,
            LoadoutAction.KEEP_GUARDRAIL,
            "never remove guardrail",
        ),
    )


def test_learned_loadout_never_removes_canonical_mandatory_scanner():
    base = plan()
    loadout = build_adaptive_research_loadout(
        base,
        recommendations=recommendations(),
        optional_units=("UNRATED_SIGNAL",),
        trigger_state={"POLITICAL_TIMING": False},
    )
    assert "CAPACITY_RAMP" in base.mandatory_scanners
    assert "CAPACITY_RAMP" in loadout.active_units
    assert "CAPACITY_RAMP" in loadout.governance_review_units
    assert "PATENT_SIGNAL" in loadout.sample_units
    assert "POLITICAL_TIMING" in loadout.conditional_units
    assert "AUDIT_GATE" in loadout.active_units
    assert "UNRATED_SIGNAL" in loadout.unchanged_units


def test_conditional_research_becomes_active_only_when_current_trigger_is_present():
    loadout = build_adaptive_research_loadout(
        plan(),
        recommendations=recommendations(),
        trigger_state={"POLITICAL_TIMING": True},
    )
    assert "POLITICAL_TIMING" in loadout.active_units
    assert "POLITICAL_TIMING" not in loadout.conditional_units


def test_module_requirement_adapter_applies_prior_learning_without_mutating_base_contract():
    result = run_controlled_workflow(
        run_id="MODULE-LEARNING",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("MODULE_REQUIREMENT_PLAN",),
        adapters={
            "MODULE_REQUIREMENT_PLAN": module_requirement_plan_adapter(
                registry_path=REGISTRY,
                control_requirements_path=CONTROLS,
            )
        },
        required_stages=("MODULE_REQUIREMENT_PLAN",),
        initial_data={
            "industry_dna_profiles": (profile(),),
            "prior_research_loadout_recommendations": recommendations(),
            "research_trigger_state": {"POLITICAL_TIMING": False},
            "optional_research_units": ("PATENT_SIGNAL", "POLITICAL_TIMING"),
        },
    )
    assert result.blocked_reasons == ()
    assert result.stage_traces[0].status is StageStatus.PASS
    base = result.data["module_requirement_plan"]
    loadout = result.data["adaptive_research_loadout"]
    assert "CAPACITY_RAMP" in base.mandatory_scanners
    assert "CAPACITY_RAMP" in loadout.active_units
    assert "CAPACITY_RAMP" in loadout.governance_review_units
