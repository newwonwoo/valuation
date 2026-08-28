from decimal import Decimal

import pytest

from valuation_engine.dynamic_hierarchical_posterior import (
    BetaPosterior,
    DataIntegrityAssessment,
    HierarchicalEvidenceBlock,
    PosteriorStatus,
    build_dynamic_hierarchical_posterior,
    estimate_empirical_bayes_parent_strength,
)


def test_zero_local_data_inherits_prior_instead_of_blocking_probability():
    root = BetaPosterior.from_mean_strength(Decimal("0.30"), Decimal("10"))
    result = build_dynamic_hierarchical_posterior(
        event_class="margin_compression",
        horizon="12m",
        root_prior=root,
        evidence_blocks=(
            HierarchicalEvidenceBlock(
                node_id="memory",
                success_count=0,
                total_count=0,
                likelihood_weight=Decimal("0.20"),
                dataset_hash="MEMORY-EMPTY",
            ),
        ),
    )
    assert result.status is PosteriorStatus.ESTIMATED
    assert result.numeric_weighting_allowed
    assert result.probability == root.mean
    assert result.credible_interval is not None


def test_weak_evidence_moves_probability_less_than_strong_evidence():
    root = BetaPosterior.from_mean_strength(Decimal("0.30"), Decimal("10"))
    weak = build_dynamic_hierarchical_posterior(
        event_class="margin_compression",
        horizon="12m",
        root_prior=root,
        evidence_blocks=(
            HierarchicalEvidenceBlock("memory", 8, 10, Decimal("0.20"), "WEAK"),
        ),
    )
    strong = build_dynamic_hierarchical_posterior(
        event_class="margin_compression",
        horizon="12m",
        root_prior=root,
        evidence_blocks=(
            HierarchicalEvidenceBlock("memory", 8, 10, Decimal("1.00"), "STRONG"),
        ),
    )
    assert weak.probability is not None and strong.probability is not None
    assert root.mean < weak.probability < strong.probability


def test_more_effective_evidence_narrows_credible_interval():
    root = BetaPosterior.from_mean_strength(Decimal("0.50"), Decimal("4"))
    sparse = build_dynamic_hierarchical_posterior(
        event_class="price_decline",
        horizon="12m",
        root_prior=root,
        evidence_blocks=(
            HierarchicalEvidenceBlock("semiconductor", 2, 4, Decimal("0.25"), "S"),
        ),
    )
    dense = build_dynamic_hierarchical_posterior(
        event_class="price_decline",
        horizon="12m",
        root_prior=root,
        evidence_blocks=(
            HierarchicalEvidenceBlock("semiconductor", 40, 80, Decimal("1.0"), "D"),
        ),
    )
    sl, su = sparse.credible_interval or (Decimal("0"), Decimal("1"))
    dl, du = dense.credible_interval or (Decimal("0"), Decimal("1"))
    assert du - dl < su - sl


def test_only_data_integrity_failures_hard_block_posterior_weighting():
    root = BetaPosterior.from_mean_strength(Decimal("0.50"), Decimal("8"))
    blocked = build_dynamic_hierarchical_posterior(
        event_class="cash_conversion_miss",
        horizon="12m",
        root_prior=root,
        evidence_blocks=(
            HierarchicalEvidenceBlock(
                node_id="memory",
                success_count=3,
                total_count=5,
                likelihood_weight=Decimal("0.8"),
                dataset_hash="LEAKED",
                integrity=DataIntegrityAssessment(no_outcome_leakage=False),
            ),
        ),
    )
    assert blocked.status is PosteriorStatus.DATA_BLOCKED
    assert not blocked.numeric_weighting_allowed
    assert blocked.probability is None
    assert blocked.integrity_violations == ("memory:OUTCOME_LEAKAGE_VIOLATION",)
    with pytest.raises(PermissionError):
        blocked.certificate()


def test_posterior_certificate_carries_probability_uncertainty_and_lineage():
    root = BetaPosterior.from_mean_strength(Decimal("0.40"), Decimal("12"))
    result = build_dynamic_hierarchical_posterior(
        event_class="capacity_ramp_delay",
        horizon="24m",
        root_prior=root,
        evidence_blocks=(
            HierarchicalEvidenceBlock("capacity_manufacturing", 4, 10, Decimal("0.5"), "A"),
            HierarchicalEvidenceBlock("semiconductor", 3, 6, Decimal("0.4"), "B"),
            HierarchicalEvidenceBlock("memory", 1, 2, Decimal("0.2"), "C"),
        ),
    )
    certificate = result.certificate()
    certificate.validate_for_weighting()
    assert certificate.lower_probability <= certificate.final_probability <= certificate.upper_probability
    assert certificate.node_hashes == ("A", "B", "C")
    assert certificate.lineage_hash


def test_empirical_bayes_parent_strength_is_data_driven_and_bounded():
    homogeneous = estimate_empirical_bayes_parent_strength(
        group_successes=(5, 10, 15, 20),
        group_totals=(10, 20, 30, 40),
        minimum_strength=Decimal("2"),
        maximum_strength=Decimal("100"),
    )
    heterogeneous = estimate_empirical_bayes_parent_strength(
        group_successes=(0, 20, 0, 40),
        group_totals=(10, 20, 30, 40),
        minimum_strength=Decimal("2"),
        maximum_strength=Decimal("100"),
    )
    assert Decimal("2") <= heterogeneous <= Decimal("100")
    assert Decimal("2") <= homogeneous <= Decimal("100")
    assert homogeneous > heterogeneous
