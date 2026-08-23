from __future__ import annotations

from pathlib import Path

import pytest

from valuation_engine.decision_impact import ResearchEffort
from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile
from valuation_engine.module_requirements import (
    build_module_requirement_plan,
    build_module_requirement_plan_from_repo,
    experiment_specs_from_plan,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _profile(
    adapter: str,
    archetypes: tuple[EconomicArchetype, ...],
    *,
    segment_id: str = "segment",
) -> IndustryDNAProfile:
    return IndustryDNAProfile(
        segment_id=segment_id,
        sector_adapter=adapter,
        archetypes=archetypes,
        revenue_recognition="point_in_time_or_progress",
        price_formation="negotiated",
        asset_ownership="owned",
        capital_intensity="high",
        regulation_intensity="medium",
        customer_structure="concentrated",
        reinvestment_model="capacity_and_working_capital",
        cashflow_duration="multi_year",
        evidence_keys=("EV-ROUTE-1",),
    )


def test_power_transformer_plan_compiles_evidence_scanners_and_methods():
    plan = build_module_requirement_plan_from_repo(
        _profile(
            "power.transformer_switchgear",
            (
                EconomicArchetype.CONTRACTED_BACKLOG,
                EconomicArchetype.CAPACITY_MANUFACTURING,
            ),
            segment_id="transformers",
        ),
        repo_root=REPO_ROOT,
    )

    assert plan.segment_id == "transformers"
    assert {"backlog", "customer_advances", "effective_capacity", "utilization"}.issubset(
        set(plan.required_evidence)
    )
    assert {"BACKLOG_QUALITY", "CUSTOMER_ADVANCE", "CAPACITY_UTILIZATION"}.issubset(
        set(plan.mandatory_scanner_ids)
    )
    assert {"normalized_dcf", "driver_dcf", "warranted_per"}.issubset(
        set(plan.allowed_valuation_methods)
    )
    assert "SOURCE_FRESHNESS_PRECHECK" in plan.common_units
    assert plan.kill_conditions


def test_plan_rejects_archetype_not_permitted_by_sector_adapter():
    profile = _profile(
        "power.transformer_switchgear",
        (EconomicArchetype.PROBABILISTIC_PIPELINE,),
    )
    with pytest.raises(ValueError, match="does not permit archetypes"):
        build_module_requirement_plan_from_repo(profile, repo_root=REPO_ROOT)


def test_experiment_specs_preserve_interaction_groups_and_effort():
    plan = build_module_requirement_plan_from_repo(
        _profile(
            "software.saas",
            (EconomicArchetype.RECURRING_SUBSCRIPTION,),
        ),
        repo_root=REPO_ROOT,
    )
    specs = experiment_specs_from_plan(
        plan,
        effort_by_scanner={"RETENTION_CHURN": ResearchEffort(documents_reviewed=4)},
        condition_by_scanner={"SALES_EFFICIENCY": False},
        sample_due_by_scanner={"RPO_CONVERSION": False},
    )
    by_id = {spec.module_id: spec for spec in specs}
    assert by_id["RETENTION_CHURN"].interaction_group == "retention_economics"
    assert by_id["RETENTION_CHURN"].effort.documents_reviewed == 4
    assert not by_id["SALES_EFFICIENCY"].condition_met
    assert not by_id["RPO_CONVERSION"].sample_due
    assert all(not spec.mandatory_guardrail for spec in specs)


def test_missing_archetype_scanner_contract_fails_closed():
    profile = _profile(
        "test.adapter",
        (EconomicArchetype.CONTRACTED_BACKLOG,),
    )
    archetypes = {
        "modules": {
            "contracted_backlog": {
                "required_evidence": ["backlog"],
                "allowed_valuation_methods": ["normalized_dcf"],
            }
        }
    }
    adapters = {
        "adapters": {
            "test.adapter": {
                "default_archetypes": ["contracted_backlog"],
                "key_evidence": ["orders"],
            }
        }
    }
    with pytest.raises(ValueError, match="missing scanner map"):
        build_module_requirement_plan(
            profile,
            archetype_registry=archetypes,
            sector_adapter_registry=adapters,
            scanner_map={"archetype_scanners": {}, "common_units": []},
        )


def test_duplicate_scanner_from_two_archetypes_is_merged_not_double_deployed():
    profile = _profile(
        "test.adapter",
        (
            EconomicArchetype.CAPACITY_MANUFACTURING,
            EconomicArchetype.PROCESS_SPREAD,
        ),
    )
    archetypes = {
        "modules": {
            "capacity_manufacturing": {
                "required_evidence": ["capacity"],
                "allowed_valuation_methods": ["driver_dcf"],
            },
            "process_spread": {
                "required_evidence": ["spread"],
                "allowed_valuation_methods": ["spread_dcf"],
            },
        }
    }
    adapters = {
        "adapters": {
            "test.adapter": {
                "default_archetypes": ["capacity_manufacturing"],
                "optional_archetypes": ["process_spread"],
                "key_evidence": ["utilization"],
            }
        }
    }
    scanners = {
        "common_units": ["AUDIT_GATE"],
        "archetype_scanners": {
            "capacity_manufacturing": {
                "interaction_group": "operations",
                "scanners": [{"scanner_id": "CAPACITY", "mandatory": True}],
                "kill_conditions": ["capacity fails"],
            },
            "process_spread": {
                "interaction_group": "operations",
                "scanners": [{"scanner_id": "CAPACITY", "mandatory": False}],
                "kill_conditions": ["spread fails"],
            },
        },
        "risk_scanner_aliases": {},
    }
    plan = build_module_requirement_plan(
        profile,
        archetype_registry=archetypes,
        sector_adapter_registry=adapters,
        scanner_map=scanners,
    )
    assert tuple(scanner.scanner_id for scanner in plan.scanners) == ("CAPACITY",)
    assert plan.scanners[0].mandatory
    assert set(plan.scanners[0].origins) == {
        "archetype:capacity_manufacturing",
        "archetype:process_spread",
    }
