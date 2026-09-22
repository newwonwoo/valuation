from dataclasses import replace
from decimal import Decimal
from inspect import signature

import pytest

from valuation_engine.payoff_model_ambiguity import (
    DatedShareholderCashFlow,
    DatedShareholderPayoffPath,
    PayoffModelAmbiguityError,
    PayoffModelAmbiguityStatus,
    PayoffModelCase,
    RobustEntryPolicy,
    audit_robust_payoff_ambiguity,
    calculate_robust_payoff_ambiguity_entry,
    create_payoff_model_case,
)
from valuation_engine.probability_ambiguity import ProbabilityVector
from valuation_engine.distribution_route_policy import (
    DistributionIntegrationRoute,
    DistributionRouteRequest,
    NO_SCENARIO_ASSIGNMENT,
    authorize_distribution_route,
)


def D(value: str) -> Decimal:
    return Decimal(value)


def _path(branch_id: str, amount: str, period: int = 5) -> DatedShareholderPayoffPath:
    return DatedShareholderPayoffPath(
        branch_id=branch_id,
        cash_flows=(DatedShareholderCashFlow(period, D(amount)),),
        payoff_calculation_hash=f"apv:{branch_id}:{amount}:{period}",
    )


def _priors() -> tuple[ProbabilityVector, ...]:
    return (
        ProbabilityVector(
            "downside_heavier",
            (("Down", D("0.30")), ("Central", D("0.50")), ("Upside", D("0.20"))),
            ("evidence:downside",),
        ),
        ProbabilityVector(
            "governed_base",
            (("Down", D("0.20")), ("Central", D("0.60")), ("Upside", D("0.20"))),
            ("evidence:base",),
        ),
        ProbabilityVector(
            "execution_success",
            (("Down", D("0.15")), ("Central", D("0.60")), ("Upside", D("0.25"))),
            ("evidence:upside",),
        ),
    )


def _cases() -> tuple[PayoffModelCase, ...]:
    return (
        create_payoff_model_case(
            model_case_id="observed_schedule",
            payoffs=(
                _path("Down", "50"),
                _path("Central", "200"),
                _path("Upside", "400"),
            ),
            evidence_path_ids=("debt:schedule", "lease:schedule"),
        ),
        create_payoff_model_case(
            model_case_id="restrictive_refinancing",
            payoffs=(
                _path("Down", "0"),
                _path("Central", "150"),
                _path("Upside", "300"),
            ),
            evidence_path_ids=(
                "debt:schedule",
                "prior:restrictive-refinancing",
            ),
        ),
    )


def _policy() -> RobustEntryPolicy:
    return RobustEntryPolicy(
        "two-layer-entry-v1",
        5,
        D("0.12"),
        (D("0.10"), D("0.12"), D("0.15")),
    )


def test_two_layer_ambiguity_uses_worst_complete_expectation_not_lowest_probability():
    result = calculate_robust_payoff_ambiguity_entry(
        payoff_model_cases=_cases(),
        probability_vectors=_priors(),
        policy=_policy(),
        future_payoffs_authorized=True,
        source_payoff_hash="apv-paths:abc",
    )
    divisor = D("1.12") ** 5
    assert result.status is PayoffModelAmbiguityStatus.AVAILABLE
    assert result.minimum_expected_present_value == D("135") / divisor
    assert result.maximum_expected_present_value == D("227.5") / divisor
    assert result.binding_probability_vector_id == "downside_heavier"
    assert result.binding_payoff_model_case_id == "restrictive_refinancing"
    assert result.maximum_probability_vector_id == "execution_success"
    assert result.maximum_payoff_model_case_id == "observed_schedule"
    assert result.robust_entry_price == D("135") / divisor
    assert result.authorization_receipt
    assert not result.point_target_authorized
    assert not result.probability_success_claim_authorized
    assert "current_market_price" not in signature(
        calculate_robust_payoff_ambiguity_entry
    ).parameters


def test_each_cash_flow_is_discounted_from_its_actual_period():
    early = create_payoff_model_case(
        model_case_id="early-recovery",
        payoffs=(
            _path("Down", "100", period=1),
            _path("Central", "100", period=1),
            _path("Upside", "100", period=1),
        ),
        evidence_path_ids=("waterfall:year-one",),
    )
    late = create_payoff_model_case(
        model_case_id="late-exit",
        payoffs=(
            _path("Down", "100"),
            _path("Central", "100"),
            _path("Upside", "100"),
        ),
        evidence_path_ids=("terminal:year-five",),
    )
    result = calculate_robust_payoff_ambiguity_entry(
        payoff_model_cases=(early, late),
        probability_vectors=_priors(),
        policy=_policy(),
        future_payoffs_authorized=True,
        source_payoff_hash="timing-test",
    )
    assert result.binding_payoff_model_case_id == "late-exit"
    assert result.maximum_payoff_model_case_id == "early-recovery"
    assert result.minimum_expected_present_value == D("100") / (D("1.12") ** 5)
    assert result.maximum_expected_present_value == D("100") / D("1.12")


