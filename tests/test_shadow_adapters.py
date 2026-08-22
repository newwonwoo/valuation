from pathlib import Path

from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile
from valuation_engine.orchestrator import run_controlled_workflow
from valuation_engine.shadow_adapters import (
    company_resolution_adapter,
    industry_dna_adapter,
    load_company_state_adapter,
    module_requirement_plan_adapter,
)


def dna() -> IndustryDNAProfile:
    return IndustryDNAProfile(
        segment_id="transformers",
        sector_adapter="power_equipment",
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


def test_stage_ii_shadow_adapters_compile_real_module_requirement_plan(tmp_path):
    root = Path(__file__).resolve().parents[1]
    sequence = (
        "COMPANY_RESOLUTION",
        "LOAD_COMPANY_STATE",
        "INDUSTRY_DNA_ROUTE",
        "MODULE_REQUIREMENT_PLAN",
    )
    adapters = {
        "COMPANY_RESOLUTION": company_resolution_adapter(company="Example", ticker="EXM"),
        "LOAD_COMPANY_STATE": load_company_state_adapter(state_root=tmp_path),
        "INDUSTRY_DNA_ROUTE": industry_dna_adapter(profiles=(dna(),)),
        "MODULE_REQUIREMENT_PLAN": module_requirement_plan_adapter(
            registry_path=root / "config" / "archetype_module_registry.yaml"
        ),
    }

    result = run_controlled_workflow(
        run_id="SHADOW1",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=sequence,
        adapters=adapters,
        required_stages=sequence,
    )

    assert result.blocked_reasons == ()
    assert all(trace.status is StageStatus.PASS for trace in result.stage_traces)
    assert "backlog" in result.data["required_evidence"]
    assert "effective_capacity" in result.data["required_evidence"]
    assert "contracted_backlog" in result.data["expected_module_ids"]
    assert "capacity_manufacturing" in result.data["expected_module_ids"]


def test_module_plan_adapter_requests_recovery_without_industry_dna(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = run_controlled_workflow(
        run_id="SHADOW2",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("MODULE_REQUIREMENT_PLAN",),
        adapters={
            "MODULE_REQUIREMENT_PLAN": module_requirement_plan_adapter(
                registry_path=root / "config" / "archetype_module_registry.yaml"
            )
        },
        required_stages=("MODULE_REQUIREMENT_PLAN",),
    )

    assert result.blocked_reasons
    assert result.stage_traces[0].status is StageStatus.RECOVERY_REQUIRED
