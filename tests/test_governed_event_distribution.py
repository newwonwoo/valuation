from decimal import Decimal

import pytest

from valuation_engine.governed_event_distribution import (
    GovernedDistributionError,
    StructuralEquityBranch,
    compose_governed_equity_distribution,
    structural_equity_value,
)


def _branch(branch_id: str, probability: str, assets: str):
    return StructuralEquityBranch(
        branch_id=branch_id,
        probability=Decimal(probability),
        business_asset_value=Decimal(assets),
        senior_claim_value=Decimal("20366800000000"),
        annual_asset_volatility=Decimal("0.22"),
        risk_free_rate=Decimal("0.04571"),
        claim_horizon_years=Decimal("5"),
        evidence_path_ids=(f"audited:{branch_id}",),
        is_central=branch_id == "Central",
    )


def test_structural_downside_is_a_dated_option_not_report_time_zero_floor():
    value = structural_equity_value(_branch("Down", "0.20", "9938911959819.174"))
    assert value > 0
    assert value < Decimal("1000000000000")


def test_governed_prior_has_central_mode_weighted_value_and_concrete_entry():
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
    assert result.authorization_status == "GOVERNED_EVENT_PRIOR_WITH_AUDITED_SCENARIO_VALUE_BRIDGE"
    assert len(result.distribution_hash) == 64


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
