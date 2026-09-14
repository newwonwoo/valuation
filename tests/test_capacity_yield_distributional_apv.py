from __future__ import annotations

from dataclasses import fields, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from inspect import signature

import pytest

from valuation_engine.capacity_yield_operating_paths import (
    CapacityYieldMetricMapping,
    CapacityYieldModuleMapping,
    CapacityYieldProfile,
    CostBasis,
    OperatingPolicy,
    VariableCostRule,
    evaluate_capacity_yield_path,
)
from valuation_engine.distributional_apv import (
    APVPathInput,
    PathAPVResult,
    SegmentCashFlowPath,
    TaxShieldSchedule,
    aggregate_equity_distribution,
    calculate_usable_tax_shields,
    evaluate_apv_path,
)
from valuation_engine.dynamic_driver_distribution import (
    CalibrationDiagnostics,
    DriverCalibrationDiagnostic,
    DriverDistributionError,
    DriverPath,
    DynamicDriverPosterior,
    TargetDriverObservation,
    TargetDriverPanel,
    fit_dynamic_driver_posterior,
    simulate_driver_paths,
)
from valuation_engine.entry_price import (
    EntryPriceError,
    EntryPricePolicy,
    EntryPriceStatus,
    ExitPayoffPath,
    ProbabilityVector,
    calculate_ambiguity_robust_entry_price,
    calculate_entry_price,
)
from valuation_engine.levered_financing_paths import (
    AssetSalePolicy,
    ClaimBalance,
    ClaimType,
    DebtPeriod,
    DebtSchedule,
    EquityRaisePolicy,
    FinancingActionType,
    FinancingPathResult,
    FinancingPathSpec,
    FinancingPeriodInput,
    LeasePeriod,
    LeaseSchedule,
    RecoveryWaterfallPolicy,
    RefinancingFacility,
    apply_recovery_waterfall,
    evaluate_financing_path,
    reconcile_lease_schedule,
)
from valuation_engine.probability_ambiguity import (
    AmbiguityValueStatus,
    SignedOutcomeValue,
    calculate_ambiguity_expected_value_range,
)


D = Decimal


def _diagnostics(*, authorized: bool = True) -> CalibrationDiagnostics:
    item = DriverCalibrationDiagnostic(
        driver_id="activity",
        model_crps=D("0.7") if authorized else D("1.2"),
        benchmark_crps=D("1"),
        crps_skill=D("0.3") if authorized else D("-0.2"),
        mean_log_score=D("-1"),
        coverage_80=D("0.75"),
        coverage_90=D("0.92"),
        pit_mean=D("0.51"),
    )
    failures = () if authorized else ("NON_POSITIVE_CRPS_SKILL:activity",)
    return CalibrationDiagnostics(
        holdout_count=12,
        driver_diagnostics=(item,),
        residual_persistence_reproduced=True,
        cross_driver_covariance_reproduced=True,
        chronology_valid=True,
        authorization_failures=failures,
    )


def _posterior(*, authorized: bool = True) -> DynamicDriverPosterior:
    return DynamicDriverPosterior(
        driver_ids=("activity",),
        seasonal_terms=((D("1"),), (D("1"),), (D("1"),), (D("1"),)),
        transition_matrix=((D("0.8"),),),
        innovation_covariance=((D("0.04"),),),
        student_t_df=6,
        parameter_uncertainty=((D("0"),),),
        last_state=(D("10"),),
        last_quarter=3,
        lower_bounds=(None,),
        upper_bounds=(None,),
        parameter_draws_hash="PARAMS",
        source_hash="TARGET-HISTORY",
        calibration_diagnostics=_diagnostics(authorized=authorized),
    )


