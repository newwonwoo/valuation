from decimal import Decimal

import pytest

from valuation_engine.hierarchical_calibration import (
    ChildSpecializationPolicy,
    HierarchicalNodeState,
    NodeCalibrationEvidence,
    ParentCalibrationPrior,
    ResolvedCalibrationEvent,
    build_hierarchical_node_calibration,
)


def policy() -> ChildSpecializationPolicy:
    return ChildSpecializationPolicy(
        version="2.0-test",
        shrinkage_version="beta_binomial_empirical_bayes/v1",
        min_resolved_events=30,
        min_companies=5,
        min_quarters=4,
        min_effective_sample_size=50,
        min_oos_windows=2,
        max_oos_brier_skill_delta_vs_parent=Decimal("0.02"),
        max_posterior_shift_without_local_promotion=Decimal("0.10"),
        parent_strength_default=40,
        parent_strength_min=10,
        parent_strength_max=200,
        parent_strength_source="training_oos_only",
    )


def parent(
    probability: str = "0.40",
    *,
    strength: int = 40,
    event_ids: tuple[str, ...] = (),
    oos: tuple[Decimal, ...] = (Decimal("0.10"), Decimal("0.08")),
    certified: bool = True,
) -> ParentCalibrationPrior:
    return ParentCalibrationPrior(
        probability=Decimal(probability),
        strength=strength,
        certified=certified,
        event_ids=event_ids,
        snapshot_hash="PARENT-SNAPSHOT" if certified else "",
        dataset_hash="PARENT-DATASET" if certified else "",
        oos_brier_skill_windows=oos,
    )


def evidence(
    outcomes: tuple[bool, ...],
    *,
    companies: int = 6,
    quarters: int = 4,
    oos: tuple[Decimal, ...] = (Decimal("0.11"), Decimal("0.09")),
) -> NodeCalibrationEvidence:
    resolved = tuple(
        ResolvedCalibrationEvent(
            event_key=f"E-{index}",
            company_id=f"C-{index % companies}",
            issued_quarter=f"202{index % quarters + 1}Q1",
            occurred=occurred,
        )
        for index, occurred in enumerate(outcomes)
    )
    return NodeCalibrationEvidence(
        node_id="memory",
        event_class="margin_compression",
        horizon="12m",
        resolved_events=resolved,
        oos_brier_skill_windows=oos,
        dataset_hash="CHILD-DATASET",
    )


def test_zero_data_child_inherits_parent_exactly():
    result = build_hierarchical_node_calibration(
        evidence=evidence((), oos=()),
        parent=parent("0.37"),
        policy=policy(),
    )
    assert result.state is HierarchicalNodeState.INHERITED
    assert result.posterior_probability == Decimal("0.37")
    assert result.posterior_shift == Decimal("0")
    assert result.local_resolved_count == 0


def test_small_extreme_sample_is_shrunk_and_shift_capped():
    result = build_hierarchical_node_calibration(
        evidence=evidence((True,) * 10, companies=2, quarters=2, oos=()),
        parent=parent("0.20"),
        policy=policy(),
    )
    assert result.state is HierarchicalNodeState.SHRUNK
    assert result.posterior_probability == Decimal("0.30")
    assert result.posterior_shift == Decimal("0.10")
    assert "MIN_RESOLVED_EVENTS" in result.gate_failures
    assert "OOS_WINDOWS" in result.gate_failures


def test_large_coherent_child_can_specialize():
    outcomes = (True,) * 80 + (False,) * 20
    result = build_hierarchical_node_calibration(
        evidence=evidence(outcomes, companies=10, quarters=4),
        parent=parent("0.20"),
        policy=policy(),
    )
    assert result.state is HierarchicalNodeState.CALIBRATED_LOCAL
    assert result.posterior_probability == Decimal(88) / Decimal(140)
    assert result.posterior_shift > Decimal("0.10")
    assert not result.gate_failures


def test_oos_deterioration_blocks_local_promotion():
    outcomes = (True,) * 24 + (False,) * 16
    result = build_hierarchical_node_calibration(
        evidence=evidence(
            outcomes,
            oos=(Decimal("0.02"), Decimal("0.01")),
        ),
        parent=parent(
            "0.40",
            oos=(Decimal("0.10"), Decimal("0.08")),
        ),
        policy=policy(),
    )
    assert result.state is HierarchicalNodeState.SHRUNK
    assert "OOS_DETERIORATION" in result.gate_failures


def test_parent_and_child_event_overlap_is_rejected():
    child = evidence((True, False, True))
    with pytest.raises(ValueError, match="leave-child-out"):
        build_hierarchical_node_calibration(
            evidence=child,
            parent=parent(event_ids=("E-1",)),
            policy=policy(),
        )


def test_uncertified_parent_cannot_authorize_child():
    result = build_hierarchical_node_calibration(
        evidence=evidence((True,) * 40),
        parent=parent(certified=False),
        policy=policy(),
    )
    assert result.state is HierarchicalNodeState.UNCALIBRATED
    assert result.posterior_probability is None
    assert result.gate_failures == ("CERTIFIED_PARENT_REQUIRED",)


def test_prior_local_node_degrades_when_current_gate_fails():
    result = build_hierarchical_node_calibration(
        evidence=evidence(
            (True,) * 20 + (False,) * 20,
            oos=(Decimal("-0.10"), Decimal("-0.12")),
        ),
        parent=parent(),
        policy=policy(),
        prior_state=HierarchicalNodeState.CALIBRATED_LOCAL,
    )
    assert result.state is HierarchicalNodeState.DEGRADED
    assert not result.authorizable
