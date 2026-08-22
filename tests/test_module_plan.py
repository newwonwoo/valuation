from pathlib import Path

import pytest

from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile
from valuation_engine.module_plan import build_module_requirement_plan


def profile(*archetypes: EconomicArchetype) -> IndustryDNAProfile:
    return IndustryDNAProfile(
        segment_id="transformers",
        sector_adapter="power_equipment",
        archetypes=archetypes,
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


def registry_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "archetype_module_registry.yaml"


def test_multi_archetype_plan_unions_requirements_without_duplicate_paths():
    plan = build_module_requirement_plan(
        (profile(EconomicArchetype.CONTRACTED_BACKLOG, EconomicArchetype.CAPACITY_MANUFACTURING),),
        registry_path=registry_path(),
    )
    segment = plan.plan_for_segment("transformers")

    assert "backlog" in segment.required_evidence
    assert "effective_capacity" in segment.required_evidence
    assert "backlog_conversion" in segment.scenario_variables
    assert "utilization" in segment.scenario_variables
    assert "normalized_dcf" in segment.allowed_valuation_methods
    assert "driver_dcf" in segment.allowed_valuation_methods
    assert len(segment.required_evidence) == len(set(segment.required_evidence))
    assert "intrinsic_value_freeze" in plan.common_core_modules


def test_segment_plan_rejects_missing_archetype_registry_entry(tmp_path):
    registry = tmp_path / "registry.yaml"
    registry.write_text("version: 1\nmodules: {contracted_backlog: {required_evidence: [backlog], allowed_valuation_methods: [normalized_dcf]}}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="capacity_manufacturing"):
        build_module_requirement_plan(
            (profile(EconomicArchetype.CAPACITY_MANUFACTURING),),
            registry_path=registry,
        )


def test_duplicate_segment_ids_are_rejected():
    p = profile(EconomicArchetype.CONTRACTED_BACKLOG)
    with pytest.raises(ValueError, match="duplicate segment"):
        build_module_requirement_plan((p, p), registry_path=registry_path())