def test_driver_simulation_is_recursive_deterministic_and_has_no_scenario_anchors():
    first = simulate_driver_paths(
        _posterior(), horizon_periods=3, draws_per_seed=4, seed_set=(7, 11)
    )
    second = simulate_driver_paths(
        _posterior(), horizon_periods=3, draws_per_seed=4, seed_set=(7, 11)
    )
    assert first == second
    assert first.simulation_hash == second.simulation_hash
    path = first.paths[0].as_map()["activity"]
    # Period two starts from simulated period one, not the original state.
    assert abs(path[1] - (D("1") + D("0.8") * (path[0] - D("1")))) < D("2")
    posterior_names = {item.name for item in fields(DynamicDriverPosterior)}
    assert not {"scenario_id", "scenario_anchor", "current_market_price"}.intersection(
        posterior_names
    )


def test_unauthorized_oos_distribution_cannot_produce_paths():
    with pytest.raises(DriverDistributionError, match="not authorized"):
        simulate_driver_paths(
            _posterior(authorized=False),
            horizon_periods=2,
            draws_per_seed=1,
            seed_set=(1,),
        )


def test_simulated_paths_reproduce_positive_cross_driver_dependence():
    diagnostics = CalibrationDiagnostics(
        holdout_count=12,
        driver_diagnostics=tuple(
            DriverCalibrationDiagnostic(
                driver_id=driver_id,
                model_crps=D("0.7"),
                benchmark_crps=D("1"),
                crps_skill=D("0.3"),
                mean_log_score=D("-1"),
                coverage_80=D("0.75"),
                coverage_90=D("0.92"),
                pit_mean=D("0.5"),
            )
            for driver_id in ("volume", "yield")
        ),
        residual_persistence_reproduced=True,
        cross_driver_covariance_reproduced=True,
        chronology_valid=True,
    )
    posterior = DynamicDriverPosterior(
        driver_ids=("volume", "yield"),
        seasonal_terms=((D("0"), D("0")),) * 4,
        transition_matrix=((D("0"), D("0")), (D("0"), D("0"))),
        innovation_covariance=((D("1"), D("0.8")), (D("0.8"), D("1"))),
        student_t_df=6,
        parameter_uncertainty=((D("0"), D("0")), (D("0"), D("0"))),
        last_state=(D("0"), D("0")),
        last_quarter=3,
        lower_bounds=(None, None),
        upper_bounds=(None, None),
        parameter_draws_hash="P2",
        source_hash="TARGET2",
        calibration_diagnostics=diagnostics,
    )
    simulation = simulate_driver_paths(
        posterior, horizon_periods=1, draws_per_seed=1000, seed_set=(17,)
    )
    x = tuple(path.as_map()["volume"][0] for path in simulation.paths)
    y = tuple(path.as_map()["yield"][0] for path in simulation.paths)
    x_mean = sum(x, D("0")) / D(len(x))
    y_mean = sum(y, D("0")) / D(len(y))
    covariance = sum(
        ((a - x_mean) * (b - y_mean) for a, b in zip(x, y)), D("0")
    ) / D(len(x) - 1)
    assert covariance > D("0.6")


def test_target_panel_rejects_mixed_frequency_and_peer_company_outcomes():
    observations = []
    year, month = 2018, 3
    for index in range(32):
        period_end = date(year, month, 28)
        published = datetime.combine(period_end, datetime.min.time()) + timedelta(days=30)
        observations.append(
            TargetDriverObservation(
                period_end=period_end,
                published_at=published,
                first_seen_at=published,
                values=(("activity", D(index + 1)),),
                source_ref="https://example.test/filing",
                source_hash=f"H{index}",
            )
        )
        month += 3
        if month > 12:
            month -= 12
            year += 1
    panel = TargetDriverPanel(
        target_id="carrier-a",
        frequency="ANNUAL_PLUS_HALFYEAR",
        perimeter="consolidated",
        observations=tuple(observations),
        structural_breaks=(),
        source_hash="PANEL",
    )
    with pytest.raises(DriverDistributionError, match="quarterly"):
        panel.validate()
    peer = replace(observations[-1], origin="PEER_REALIZED")
    with pytest.raises(DriverDistributionError, match="target's own"):
        replace(panel, frequency="QUARTERLY", observations=tuple(observations[:-1]) + (peer,)).validate()


