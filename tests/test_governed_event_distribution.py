from decimal import Decimal

import pytest

from valuation_engine.governed_event_distribution import (
    GovernedDistributionError,
    StructuralAssetBasis,
    StructuralClaimBasis,
    StructuralEquityBranch,
    StructuralMaturityBasis,
    StructuralModelQualification,
    StructuralModelRole,
    StructuralVolatilityBasis,
    compose_governed_equity_distribution,
    structural_equity_value,
)


def _qualification(
    *,
    claim_basis: StructuralClaimBasis = StructuralClaimBasis.PROMISED_AT_HORIZON,
    asset_basis: StructuralAssetBasis = StructuralAssetBasis.MARKET_CALIBRATED,
    volatility_basis: StructuralVolatilityBasis = StructuralVolatilityBasis.CALIBRATED_ASSET_RETURNS,
    maturity_basis: StructuralMaturityBasis = StructuralMaturityBasis.SINGLE_MATURITY,
    permitted_role: StructuralModelRole = StructuralModelRole.PRIMARY_VALUE,
) -> StructuralModelQualification:
    return StructuralModelQualification(
        claim_basis=claim_basis,
        asset_basis=asset_basis,
        volatility_basis=volatility_basis,
        maturity_basis=maturity_basis,
        evidence_path_ids=("structural-inputs",),
        permitted_role=permitted_role,
    )


def _branch(
    branch_id: str,
    probability: str,
    assets: str,
    *,
    qualification: StructuralModelQualification | None = None,
    probability_basis: str = "CALIBRATED_EVENT_PROBABILITY",
):
    return StructuralEquityBranch(
        branch_id=branch_id,
        probability=Decimal(probability),
        business_asset_value=Decimal(assets),
        senior_claim_value=Decimal("20366800000000"),
        annual_asset_volatility=Decimal("0.22"),
        risk_free_rate=Decimal("0.04571"),
        claim_horizon_years=Decimal("5"),
        evidence_path_ids=(f"audited:{branch_id}",),
        qualification=qualification or _qualification(),
        probability_basis=probability_basis,
        is_central=branch_id == "Central",
    )


def test_structural_downside_is_a_dated_option_not_report_time_zero_floor():
    value = structural_equity_value(_branch("Down", "0.20", "9938911959819.174"))
    assert value > 0
    assert value < Decimal("1000000000000")


def test_calibrated_event_distribution_has_weighted_value_and_concrete_entry():
    result = compose_governed_equity_distribution(
        branches=(
            _branch("Down", "0.20", "9938911959819.174"),
            _branch("Central", "0.60", "21797575942933.96"),
            _branch("Upside", "0.20", "34656996632197.97"),
        ),
        diluted_shares=Decimal("389669121"),
        entry_horizon_years=3,
        required_annual_return=Decimal("0.12"),
        entry_quantile=Decimal("0.25"),
        sensitivity_returns=(Decimal("0.10"), Decimal("0.12"), Decimal("0.15")),
        source_bridge_hash="audited-legacy-valuation-plus-financing-bridge",
    )
    values = dict((name, value) for name, value, _ in result.branch_values_per_share)
    assert values["Down"] > 0
    assert result.p50 == values["Central"]
    assert result.mean > result.p50
    assert result.entry_price > 0
    assert result.target_success_probability == Decimal("0.75")
    assert result.realized_success_probability == Decimal("0.80")
    assert result.authorization_status == (
        "CALIBRATED_EVENT_PROBABILITY_WITH_QUALIFIED_STRUCTURAL_VALUE"
    )
    assert len(result.distribution_hash) == 64


