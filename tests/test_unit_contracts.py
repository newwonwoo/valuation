from pathlib import Path

from valuation_engine.unit_contracts import (
    audit_expected_vs_actual_impact,
    load_unit_contract_registry,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "unit_contract_registry.yaml"


def test_registry_loads_and_has_core_units():
    registry = load_unit_contract_registry(REGISTRY)
    ids = {unit.unit_id for unit in registry.units}
    for required in {
        "VALUATION_CONTROL_PLANE",
        "INDUSTRY_DNA_ROUTER",
        "ASSUMPTION_COMPILER",
        "WACC_ENGINE",
        "AUDIT_GATE",
        "INTRINSIC_FREEZE",
        "DECISION_IMPACT",
    }:
        assert required in ids


def test_forward_and_reverse_dependency_queries():
    registry = load_unit_contract_registry(REGISTRY)
    assert "WACC_ENGINE" in registry.forward_dependencies("HIERARCHICAL_BETA_ENGINE")
    upstream = registry.reverse_dependencies("WACC_ENGINE")
    assert "HIERARCHICAL_BETA_ENGINE" in upstream
    assert "UPSTREAM_FUNDING_SCAN" in upstream


def test_market_compare_is_post_freeze_only_by_dependency():
    registry = load_unit_contract_registry(REGISTRY)
    assert "MARKET_COMPARE" in registry.forward_dependencies("INTRINSIC_FREEZE")
    assert "MARKET_COMPARE" not in registry.forward_dependencies("ASSUMPTION_COMPILER", transitive=False)


def test_undeclared_effect_is_flagged():
    registry = load_unit_contract_registry(REGISTRY)
    findings = audit_expected_vs_actual_impact(
        registry,
        unit_id="BROKER_RESEARCH",
        actual_effect_types=("assumption_effect",),
        actual_connected=True,
    )
    assert findings == ("UNDECLARED_EFFECT:assumption_effect",)


def test_guardrail_can_have_zero_numeric_connection_without_waste_flag():
    registry = load_unit_contract_registry(REGISTRY)
    findings = audit_expected_vs_actual_impact(
        registry,
        unit_id="AUDIT_GATE",
        actual_effect_types=("guardrail_effect",),
        actual_connected=False,
    )
    assert findings == ()


def test_non_guardrail_disconnected_unit_is_flagged():
    registry = load_unit_contract_registry(REGISTRY)
    findings = audit_expected_vs_actual_impact(
        registry,
        unit_id="ROCKET_INSIGHT_SCAN",
        actual_effect_types=(),
        actual_connected=False,
    )
    assert findings == ("NO_ACTUAL_IMPACT_PATH",)