def test_rolling_origin_fit_records_proper_scores_and_authorizes_only_positive_skill():
    observations = []
    value = D("10")
    year, month = 2015, 3
    seasonal = (D("0.4"), D("-0.2"), D("0.3"), D("-0.1"))
    for index in range(44):
        period_end = date(year, month, 28)
        published = datetime.combine(period_end, datetime.min.time()) + timedelta(days=20)
        value = (
            D("2")
            + D("0.7") * value
            + seasonal[index % 4]
            + D(str(((index * 7) % 5 - 2) * 0.1))
        )
        observations.append(
            TargetDriverObservation(
                period_end,
                published,
                published,
                (("activity", value),),
                "https://example.test/target-filing",
                f"OBS-{index}",
            )
        )
        month += 3
        if month > 12:
            month -= 12
            year += 1
    posterior = fit_dynamic_driver_posterior(
        TargetDriverPanel(
            "target-a",
            "QUARTERLY",
            "consolidated",
            tuple(observations),
            (),
            "TARGET-PANEL",
        )
    )
    diagnostic = posterior.calibration_diagnostics.driver_diagnostics[0]
    assert diagnostic.crps_skill > D("0")
    assert diagnostic.model_crps < diagnostic.benchmark_crps
    assert posterior.calibration_diagnostics.valuation_distribution_authorized


def _air_profile_and_mapping():
    profile = CapacityYieldProfile(
        target_id="network-carrier-a",
        economic_archetype="capacity_yield_levered",
        reporting_currency="KRW",
        accounting_basis="IFRS",
        active_module_ids=("passenger", "cargo"),
        non_capacity_segment_ids=("maintenance",),
        metric_mapping_version="air-map-v1",
    )
    mapping = CapacityYieldMetricMapping(
        version="air-map-v1",
        modules=(
            CapacityYieldModuleMapping(
                "passenger", "seat_capacity", "passenger_yield", "load_factor", "PATH-PAX"
            ),
            CapacityYieldModuleMapping(
                "cargo", "cargo_traffic", "cargo_yield", None, "PATH-CARGO"
            ),
        ),
        variable_cost_rules=(
            VariableCostRule(
                "passenger-fuel",
                "passenger",
                CostBasis.CAPACITY,
                ("fuel_intensity", "fuel_price", "fx_rate"),
            ),
            VariableCostRule(
                "passenger-nonfuel",
                "passenger",
                CostBasis.CAPACITY,
                ("nonfuel_unit_cost",),
            ),
            VariableCostRule(
                "cargo-variable", "cargo", CostBasis.TRAFFIC, ("cargo_unit_cost",)
            ),
        ),
        fixed_cost_driver_ids=("fixed_cost",),
        depreciation_driver_id="depreciation",
        owned_capex_driver_id="owned_capex",
        lease_additions_driver_id="lease_additions",
        change_in_working_capital_driver_id="delta_nwc",
    )
    return profile, mapping


def _air_driver_path(*, owned_capex: str = "30", lease_additions: str = "20") -> DriverPath:
    return DriverPath(
        path_id="air-path-1",
        seed=1,
        values=(
            ("seat_capacity", (D("100"),)),
            ("passenger_yield", (D("2"),)),
            ("load_factor", (D("0.8"),)),
            ("cargo_traffic", (D("20"),)),
            ("cargo_yield", (D("3"),)),
            ("fuel_intensity", (D("0.01"),)),
            ("fuel_price", (D("5"),)),
            ("fx_rate", (D("1"),)),
            ("nonfuel_unit_cost", (D("0.5"),)),
            ("cargo_unit_cost", (D("1"),)),
            ("fixed_cost", (D("40"),)),
            ("depreciation", (D("10"),)),
            ("owned_capex", (D(owned_capex),)),
            ("lease_additions", (D(lease_additions),)),
            ("delta_nwc", (D("5"),)),
        ),
    )


