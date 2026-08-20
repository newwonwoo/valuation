import pytest

from valuation_engine.funding import ClaimStage, FundingLadder, FundingLayer, FundingLink, PolicyTransmission
from valuation_engine.per import (
    EconomicAssumptionFingerprint,
    FundamentalPERAssumptions,
    PERLevel,
    PERLevelName,
    PeerPERInput,
    build_hierarchical_warranted_per,
    fundamental_forward_per,
    hierarchical_residual_pool,
    validate_dcf_per_assumption_consistency,
)
from valuation_engine.risk import (
    BetaLevel,
    BetaLevelName,
    PeerBetaInput,
    blume_adjust_beta,
    hierarchical_partial_pool,
    relever_beta,
    unlever_beta,
    vasicek_adjust_beta,
)
from valuation_engine.street import (
    GapDriverCategory,
    GapEvidenceQuality,
    GapQuality,
    StreetGapDriver,
    StreetResearchReport,
    analyze_street_gap,
)
from valuation_engine.wacc import (
    CostOfDebtInputs,
    CostOfEquityInputs,
    CustomerAdvanceCreditEvidence,
    TargetCapitalStructure,
    compute_wacc,
    customer_funded_growth_ratio,
    validate_terminal_consistency,
)


def peer(peer_id, beta):
    return PeerBetaInput(peer_id, beta, debt=20, equity=100, tax_rate=0.25)


def test_unlever_relever_roundtrip():
    asset = unlever_beta(1.4, debt=20, equity=100, tax_rate=0.25)
    assert relever_beta(asset, debt=20, equity=100, tax_rate=0.25) == pytest.approx(1.4)


def test_beta_adjustments_shrink_extreme_beta():
    assert 1.0 < blume_adjust_beta(1.6) < 1.6
    adjusted = vasicek_adjust_beta(1.6, raw_variance=0.16, prior_mean=1.1, prior_variance=0.04)
    assert 1.1 < adjusted < 1.6


def test_noisy_small_l4_shrinks_to_upper_prior():
    levels = (
        BetaLevel(BetaLevelName.L1_BROAD_SECTOR, (peer("l1a", 1.0), peer("l1b", 1.4), peer("l1c", 1.2))),
        BetaLevel(BetaLevelName.L2_INDUSTRY, (peer("l2a", 1.1), peer("l2b", 1.2), peer("l2c", 1.3))),
        BetaLevel(BetaLevelName.L3_RISK_DRIVER_SUBINDUSTRY, (peer("l3a", 1.15), peer("l3b", 1.25), peer("l3c", 1.2))),
        BetaLevel(BetaLevelName.L4_ECONOMIC_TWINS, (peer("twin", 2.0),)),
    )
    result = hierarchical_partial_pool(levels)
    l3 = result.updates[-2].posterior_mean
    l4_raw = result.updates[-1].group_mean_asset_beta
    assert abs(result.asset_beta - l3) < abs(l4_raw - l3)


def test_wacc_requires_currency_consistency_and_target_weights():
    with pytest.raises(ValueError, match="currency"):
        CostOfEquityInputs(0.04, 1.2, 0.05, "KRW", "USD")
    equity = CostOfEquityInputs(0.035, 1.2, 0.05, "KRW", "KRW")
    debt = CostOfDebtInputs(0.045, 0.25)
    result = compute_wacc(equity, debt, TargetCapitalStructure(0.8, 0.2))
    assert result.wacc == pytest.approx(0.8 * equity.cost_of_equity + 0.2 * debt.after_tax_cost)


def test_customer_advances_need_credit_confirmation_for_wacc_cut():
    weak = CustomerAdvanceCreditEvidence(True, True, True, True, False, True)
    strong = CustomerAdvanceCreditEvidence(True, True, True, True, True, True)
    assert not weak.supports_wacc_reduction
    assert strong.supports_wacc_reduction
    assert customer_funded_growth_ratio(30, 40, 20) == pytest.approx(0.5)


def test_terminal_consistency_gate():
    result = validate_terminal_consistency(wacc=0.09, terminal_growth=0.02, terminal_roic=0.12)
    assert result.reinvestment_rate == pytest.approx(1 / 6)
    with pytest.raises(ValueError, match="WACC"):
        validate_terminal_consistency(wacc=0.02, terminal_growth=0.02, terminal_roic=0.12)