def test_dated_payoff_reconciliation_allows_only_decimal_operation_order_rounding():
    from valuation_engine.distributional_apv import PathAPVResult
    from valuation_engine.payoff_model_ambiguity import dated_payoff_from_apv_result

    terminal = D("8123.456789012345678901234567")
    shares = D("0.389669121")
    required_return = D("0.09577089965527585")
    present_value = terminal / ((D("1") + required_return) ** 5)
    result = PathAPVResult(
        path_id="large-real-world-path",
        segments=(),
        usable_tax_shields=(),
        tax_shield_present_value=D("0"),
        explicit_financing_cost_present_value=D("0"),
        operating_apv=D("0"),
        distressed=False,
        dilution_occurred=False,
        old_shareholder_retention=D("1"),
        terminal_old_equity_payoff=terminal,
        distress_old_shareholder_recovery=None,
        old_shareholder_present_value=present_value,
        value_per_initial_share=present_value / shares,
        initial_shares=shares,
        distributions_to_old_holders=(D("0"),) * 5,
        realized_periods=5,
        equity_required_return=required_return,
        path_calculation_hash="path:large-real-world",
    )

    payoff = dated_payoff_from_apv_result(branch_id="Central", result=result)
    assert payoff.cash_flows[-1].amount_per_share == terminal / shares
    with pytest.raises(PayoffModelAmbiguityError, match="does not reconcile"):
        dated_payoff_from_apv_result(
            branch_id="Central",
            result=replace(result, value_per_initial_share=result.value_per_initial_share + D("1")),
        )


def test_unauthorized_payoff_models_expose_diagnostics_but_no_entry_or_receipt():
    result = calculate_robust_payoff_ambiguity_entry(
        payoff_model_cases=_cases(),
        probability_vectors=_priors(),
        policy=_policy(),
        future_payoffs_authorized=False,
        source_payoff_hash="apv-paths:abc",
    )
    assert result.status is PayoffModelAmbiguityStatus.WITHHELD
    assert result.minimum_expected_present_value > D("0")
    assert result.robust_entry_price is None
    assert result.authorization_receipt is None
    assert result.withheld_reason == "FUTURE_PAYOFF_MODELS_NOT_AUTHORIZED"


def test_negative_present_value_cannot_be_relabeled_as_future_shareholder_payoff():
    invalid = create_payoff_model_case(
        model_case_id="signed-pv",
        payoffs=(
            _path("Down", "-1"),
            _path("Central", "1"),
            _path("Upside", "2"),
        ),
        evidence_path_ids=("value:signed",),
    )
    with pytest.raises(PayoffModelAmbiguityError, match="cannot be negative"):
        calculate_robust_payoff_ambiguity_entry(
            payoff_model_cases=(invalid,),
            probability_vectors=_priors(),
            policy=_policy(),
            future_payoffs_authorized=True,
            source_payoff_hash="signed-pv",
        )


def test_every_model_case_must_cover_the_same_complete_branch_set():
    incomplete = create_payoff_model_case(
        model_case_id="incomplete",
        payoffs=(_path("Down", "0"), _path("Central", "100")),
        evidence_path_ids=("model:incomplete",),
    )
    with pytest.raises(PayoffModelAmbiguityError, match="exact branch set"):
        calculate_robust_payoff_ambiguity_entry(
            payoff_model_cases=(_cases()[0], incomplete),
            probability_vectors=_priors(),
            policy=_policy(),
            future_payoffs_authorized=True,
            source_payoff_hash="apv-paths:abc",
        )


def test_distinct_model_cases_may_converge_to_the_same_payoff_vector():
    duplicate = create_payoff_model_case(
        model_case_id="duplicate",
        payoffs=_cases()[0].payoffs,
        evidence_path_ids=("model:duplicate",),
    )
    result = calculate_robust_payoff_ambiguity_entry(
        payoff_model_cases=(_cases()[0], duplicate),
        probability_vectors=_priors(),
        policy=_policy(),
        future_payoffs_authorized=True,
        source_payoff_hash="apv-paths:abc",
    )
    assert result.status is PayoffModelAmbiguityStatus.AVAILABLE
    assert len(result.combination_results) == len(_priors()) * 2
    downside_pairs = tuple(
        item
        for item in result.combination_results
        if item.probability_vector_id == "downside_heavier"
    )
    assert {item.expected_undiscounted_payoff for item in downside_pairs} == {D("195")}
    assert len({item.expected_present_value for item in downside_pairs}) == 1