def test_capacity_yield_operating_math_and_lease_reclassification_invariance():
    profile, mapping = _air_profile_and_mapping()
    purchase = evaluate_capacity_yield_path(
        profile=profile,
        metric_mapping=mapping,
        driver_path=_air_driver_path(owned_capex="50", lease_additions="0"),
        policy=OperatingPolicy(D("0.25")),
    )
    lease = evaluate_capacity_yield_path(
        profile=profile,
        metric_mapping=mapping,
        driver_path=_air_driver_path(owned_capex="0", lease_additions="50"),
        policy=OperatingPolicy(D("0.25")),
    )
    period = purchase.periods[0]
    assert period.revenue == D("220")
    assert period.variable_cost == D("75")
    assert period.ebitdar == D("105")
    assert period.lease_adjusted_ebit == D("95")
    assert purchase.periods[0].economic_reinvestment == lease.periods[0].economic_reinvestment == D("50")
    assert purchase.periods[0].mandatory_capex == D("50")
    assert lease.periods[0].mandatory_capex == D("0")
    assert purchase.periods[0].unlevered_fcff == lease.periods[0].unlevered_fcff


def test_non_airline_transport_profile_uses_same_common_calculation():
    profile = CapacityYieldProfile(
        target_id="ocean-line-b",
        economic_archetype="capacity_yield_levered",
        reporting_currency="USD",
        accounting_basis="IFRS",
        active_module_ids=("container",),
        non_capacity_segment_ids=(),
        metric_mapping_version="shipping-map-v1",
    )
    mapping = CapacityYieldMetricMapping(
        version="shipping-map-v1",
        modules=(
            CapacityYieldModuleMapping(
                "container", "available_teu", "freight_rate", "vessel_utilization", "PATH-BOX"
            ),
        ),
        variable_cost_rules=(
            VariableCostRule(
                "bunker", "container", CostBasis.CAPACITY, ("bunker_cost_per_teu",)
            ),
        ),
        fixed_cost_driver_ids=("charter_fixed_cost",),
        depreciation_driver_id="vessel_depreciation",
        owned_capex_driver_id="vessel_capex",
        lease_additions_driver_id="charter_additions",
        change_in_working_capital_driver_id="working_capital_change",
    )
    path = DriverPath(
        path_id="shipping-path",
        seed=3,
        values=(
            ("available_teu", (D("100"),)),
            ("freight_rate", (D("4"),)),
            ("vessel_utilization", (D("0.9"),)),
            ("bunker_cost_per_teu", (D("1"),)),
            ("charter_fixed_cost", (D("50"),)),
            ("vessel_depreciation", (D("20"),)),
            ("vessel_capex", (D("30"),)),
            ("charter_additions", (D("10"),)),
            ("working_capital_change", (D("5"),)),
        ),
    )
    result = evaluate_capacity_yield_path(
        profile=profile,
        metric_mapping=mapping,
        driver_path=path,
        policy=OperatingPolicy(D("0.20")),
    )
    assert result.periods[0].revenue == D("360")
    assert result.periods[0].unlevered_fcff == D("127")


def test_second_airline_profile_changes_only_inputs_and_metric_mapping():
    profile = CapacityYieldProfile(
        target_id="regional-low-cost-b",
        economic_archetype="capacity_yield_levered",
        reporting_currency="JPY",
        accounting_basis="IFRS",
        active_module_ids=("passenger",),
        non_capacity_segment_ids=(),
        metric_mapping_version="lcc-map-v2",
    )
    mapping = CapacityYieldMetricMapping(
        version="lcc-map-v2",
        modules=(
            CapacityYieldModuleMapping(
                "passenger", "available_seat_km", "fare_per_rpk", "seat_load", "PATH-LCC-PAX"
            ),
        ),
        variable_cost_rules=(
            VariableCostRule(
                "all-variable-cost", "passenger", CostBasis.CAPACITY, ("cost_per_ask",)
            ),
        ),
        fixed_cost_driver_ids=("overhead",),
        depreciation_driver_id="depreciation",
        owned_capex_driver_id="cash_aircraft_capex",
        lease_additions_driver_id="leased_aircraft_additions",
        change_in_working_capital_driver_id="delta_working_capital",
    )
    path = DriverPath(
        path_id="lcc-path",
        seed=9,
        values=(
            ("available_seat_km", (D("200"),)),
            ("fare_per_rpk", (D("1.5"),)),
            ("seat_load", (D("0.9"),)),
            ("cost_per_ask", (D("0.7"),)),
            ("overhead", (D("20"),)),
            ("depreciation", (D("10"),)),
            ("cash_aircraft_capex", (D("15"),)),
            ("leased_aircraft_additions", (D("5"),)),
            ("delta_working_capital", (D("0"),)),
        ),
    )
    result = evaluate_capacity_yield_path(
        profile=profile,
        metric_mapping=mapping,
        driver_path=path,
        policy=OperatingPolicy(D("0.25")),
    )
    assert result.target_id == "regional-low-cost-b"
    assert result.periods[0].revenue == D("270.00")
    assert result.periods[0].unlevered_fcff == D("65.0000")


