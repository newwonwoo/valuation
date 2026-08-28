from decimal import Decimal

from valuation_engine.dynamic_hierarchical_posterior import DataIntegrityAssessment
from valuation_engine.hierarchical_continuous_calibration import (
    ContinuousSummaryEvidence,
    NormalInverseGammaPosterior,
    build_hierarchical_continuous_posterior,
)


def _root() -> NormalInverseGammaPosterior:
    return NormalInverseGammaPosterior(
        mean=Decimal("0.10"),
        mean_strength=Decimal("8"),
        shape=Decimal("5"),
        scale=Decimal("0.08"),
    )


def test_zero_local_data_inherits_continuous_parent_distribution():
    root = _root()
    result = build_hierarchical_continuous_posterior(
        driver_id="revenue_growth",
        horizon="12m",
        root_prior=root,
        evidence=(
            ContinuousSummaryEvidence("memory", Decimal("0"), Decimal("0"), 0, Decimal("0.2"), "EMPTY"),
        ),
    )
    assert result.estimated
    assert result.final_posterior == root


def test_weak_predictive_weight_moves_continuous_posterior_less():
    root = _root()
    weak = build_hierarchical_continuous_posterior(
        driver_id="operating_margin",
        horizon="12m",
        root_prior=root,
        evidence=(
            ContinuousSummaryEvidence("memory", Decimal("0.40"), Decimal("0.10"), 20, Decimal("0.15"), "WEAK"),
        ),
    )
    strong = build_hierarchical_continuous_posterior(
        driver_id="operating_margin",
        horizon="12m",
        root_prior=root,
        evidence=(
            ContinuousSummaryEvidence("memory", Decimal("0.40"), Decimal("0.10"), 20, Decimal("0.90"), "STRONG"),
        ),
    )
    assert weak.final_posterior is not None and strong.final_posterior is not None
    assert root.mean < weak.final_posterior.mean < strong.final_posterior.mean


def test_more_effective_continuous_evidence_reduces_mean_uncertainty():
    sparse = build_hierarchical_continuous_posterior(
        driver_id="cash_conversion",
        horizon="12m",
        root_prior=_root(),
        evidence=(
            ContinuousSummaryEvidence("semiconductor", Decimal("0.15"), Decimal("0.12"), 5, Decimal("0.3"), "S"),
        ),
    )
    dense = build_hierarchical_continuous_posterior(
        driver_id="cash_conversion",
        horizon="12m",
        root_prior=_root(),
        evidence=(
            ContinuousSummaryEvidence("semiconductor", Decimal("0.15"), Decimal("0.12"), 100, Decimal("0.9"), "D"),
        ),
    )
    assert sparse.final_posterior is not None and dense.final_posterior is not None
    assert dense.final_posterior.mean_uncertainty < sparse.final_posterior.mean_uncertainty


def test_integrity_failure_blocks_continuous_calibration():
    result = build_hierarchical_continuous_posterior(
        driver_id="capex_intensity",
        horizon="12m",
        root_prior=_root(),
        evidence=(
            ContinuousSummaryEvidence(
                "memory",
                Decimal("0.25"),
                Decimal("0.08"),
                20,
                Decimal("0.8"),
                "LEAKED",
                DataIntegrityAssessment(no_outcome_leakage=False),
            ),
        ),
    )
    assert not result.estimated
    assert result.final_posterior is None
    assert result.integrity_violations == ("memory:OUTCOME_LEAKAGE_VIOLATION",)
