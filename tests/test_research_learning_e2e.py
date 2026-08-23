from pathlib import Path

from valuation_engine.ablation import ModuleAblationSpec, run_module_ablations
from valuation_engine.control_plane import ExecutionMode
from valuation_engine.decision_impact import DecisionOutcome
from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile
from valuation_engine.module_plan_adapter import module_requirement_plan_adapter
from valuation_engine.orchestrator import run_controlled_workflow
from valuation_engine.research_learning import ResearchLearningStore
from valuation_engine.state_learning_adapter import load_research_learning_adapter


ROOT = Path(__file__).resolve().parents[1]


def profile():
    return IndustryDNAProfile(
        segment_id="transformers",
        sector_adapter="power.transformer_switchgear",
        archetypes=(EconomicArchetype.CONTRACTED_BACKLOG,),
        revenue_recognition="delivery",
        price_formation="negotiated",
        asset_ownership="manufacturer",
        capital_intensity="high",
        regulation_intensity="medium",
        customer_structure="utilities",
        reinvestment_model="capacity_expansion",
        cashflow_duration="multi_year",
        evidence_keys=("EV1",),
    )


def prior_batch():
    baseline = DecisionOutcome(status="COMPLETED", intrinsic_value_per_share=100.0)
    return run_module_ablations(
        baseline=baseline,
        specs=(
            ModuleAblationSpec(
                "PATENT_SIGNAL",
                expected_impact_paths=("technology_risk",),
            ),
        ),
        run_without_module=lambda _: DecisionOutcome(
            status="COMPLETED",
            intrinsic_value_per_share=80.0,
        ),
    )


def test_prior_run_impact_is_loaded_and_changes_next_run_optional_research_loadout(tmp_path):
    store = ResearchLearningStore(tmp_path)
    store.save_batch(ticker="TEST", run_id="R0", batch=prior_batch())

    result = run_controlled_workflow(
        run_id="R1",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("LOAD_COMPANY_STATE", "MODULE_REQUIREMENT_PLAN"),
        adapters={
            "LOAD_COMPANY_STATE": load_research_learning_adapter(store=store),
            "MODULE_REQUIREMENT_PLAN": module_requirement_plan_adapter(
                registry_path=ROOT / "config" / "archetype_module_registry.yaml",
                control_requirements_path=ROOT / "config" / "archetype_control_requirements.yaml",
            ),
        },
        required_stages=("LOAD_COMPANY_STATE", "MODULE_REQUIREMENT_PLAN"),
        initial_data={
            "ticker": "TEST",
            "industry_dna_profiles": (profile(),),
            "optional_research_units": ("PATENT_SIGNAL",),
        },
    )

    assert result.blocked_reasons == ()
    assert result.data["research_learning_record_count"] == 1
    assert result.data["prior_research_loadout_recommendations"][0].module_id == "PATENT_SIGNAL"
    assert "PATENT_SIGNAL" in result.data["adaptive_research_loadout"].active_units