def _one_period_spec(*, recovery_retention: str = "0.5") -> FinancingPathSpec:
    debt = DebtSchedule(
        claim_id="secured-debt",
        seniority=0,
        periods=(DebtPeriod(1, D("40"), D("0"), D("0"), D("0"), D("40")),),
    )
    lease = LeaseSchedule(
        claim_id="fleet-lease",
        seniority=1,
        periods=(LeasePeriod(1, D("10"), D("0"), D("1"), D("1"), D("10")),),
    )
    return FinancingPathSpec(
        opening_cash=D("0"),
        minimum_operating_cash=D("10"),
        debt_schedules=(debt,),
        lease_schedules=(lease,),
        refinancing_facilities=(
            RefinancingFacility("committed-refi", 0, (D("20"),), D("0")),
        ),
        asset_sale_policy=AssetSalePolicy((D("0"),), D("0")),
        equity_raise_policy=EquityRaisePolicy((D("30"),), D("0"), (D("0.30"),)),
        recovery_waterfall=RecoveryWaterfallPolicy(
            fixed_distress_cost=D("0"),
            distress_cost_rate=D("0.10"),
            old_shareholder_retention=D(recovery_retention),
        ),
    )


def test_lease_rollforward_balances_exactly():
    schedule = LeaseSchedule(
        "lease-a",
        1,
        (
            LeasePeriod(1, D("100"), D("20"), D("5"), D("25"), D("100")),
            LeasePeriod(2, D("100"), D("10"), D("5"), D("30"), D("85")),
        ),
    )
    reconciliation = reconcile_lease_schedule(schedule)
    assert reconciliation.balanced
    assert (
        reconciliation.opening_liability
        + reconciliation.additions
        + reconciliation.imputed_interest
        - reconciliation.payments
        == reconciliation.closing_liability
    )


def test_refinancing_then_dilution_then_distress_and_waterfall_recovery():
    result = evaluate_financing_path(
        inputs=(FinancingPeriodInput(1, D("0"), D("100"), D("100")),),
        spec=_one_period_spec(),
    )
    action_types = tuple(item.action_type for item in result.periods[0].actions)
    assert action_types == (
        FinancingActionType.REFINANCING,
        FinancingActionType.EQUITY_RAISE,
        FinancingActionType.DISTRESS,
    )
    assert result.distressed and result.dilution_occurred
    assert result.old_shareholder_ownership == D("0.70")
    assert result.recovery is not None
    assert result.recovery.residual_equity == D("20")
    assert result.recovery.old_shareholder_recovery == D("7.000")


def test_distress_recovery_is_zero_or_positive_only_from_explicit_waterfall():
    claims = (
        ClaimBalance("senior", ClaimType.DEBT, 0, D("50")),
        ClaimBalance("lease", ClaimType.LEASE, 1, D("10")),
    )
    policy = RecoveryWaterfallPolicy(D("0"), D("0.10"), D("0.50"))
    zero = apply_recovery_waterfall(
        period=1,
        gross_asset_proceeds=D("50"),
        claims=claims,
        policy=policy,
        old_shareholder_ownership=D("1"),
    )
    positive = apply_recovery_waterfall(
        period=1,
        gross_asset_proceeds=D("100"),
        claims=claims,
        policy=policy,
        old_shareholder_ownership=D("1"),
    )
    assert zero.old_shareholder_recovery == D("0")
    assert positive.old_shareholder_recovery == D("15.00")