def core_per_inputs():
    return FundamentalPERAssumptions(
        normalized_forward_eps=1000,
        explicit_growth_rates=(0.15, 0.10, 0.07),
        fcfe_conversion_rates=(0.55, 0.58, 0.62, 0.68),
        cost_of_equity=0.10,
        terminal_growth=0.025,
        terminal_roe=0.14,
    )


def test_per_blocks_non_positive_eps():
    assert fundamental_forward_per(core_per_inputs()).forward_per > 0
    with pytest.raises(ValueError, match="positive normalized forward EPS"):
        FundamentalPERAssumptions(-1, (), (0.5,), 0.1, 0.02, 0.12)


def test_expansion_per_requires_committed_or_preinvested_evidence():
    expansion = FundamentalPERAssumptions(1000, (0.15, 0.12, 0.10, 0.08, 0.06), (0.55, 0.56, 0.58, 0.62, 0.68, 0.72), 0.10, 0.025, 0.14)
    with pytest.raises(ValueError, match="committed/pre-invested"):
        build_hierarchical_warranted_per(core_per_inputs(), expansion=expansion)
    result = build_hierarchical_warranted_per(core_per_inputs(), expansion=expansion, expansion_is_committed_or_preinvested=True)
    assert result.expansion_adjusted_fundamental_per is not None


def per_peer(peer_id, market_per, fundamental_per):
    return PeerPERInput(peer_id, market_per, fundamental_per)


def test_hierarchical_per_pools_residual_not_raw_per():
    levels = (
        PERLevel(PERLevelName.L1_BROAD_SECTOR, (per_peer("a", 18, 15), per_peer("b", 16, 15), per_peer("c", 17, 15))),
        PERLevel(PERLevelName.L2_INDUSTRY, (per_peer("d", 20, 16), per_peer("e", 19, 16), per_peer("f", 18, 16))),
        PERLevel(PERLevelName.L3_RISK_DRIVER_SUBINDUSTRY, (per_peer("g", 22, 18), per_peer("h", 21, 18))),
        PERLevel(PERLevelName.L4_ECONOMIC_TWINS, (per_peer("t", 24, 20),)),
    )
    pooled = hierarchical_residual_pool(levels)
    result = build_hierarchical_warranted_per(core_per_inputs(), residual_levels=levels)
    assert pooled.premium_multiplier > 1
    assert result.market_realization_per == pytest.approx(result.core_fundamental_per * pooled.premium_multiplier)


def test_dcf_per_consistency_blocks_hidden_growth_extension():
    dcf = EconomicAssumptionFingerprint((0.15, 0.1), (0.2, 0.21), (0.3, 0.32), 3)
    validate_dcf_per_assumption_consistency(dcf, EconomicAssumptionFingerprint((0.15, 0.1), (0.2, 0.21), (0.3, 0.32), 3))
    with pytest.raises(ValueError, match="growth duration"):
        validate_dcf_per_assumption_consistency(dcf, EconomicAssumptionFingerprint((0.15, 0.1, 0.08), (0.2, 0.21), (0.3, 0.32), 4))


def test_funding_ladder_and_policy_intent_separation():
    ladder = FundingLadder((
        FundingLink(FundingLayer.PRODUCT_OR_PROJECT, FundingLayer.BUYER_CASH_FLOW, "order needs spending capacity", ClaimStage.FIRST_ORDER_MECHANISM, 0.9),
        FundingLink(FundingLayer.BUYER_CASH_FLOW, FundingLayer.FINANCING_CHANNEL, "debt expands funded demand", ClaimStage.SECOND_ORDER_TRANSMISSION, 0.75),
    ))
    ladder.validate()
    with pytest.raises(ValueError, match="Policy Intent"):
        PolicyTransmission("support market liquidity", "support market liquidity", ("PRIMARY",))


def test_street_policy_only_gap_is_low_quality():
    reports = (StreetResearchReport("A", "", "2026-08-20", 60000, "KRW", "DCF", "2027", (), "source://a"),)
    drivers = (StreetGapDriver("wacc", GapDriverCategory.VALUATION_POLICY, 0.09, 0.105, "ratio", 15000, GapEvidenceQuality.VALUATION_POLICY, (), "lower discount rate"),)
    assert analyze_street_gap(75000, reports, drivers).gap_quality is GapQuality.VALUATION_POLICY_DRIVEN
