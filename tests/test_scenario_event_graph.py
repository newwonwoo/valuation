from decimal import Decimal

import pytest

from valuation_engine.scenario_event_graph import (
    ScenarioDependenceContract,
    ScenarioDependenceMethod,
    ScenarioEventFactor,
    ScenarioEventGraph,
    ScenarioEventRule,
    ScenarioJointState,
    assemble_scenario_probabilities,
)


def factors(cert_b: str = "CERT-B"):
    return (
        ScenarioEventFactor("price_decline", Decimal("0.30"), "CERT-A"),
        ScenarioEventFactor("margin_compression", Decimal("0.40"), cert_b),
    )


def partition_graph(cert_b: str = "CERT-B") -> ScenarioEventGraph:
    return ScenarioEventGraph(
        factors=factors(cert_b),
        rules=(
            ScenarioEventRule(
                "Down",
                required_event_ids=("price_decline", "margin_compression"),
            ),
            ScenarioEventRule(
                "Core",
                forbidden_event_ids=("price_decline",),
            ),
            ScenarioEventRule(
                "Bull",
                required_event_ids=("price_decline",),
                forbidden_event_ids=("margin_compression",),
            ),
        ),
        dependence=ScenarioDependenceContract(
            method=ScenarioDependenceMethod.MUTUALLY_EXCLUSIVE_STATE_TABLE,
            version="state-table-v1",
            joint_states=(
                ScenarioJointState("neither", Decimal("0.50"), ()),
                ScenarioJointState(
                    "margin_only", Decimal("0.20"), ("margin_compression",)
                ),
                ScenarioJointState(
                    "price_only", Decimal("0.10"), ("price_decline",)
                ),
                ScenarioJointState(
                    "both",
                    Decimal("0.20"),
                    ("price_decline", "margin_compression"),
                ),
            ),
        ),
    )


def test_state_table_produces_exhaustive_point_probabilities():
    assembly = assemble_scenario_probabilities(partition_graph())
    assert assembly.numeric_weighting_allowed
    points = {item.scenario_id: item.point for item in assembly.estimates}
    assert points == {
        "Down": Decimal("0.20"),
        "Core": Decimal("0.70"),
        "Bull": Decimal("0.10"),
    }
    certificate = assembly.certificate(cohort_key="scenario_assembly|memory|12m")
    certificate.validate_for_weighting()
    assert sum(
        (value for _, value in certificate.scenario_probabilities), Decimal("0")
    ) == Decimal("1")


def test_frechet_bounds_never_fabricate_point_weights():
    graph = ScenarioEventGraph(
        factors=factors(),
        rules=(
            ScenarioEventRule(
                "stress",
                required_event_ids=("price_decline", "margin_compression"),
            ),
        ),
        dependence=ScenarioDependenceContract(
            method=ScenarioDependenceMethod.FRECHET_BOUNDS,
            version="frechet-v1",
        ),
    )
    assembly = assemble_scenario_probabilities(graph)
    assert not assembly.numeric_weighting_allowed
    estimate = assembly.estimates[0]
    assert estimate.lower == Decimal("0")
    assert estimate.upper == Decimal("0.30")
    assert estimate.point is None
    with pytest.raises(PermissionError, match="only probability bounds"):
        assembly.certificate(cohort_key="scenario_assembly|stress")


def test_naive_independence_is_not_a_supported_dependence_method():
    with pytest.raises(ValueError):
        ScenarioDependenceMethod("independent")


def test_joint_state_must_map_to_exactly_one_scenario():
    graph = ScenarioEventGraph(
        factors=factors(),
        rules=(
            ScenarioEventRule("A", forbidden_event_ids=("price_decline",)),
            ScenarioEventRule("B", forbidden_event_ids=("margin_compression",)),
        ),
        dependence=ScenarioDependenceContract(
            method=ScenarioDependenceMethod.MUTUALLY_EXCLUSIVE_STATE_TABLE,
            version="state-table-v1",
            joint_states=(ScenarioJointState("neither", Decimal("1"), ()),),
        ),
    )
    with pytest.raises(ValueError, match="exactly one scenario"):
        assemble_scenario_probabilities(graph)


def test_source_calibration_lineage_changes_scenario_assembly_hash():
    first = assemble_scenario_probabilities(partition_graph("CERT-B"))
    second = assemble_scenario_probabilities(partition_graph("CERT-B-REV2"))
    assert first.assembly_hash != second.assembly_hash
    assert first.dataset_hash != second.dataset_hash
    assert (
        first.certificate(cohort_key="scenario_assembly|memory|12m").lineage_hash
        != second.certificate(cohort_key="scenario_assembly|memory|12m").lineage_hash
    )


def test_versioned_copula_contract_requires_explicit_joint_distribution():
    with pytest.raises(ValueError, match="explicit joint-state distribution"):
        ScenarioDependenceContract(
            method=ScenarioDependenceMethod.VERSIONED_CORRELATION_OR_COPULA,
            version="copula-v1",
        ).validate()