def test_equal_seniority_claims_recover_pro_rata_not_by_claim_name():
    recovery = apply_recovery_waterfall(
        period=1,
        gross_asset_proceeds=D("50"),
        claims=(
            ClaimBalance("z-large", ClaimType.DEBT, 0, D("80")),
            ClaimBalance("a-small", ClaimType.DEBT, 0, D("20")),
        ),
        policy=RecoveryWaterfallPolicy(D("0"), D("0"), D("0")),
        old_shareholder_ownership=D("1"),
    )
    allocations = {item.claim_id: item.recovery_amount for item in recovery.creditor_allocations}
    assert allocations == {"a-small": D("10"), "z-large": D("40")}


def test_tax_shield_exists_only_when_taxable_income_is_positive():
    schedule = TaxShieldSchedule(
        taxable_income_before_interest=(D("-10"), D("5"), D("20")),
        deductible_interest=(D("4"), D("8"), D("8")),
        tax_rate=D("0.25"),
        discount_rate=D("0.05"),
    )
    assert calculate_usable_tax_shields(schedule) == (D("0.00"), D("1.25"), D("2.00"))


def _surviving_financing_result() -> FinancingPathResult:
    spec = replace(
        _one_period_spec(),
        opening_cash=D("100"),
        refinancing_facilities=(),
        equity_raise_policy=EquityRaisePolicy((D("0"),), D("0"), (D("0"),)),
    )
    return evaluate_financing_path(
        inputs=(
            FinancingPeriodInput(
                1,
                D("20"),
                D("10"),
                D("0"),
                taxable_income_before_interest=D("10"),
                tax_rate=D("0.25"),
            ),
        ),
        spec=spec,
    )


def _apv_result(path_id: str = "p1") -> PathAPVResult:
    result = evaluate_apv_path(
        APVPathInput(
            path_id=path_id,
            segments=(
                SegmentCashFlowPath(
                    "transport", "PATH-TRANSPORT", (D("20"),), D("0.10"), D("0.02")
                ),
            ),
            tax_shield_schedule=TaxShieldSchedule(
                (D("10"),), (D("1"),), D("0.25"), D("0.05")
            ),
            financing_result=_surviving_financing_result(),
            non_operating_assets_present=D("5"),
            non_operating_assets_at_horizon=D("5"),
            distributions_to_old_holders=(D("1"),),
            equity_required_return=D("0.12"),
            initial_shares=D("10"),
        )
    )
    assert result.tax_shield_present_value == D("0.25") / D("1.05")
    assert result.explicit_financing_cost_present_value == D("0")
    return result


def test_distribution_headline_is_p50_mean_secondary_and_signed_values_are_not_floored():
    base = _apv_result()
    signed = (
        replace(base, path_id="a", value_per_initial_share=D("-10")),
        replace(base, path_id="b", value_per_initial_share=D("10")),
        replace(base, path_id="c", value_per_initial_share=D("20")),
    )
    distribution = aggregate_equity_distribution(
        path_results=signed,
        seed_set=(1,),
        input_hash="INPUT",
        valuation_distribution_authorized=True,
    )
    assert distribution.path_values_per_share[0] == D("-10")
    assert distribution.headline_value == D("10")
    assert distribution.mean == D("20") / D("3")


def test_distress_cost_is_not_subtracted_as_apv_financing_cost_again():
    financing = evaluate_financing_path(
        inputs=(FinancingPeriodInput(1, D("0"), D("100"), D("100")),),
        spec=_one_period_spec(),
    )
    path = APVPathInput(
        path_id="distress-path",
        segments=(SegmentCashFlowPath("ops", "PATH-OPS", (D("10"),), D("0.1"), D("0")),),
        tax_shield_schedule=TaxShieldSchedule((D("0"),), (D("0"),), D("0.25"), D("0.05")),
        financing_result=financing,
        non_operating_assets_present=D("0"),
        non_operating_assets_at_horizon=D("0"),
        distributions_to_old_holders=(D("0"),),
        equity_required_return=D("0.12"),
        initial_shares=D("1"),
    )
    result = evaluate_apv_path(path)
    assert financing.recovery is not None and financing.recovery.distress_cost == D("10")
    assert result.explicit_financing_cost_present_value == D("0")


