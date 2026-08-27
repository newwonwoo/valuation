from decimal import Decimal

from valuation_engine.capacity_economics import build_capacity_economics
from valuation_engine.sanil_live_primary import load_sanil_snapshot


def test_sanil_site_capacity_connects_to_mix_profit_and_fcff():
    snapshot = load_sanil_snapshot()
    result = build_capacity_economics(snapshot.payload)
    physical = result.physical

    assert physical.existing_product_nameplate_capacity.quantize(
        Decimal("0.001")
    ) == Decimal("309.019")
    assert physical.existing_product_effective_capacity.quantize(
        Decimal("0.001")
    ) == Decimal("293.568")
    assert physical.uhv_effective_capacity == Decimal("209.00")
    assert physical.total_effective_capacity.quantize(
        Decimal("0.001")
    ) == Decimal("502.568")
    assert physical.existing_product_mix.quantize(Decimal("0.001")) == Decimal(
        "0.584"
    )
    assert physical.uhv_mix.quantize(Decimal("0.001")) == Decimal("0.416")
    assert physical.specialty_transformer_effective_capacity.quantize(
        Decimal("0.001")
    ) == Decimal("223.111")
    assert physical.grid_transformer_effective_capacity.quantize(
        Decimal("0.001")
    ) == Decimal("61.649")
    assert physical.other_product_effective_capacity.quantize(
        Decimal("0.001")
    ) == Decimal("8.807")

    core = result.scenario("Core").mature
    bull = result.scenario("Bull").mature
    assert core.total_revenue.quantize(Decimal("0.1")) == Decimal("1523.4")
    assert core.total_operating_profit.quantize(Decimal("0.1")) == Decimal(
        "608.1"
    )
    assert core.total_fcff.quantize(Decimal("0.1")) == Decimal("451.6")
    assert bull.total_revenue.quantize(Decimal("0.1")) == Decimal("1670.2")
    assert bull.total_operating_profit.quantize(Decimal("0.1")) == Decimal(
        "667.8"
    )
    assert bull.total_fcff.quantize(Decimal("0.1")) == Decimal("499.2")

    ramp, mature = result.checkpoints
    assert (ramp.year, ramp.label) == (2029, "램프업")
    assert ramp.total_revenue.quantize(Decimal("0.1")) == Decimal("1514.4")
    assert ramp.operating_profit.quantize(Decimal("0.1")) == Decimal("595.0")
    assert ramp.fcff.quantize(Decimal("0.1")) == Decimal("433.8")
    assert (mature.year, mature.label) == (2030, "전량가동")
    assert mature.total_revenue.quantize(Decimal("0.1")) == Decimal("1670.2")
    assert mature.operating_profit.quantize(Decimal("0.1")) == Decimal("667.8")
    assert mature.fcff.quantize(Decimal("0.1")) == Decimal("481.4")
    assert mature.normalized_fcff.quantize(Decimal("0.1")) == Decimal("506.6")


def test_working_capital_is_charged_on_annual_revenue_change_only():
    snapshot = load_sanil_snapshot()
    core = build_capacity_economics(snapshot.payload).scenario("Core")

    final_increment = (
        core.years[-1].existing_product_incremental_revenue
        + core.years[-1].uhv_incremental_revenue
    ) - (
        core.years[-2].existing_product_incremental_revenue
        + core.years[-2].uhv_incremental_revenue
    )
    final_incremental_nopat = (
        core.years[-1].incremental_operating_profit * Decimal("0.771")
    )
    expected_fcff = (
        final_incremental_nopat
        + (
            core.years[-1].existing_product_incremental_revenue
            + core.years[-1].uhv_incremental_revenue
        )
        * Decimal("0.01")
        - (
            core.years[-1].existing_product_incremental_revenue
            + core.years[-1].uhv_incremental_revenue
        )
        * Decimal("0.015")
        - final_increment * Decimal("0.05")
    )

    assert core.years[-1].incremental_fcff == expected_fcff