def test_single_governed_event_prior_cannot_authorize_target_or_success_entry():
    with pytest.raises(GovernedDistributionError, match="single governed event prior"):
        compose_governed_equity_distribution(
            branches=(
                _branch(
                    "Down", "0.20", "9938911959819.174",
                    probability_basis="GOVERNED_EVENT_PRIOR",
                ),
                _branch(
                    "Central", "0.60", "21797575942933.96",
                    probability_basis="GOVERNED_EVENT_PRIOR",
                ),
                _branch(
                    "Upside", "0.20", "34656996632197.97",
                    probability_basis="GOVERNED_EVENT_PRIOR",
                ),
            ),
            diluted_shares=Decimal("389669121"),
            entry_horizon_years=3,
            required_annual_return=Decimal("0.12"),
            entry_quantile=Decimal("0.25"),
            sensitivity_returns=(Decimal("0.10"),),
            source_bridge_hash="bridge",
        )


def test_nearest_anchor_shape_with_noncentral_mode_is_rejected():
    with pytest.raises(GovernedDistributionError, match="uniquely most probable"):
        compose_governed_equity_distribution(
            branches=(
                _branch("Down", "0.45", "9938911959819.174"),
                _branch("Central", "0.10", "21797575942933.96"),
                _branch("Upside", "0.45", "34656996632197.97"),
            ),
            diluted_shares=Decimal("389669121"),
            entry_horizon_years=3,
            required_annual_return=Decimal("0.12"),
            entry_quantile=Decimal("0.25"),
            sensitivity_returns=(Decimal("0.10"),),
            source_bridge_hash="bridge",
        )


def test_current_carrying_amount_cannot_be_used_as_a_future_structural_strike():
    branch = _branch(
        "Down",
        "0.20",
        "9938911959819.174",
        qualification=_qualification(
            claim_basis=StructuralClaimBasis.CURRENT_CARRYING_AMOUNT
        ),
    )
    with pytest.raises(GovernedDistributionError, match="promised claim amount"):
        structural_equity_value(branch)


def test_dcf_scenario_width_and_multi_maturity_proxy_are_cross_check_only():
    diagnostic = _qualification(
        asset_basis=StructuralAssetBasis.DCF_DERIVED,
        volatility_basis=StructuralVolatilityBasis.SCENARIO_ENVELOPE_PROXY,
        maturity_basis=StructuralMaturityBasis.MULTI_MATURITY_AGGREGATE_PROXY,
    )
    branches = (
        _branch("Down", "0.20", "9938911959819.174", qualification=diagnostic),
        _branch("Central", "0.60", "21797575942933.96", qualification=diagnostic),
        _branch("Upside", "0.20", "34656996632197.97", qualification=diagnostic),
    )
    # A cross-check number remains calculable, but it cannot authorize the
    # primary distribution or a target/entry price.
    assert structural_equity_value(branches[0]) > 0
    with pytest.raises(GovernedDistributionError, match="diagnostic-only"):
        compose_governed_equity_distribution(
            branches=branches,
            diluted_shares=Decimal("389669121"),
            entry_horizon_years=3,
            required_annual_return=Decimal("0.12"),
            entry_quantile=Decimal("0.25"),
            sensitivity_returns=(Decimal("0.10"),),
            source_bridge_hash="bridge",
        )


def test_explicit_diagnostic_role_cannot_be_promoted_even_with_qualified_inputs():
    diagnostic_only = _qualification(
        permitted_role=StructuralModelRole.DIAGNOSTIC_CROSS_CHECK_ONLY
    )
    branches = (
        _branch("Down", "0.20", "9938911959819.174", qualification=diagnostic_only),
        _branch("Central", "0.60", "21797575942933.96", qualification=diagnostic_only),
        _branch("Upside", "0.20", "34656996632197.97", qualification=diagnostic_only),
    )
    with pytest.raises(GovernedDistributionError, match="diagnostic-only"):
        compose_governed_equity_distribution(
            branches=branches,
            diluted_shares=Decimal("389669121"),
            entry_horizon_years=3,
            required_annual_return=Decimal("0.12"),
            entry_quantile=Decimal("0.25"),
            sensitivity_returns=(Decimal("0.10"),),
            source_bridge_hash="bridge",
        )