def test_entry_price_is_discounted_q25_and_has_no_market_input():
    payoffs = tuple(
        ExitPayoffPath(str(index), D(value), D("0"))
        for index, value in enumerate(("100", "200", "300", "400"), start=1)
    )
    policy = EntryPricePolicy(
        policy_version="entry-v1",
        horizon_years=3,
        required_annual_return=D("0.12"),
        success_quantile=D("0.25"),
        sensitivity_returns=(D("0.10"), D("0.12"), D("0.15")),
    )
    result = calculate_entry_price(
        payoffs=payoffs,
        policy=policy,
        valuation_distribution_authorized=True,
        distribution_hash="DISTRIBUTION",
    )
    assert result.status is EntryPriceStatus.AVAILABLE
    assert result.entry_price == D("175") / (D("1.12") ** 3)
    assert result.target_success_probability == D("0.75")
    assert "current_market_price" not in signature(calculate_entry_price).parameters
    assert "market_price" not in signature(calculate_entry_price).parameters


def test_entry_price_is_withheld_when_distribution_is_not_authorized():
    result = calculate_entry_price(
        payoffs=(ExitPayoffPath("p", D("100"), D("0")),),
        policy=EntryPricePolicy(
            "entry-v1", 3, D("0.12"), D("0.25"), (D("0.10"), D("0.12"), D("0.15"))
        ),
        valuation_distribution_authorized=False,
        distribution_hash="UNAUTHORIZED",
    )
    assert result.status is EntryPriceStatus.WITHHELD
    assert result.entry_price is None
    assert result.withheld_reason == "VALUATION_DISTRIBUTION_NOT_AUTHORIZED"


def test_event_prior_uses_worst_expected_value_when_q25_branch_is_unstable():
    payoffs = (
        ExitPayoffPath("Down", D("100"), D("0")),
        ExitPayoffPath("Central", D("200"), D("0")),
        ExitPayoffPath("Upside", D("400"), D("0")),
    )
    vectors = (
        ProbabilityVector(
            "downside_heavier",
            (("Down", D("0.30")), ("Central", D("0.50")), ("Upside", D("0.20"))),
            ("prior:downside",),
        ),
        ProbabilityVector(
            "governed_base",
            (("Down", D("0.20")), ("Central", D("0.60")), ("Upside", D("0.20"))),
            ("prior:base",),
        ),
        ProbabilityVector(
            "execution_success",
            (("Down", D("0.15")), ("Central", D("0.60")), ("Upside", D("0.25"))),
            ("prior:upside",),
        ),
    )
    policy = EntryPricePolicy(
        "ambiguity-entry-v1",
        3,
        D("0.12"),
        D("0.25"),
        (D("0.10"), D("0.12"), D("0.15")),
    )
    result = calculate_ambiguity_robust_entry_price(
        payoffs=payoffs,
        probability_vectors=vectors,
        policy=policy,
        valuation_values_authorized=True,
        distribution_hash="EVENT-PAYOFFS",
    )
    assert result.status is EntryPriceStatus.AVAILABLE
    assert result.worst_case_expected_payoff == D("210")
    assert result.best_case_expected_payoff == D("235")
    assert result.entry_price == D("210") / (D("1.12") ** 3)
    assert result.binding_probability_vector_id == "downside_heavier"
    assert not result.diagnostic_quantile_stable
    assert result.diagnostic_quantile_entry_price is None
    assert not result.probability_success_claim_authorized
    assert "current_market_price" not in signature(
        calculate_ambiguity_robust_entry_price
    ).parameters