def test_probability_vector_still_has_to_cover_every_payoff_branch():
    incomplete = (
        ProbabilityVector(
            "incomplete", (("Down", D("1")),), ("prior:incomplete",)
        ),
        _priors()[1],
    )
    with pytest.raises(PayoffModelAmbiguityError, match="exact outcome set"):
        calculate_robust_payoff_ambiguity_entry(
            payoff_model_cases=_cases(),
            probability_vectors=incomplete,
            policy=_policy(),
            future_payoffs_authorized=True,
            source_payoff_hash="apv-paths:abc",
        )


def test_model_case_hash_must_replay_after_any_payoff_edit():
    tampered = replace(
        _cases()[0],
        payoffs=(
            _path("Down", "51"),
            _path("Central", "200"),
            _path("Upside", "400"),
        ),
    )
    with pytest.raises(PayoffModelAmbiguityError, match="hash does not replay"):
        calculate_robust_payoff_ambiguity_entry(
            payoff_model_cases=(tampered,),
            probability_vectors=_priors(),
            policy=_policy(),
            future_payoffs_authorized=True,
            source_payoff_hash="apv-paths:abc",
        )


def test_audit_replays_calculation_route_and_payoff_receipt():
    cases = _cases()
    priors = _priors()
    policy = _policy()
    result = calculate_robust_payoff_ambiguity_entry(
        payoff_model_cases=cases,
        probability_vectors=priors,
        policy=policy,
        future_payoffs_authorized=True,
        source_payoff_hash="apv-paths:abc",
    )
    assert result.authorization_receipt is not None
    request = DistributionRouteRequest(
        route=DistributionIntegrationRoute.PRIOR_AMBIGUITY_VALUE_RANGE,
        economic_archetypes=("capacity_yield_levered",),
        scenario_assignment_method=NO_SCENARIO_ASSIGNMENT,
        evidence_path_ids=("driver:panel", "financing:schedules"),
        signed_values_authorized=True,
        ambiguity_set_validated=True,
        ambiguity_vector_count=len(priors),
        future_shareholder_payoffs_authorized=True,
        future_payoff_authorization_receipt=result.authorization_receipt,
        payoff_horizon_years=policy.horizon_years,
        payoff_model_case_count=len(cases),
    )
    authorization = authorize_distribution_route(request)
    audit = audit_robust_payoff_ambiguity(
        result=result,
        payoff_model_cases=cases,
        probability_vectors=priors,
        policy=policy,
        future_payoffs_authorized=True,
        source_payoff_hash="apv-paths:abc",
        route_request=request,
        route_authorization=authorization,
    )
    assert audit.passed
    assert all(item.passed for item in audit.findings)
    assert audit.audit_hash


def test_audit_fails_if_route_receipt_is_not_the_calculation_receipt():
    cases = _cases()
    priors = _priors()
    policy = _policy()
    result = calculate_robust_payoff_ambiguity_entry(
        payoff_model_cases=cases,
        probability_vectors=priors,
        policy=policy,
        future_payoffs_authorized=True,
        source_payoff_hash="apv-paths:abc",
    )
    request = DistributionRouteRequest(
        route=DistributionIntegrationRoute.PRIOR_AMBIGUITY_VALUE_RANGE,
        economic_archetypes=("capacity_yield_levered",),
        scenario_assignment_method=NO_SCENARIO_ASSIGNMENT,
        evidence_path_ids=("driver:panel", "financing:schedules"),
        signed_values_authorized=True,
        ambiguity_set_validated=True,
        ambiguity_vector_count=len(priors),
        future_shareholder_payoffs_authorized=True,
        future_payoff_authorization_receipt="wrong-receipt",
        payoff_horizon_years=policy.horizon_years,
        payoff_model_case_count=len(cases),
    )
    authorization = authorize_distribution_route(request)
    audit = audit_robust_payoff_ambiguity(
        result=result,
        payoff_model_cases=cases,
        probability_vectors=priors,
        policy=policy,
        future_payoffs_authorized=True,
        source_payoff_hash="apv-paths:abc",
        route_request=request,
        route_authorization=authorization,
    )
    assert not audit.passed
    receipt = next(
        item for item in audit.findings if item.check == "payoff_receipt_binding"
    )
    assert not receipt.passed
