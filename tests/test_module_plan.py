from pathlib import Path

import pytest
import yaml

from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile
from valuation_engine.module_plan import build_module_requirement_plan


def profile(*archetypes: EconomicArchetype) -> IndustryDNAProfile:
    return IndustryDNAProfile(
        segment_id="transformers",
        sector_adapter="power.transformer_switchgear",
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


def root() -> Path:
    return Path(__file__).resolve().parents[1]


def registry_path() -> Path:
    return root() / "config" / "archetype_module_registry.yaml"


def control_path() -> Path:
    return root() / "config" / "archetype_control_requirements.yaml"


def build(*profiles: IndustryDNAProfile):
    return build_module_requirement_plan(
        tuple(profiles),
        registry_path=registry_path(),
        control_requirements_path=control_path(),
    )


def test_multi_archetype_plan_unions_complete_requirements_without_duplicates():
    plan = build(profile(EconomicArchetype.CONTRACTED_BACKLOG, EconomicArchetype.CAPACITY_MANUFACTURING))
    segment = plan.plan_for_segment("transformers")

    assert "backlog" in segment.required_evidence
    assert "effective_capacity" in segment.required_evidence
    assert "book_to_bill" in segment.required_kpis
    assert "CAPACITY_RAMP" in segment.mandatory_scanners
    assert any("backlog fails to convert" in item for item in segment.kill_conditions)
    assert "backlog_conversion" in segment.scenario_variables
    assert "utilization" in segment.scenario_variables
    assert "normalized_dcf" in segment.allowed_valuation_methods
    assert "driver_dcf" in segment.allowed_valuation_methods
    assert len(segment.required_evidence) == len(set(segment.required_evidence))
    assert len(segment.mandatory_scanners) == len(set(segment.mandatory_scanners))
    assert "intrinsic_value_freeze" in plan.common_core_modules


def test_control_requirements_cover_every_economic_archetype():
    payload = yaml.safe_load(control_path().read_text(encoding="utf-8"))
    assert set(payload["requirements"]) == {item.value for item in EconomicArchetype}
    for archetype, spec in payload["requirements"].items():
        assert spec["required_kpis"], archetype
        assert spec["mandatory_scanners"], archetype
        assert spec["kill_conditions"], archetype


def test_segment_plan_rejects_missing_archetype_registry_entry(tmp_path):
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "version: 1\nmodules: {contracted_backlog: {required_evidence: [backlog], allowed_valuation_methods: [normalized_dcf]}}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="capacity_manufacturing"):
        build_module_requirement_plan(
            (profile(EconomicArchetype.CAPACITY_MANUFACTURING),),
            registry_path=registry,
            control_requirements_path=control_path(),
        )


def test_segment_plan_rejects_missing_control_requirements(tmp_path):
    controls = tmp_path / "controls.yaml"
    controls.write_text(
        "version: 1\nrequirements: {contracted_backlog: {required_kpis: [backlog], mandatory_scanners: [BACKLOG], kill_conditions: [break]}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="capacity_manufacturing"):
        build_module_requirement_plan(
            (profile(EconomicArchetype.CAPACITY_MANUFACTURING),),
            registry_path=registry_path(),
            control_requirements_path=controls,
        )


def test_duplicate_segment_ids_are_rejected():
    p = profile(EconomicArchetype.CONTRACTED_BACKLOG)
    with pytest.raises(ValueError, match="duplicate segment"):
        build(p, p)