def test_event_prior_expected_value_is_a_signed_range_without_zero_floor():
    outcomes = (
        SignedOutcomeValue("Down", D("-100"), ("value:down",)),
        SignedOutcomeValue("Central", D("20"), ("value:central",)),
        SignedOutcomeValue("Upside", D("200"), ("value:upside",)),
    )
    vectors = (
        ProbabilityVector(
            "downside_heavier",
            (("Down", D("0.30")), ("Central", D("0.50")), ("Upside", D("0.20"))),
            ("prior:downside",),
        ),
        ProbabilityVector(
            "governed_base",
            (("Down", D("0.20")), ("Central", D("0.60")), ("Upside", D("0.20"))),
            ("prior:base",),
        ),
        ProbabilityVector(
            "execution_success",
            (("Down", D("0.15")), ("Central", D("0.60")), ("Upside", D("0.25"))),
            ("prior:upside",),
        ),
    )
    result = calculate_ambiguity_expected_value_range(
        outcomes=outcomes,
        probability_vectors=vectors,
        values_authorized=True,
        value_set_hash="SIGNED-VALUES",
    )
    assert result.status is AmbiguityValueStatus.AVAILABLE
    assert result.minimum_expected_value == D("20")
    assert result.maximum_expected_value == D("47")
    assert result.binding_minimum_vector_id == "downside_heavier"
    assert not result.calibrated_probability_claim_authorized


def test_event_prior_expected_value_is_withheld_when_values_are_unauthorized():
    result = calculate_ambiguity_expected_value_range(
        outcomes=(
            SignedOutcomeValue("Down", D("-100"), ("value:down",)),
            SignedOutcomeValue("Upside", D("200"), ("value:upside",)),
        ),
        probability_vectors=(
            ProbabilityVector(
                "lower",
                (("Down", D("0.60")), ("Upside", D("0.40"))),
                ("prior:lower",),
            ),
            ProbabilityVector(
                "upper",
                (("Down", D("0.40")), ("Upside", D("0.60"))),
                ("prior:upper",),
            ),
        ),
        values_authorized=False,
        value_set_hash="UNAUTHORIZED-VALUES",
    )
    assert result.status is AmbiguityValueStatus.WITHHELD
    assert result.minimum_expected_value is None
    assert result.vector_results == ()
    assert result.withheld_reason == "OUTCOME_VALUES_NOT_AUTHORIZED"


def test_ambiguity_robust_entry_requires_source_bound_complete_probability_vectors():
    with pytest.raises(EntryPriceError, match="exact outcome set"):
        calculate_ambiguity_robust_entry_price(
            payoffs=(
                ExitPayoffPath("Down", D("100"), D("0")),
                ExitPayoffPath("Upside", D("300"), D("0")),
            ),
            probability_vectors=(
                ProbabilityVector(
                    "incomplete", (("Down", D("1")),), ("prior:one",)
                ),
                ProbabilityVector(
                    "complete",
                    (("Down", D("0.5")), ("Upside", D("0.5"))),
                    ("prior:two",),
                ),
            ),
            policy=EntryPricePolicy(
                "ambiguity-entry-v1", 3, D("0.12"), D("0.25"), (D("0.12"),)
            ),
            valuation_values_authorized=True,
            distribution_hash="EVENT-PAYOFFS",
        )


def test_ambiguity_robust_entry_rejects_a_single_prior_as_false_precision():
    with pytest.raises(EntryPriceError, match="at least two probability vectors"):
        calculate_ambiguity_robust_entry_price(
            payoffs=(
                ExitPayoffPath("Down", D("100"), D("0")),
                ExitPayoffPath("Upside", D("300"), D("0")),
            ),
            probability_vectors=(
                ProbabilityVector(
                    "only-prior",
                    (("Down", D("0.5")), ("Upside", D("0.5"))),
                    ("prior:only",),
                ),
            ),
            policy=EntryPricePolicy(
                "ambiguity-entry-v1", 3, D("0.12"), D("0.25"), (D("0.12"),)
            ),
            valuation_values_authorized=True,
            distribution_hash="EVENT-PAYOFFS",
        )
