from pathlib import Path

from valuation_engine.unit_contracts import (
    UnitContract,
    UnitContractRegistry,
    audit_expected_vs_actual_impact,
    load_unit_contract_registry,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "unit_contract_registry.yaml"


def _unit(unit_id: str, consumers: tuple[str, ...]) -> UnitContract:
    return UnitContract(
        unit_id=unit_id,
        unit_type="controller",
        implementation_status="implemented",
        stages=("TEST",),
        purpose="test dependency graph",
        inputs=("input",),
        outputs=(f"output:{unit_id}",),
        consumers=consumers,
        effect_types=("routing_effect",),
        final_outputs=("run_status",),
        canonical_refs=("tests/test_unit_contracts.py",),
        forbidden_effects=(),
    )


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


def test_transitive_dependency_queries_are_indexed_deterministic_and_cycle_safe():
    registry = UnitContractRegistry(
        version="test",
        units=(
            _unit("A", ("B",)),
            _unit("B", ("C",)),
            _unit("C", ("A", "USER")),
        ),
    )
    registry.validate()

    assert registry.forward_dependencies("A", transitive=True) == ("B", "C")
    assert registry.reverse_dependencies("A", transitive=True) == ("B", "C")
    assert "A" not in registry.forward_dependencies("A", transitive=True)
    assert "A" not in registry.reverse_dependencies("A", transitive=True)

    forward_index = registry._forward_known_index
    reverse_index = registry._reverse_index
    registry.forward_dependencies("A", transitive=True)
    registry.reverse_dependencies("A", transitive=True)
    assert registry._forward_known_index is forward_index
    assert registry._reverse_index is reverse_index


def test_direct_forward_query_preserves_virtual_consumer_and_declared_order():
    registry = UnitContractRegistry(
        version="test",
        units=(
            _unit("A", ("USER", "B")),
            _unit("B", ("USER",)),
        ),
    )
    registry.validate()
    assert registry.forward_dependencies("A") == ("USER", "B")
    assert registry.forward_dependencies("A", transitive=True) == ("B",)


def test_market_compare_is_post_freeze_only_by_dependency():
    registry = load_unit_contract_registry(REGISTRY)
    assert "MARKET_COMPARE" in registry.forward_dependencies("INTRINSIC_FREEZE")
    assert "MARKET_COMPARE" not in registry.forward_dependencies(
        "ASSUMPTION_COMPILER",
        transitive=False,
    )


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
