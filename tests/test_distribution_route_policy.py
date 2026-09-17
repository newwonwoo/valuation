from valuation_engine.distribution_route_policy import (
    DistributionIntegrationRoute,
    DistributionRouteRequest,
    DistributionRouteStatus,
    LEGACY_NEAREST_ASSIGNMENT,
    NO_SCENARIO_ASSIGNMENT,
    authorize_distribution_route,
)


EVIDENCE = ("declarations/target_driver_panel.json",)


def test_calibrated_pathwise_route_authorizes_distribution_and_payoff_entry():
    result = authorize_distribution_route(
        DistributionRouteRequest(
            route=DistributionIntegrationRoute.PATHWISE_VALUE_DISTRIBUTION,
            economic_archetypes=("capacity_yield_levered",),
            scenario_assignment_method=NO_SCENARIO_ASSIGNMENT,
            evidence_path_ids=EVIDENCE,
            calibrated_driver_distribution=True,
            financing_waterfall_authorized=True,
            future_shareholder_payoffs_authorized=True,
            future_payoff_authorization_receipt="sha256:pathwise-payoffs",
            payoff_horizon_years=5,
            payoff_model_case_count=1000,
        )
    )
    assert result.status is DistributionRouteStatus.AUTHORIZED
    assert result.pathwise_value_distribution_authorized
    assert result.point_target_authorized
    assert result.entry_price_authorized
    assert result.success_probability_claim_authorized


def test_pathwise_route_without_financing_waterfall_cannot_publish_value():
    result = authorize_distribution_route(
        DistributionRouteRequest(
            route=DistributionIntegrationRoute.PATHWISE_VALUE_DISTRIBUTION,
            economic_archetypes=("capacity_yield_levered",),
            scenario_assignment_method=NO_SCENARIO_ASSIGNMENT,
            evidence_path_ids=EVIDENCE,
            calibrated_driver_distribution=True,
        )
    )
    assert result.status is DistributionRouteStatus.BLOCKED
    assert not result.pathwise_value_distribution_authorized
    assert "FINANCING_WATERFALL_NOT_AUTHORIZED" in result.blocking_reasons


def test_ambiguity_route_authorizes_range_but_not_single_target_or_success_claim():
    result = authorize_distribution_route(
        DistributionRouteRequest(
            route=DistributionIntegrationRoute.PRIOR_AMBIGUITY_VALUE_RANGE,
            economic_archetypes=("capacity_yield_levered",),
            scenario_assignment_method=NO_SCENARIO_ASSIGNMENT,
            evidence_path_ids=EVIDENCE,
            signed_values_authorized=True,
            ambiguity_set_validated=True,
            ambiguity_vector_count=3,
        )
    )
    assert result.status is DistributionRouteStatus.CONDITIONAL
    assert result.expected_value_interval_authorized
    assert not result.point_target_authorized
    assert not result.entry_price_authorized
    assert not result.success_probability_claim_authorized


def test_nearest_anchor_is_replay_only_and_never_authorizes_investor_outputs():
    result = authorize_distribution_route(
        DistributionRouteRequest(
            route=DistributionIntegrationRoute.LEGACY_NEAREST_ANCHOR_REPLAY,
            economic_archetypes=("capacity_manufacturing",),
            scenario_assignment_method=LEGACY_NEAREST_ASSIGNMENT,
            evidence_path_ids=EVIDENCE,
            frozen_legacy_replay_receipt="sha256:abc",
        )
    )
    assert result.status is DistributionRouteStatus.REPLAY_ONLY
    assert result.diagnostic_only
    assert not result.pathwise_value_distribution_authorized
    assert not result.expected_value_interval_authorized
    assert not result.point_target_authorized
    assert not result.entry_price_authorized
    assert not result.success_probability_claim_authorized


def test_capacity_yield_archetype_rejects_nearest_anchor_even_for_replay():
    result = authorize_distribution_route(
        DistributionRouteRequest(
            route=DistributionIntegrationRoute.LEGACY_NEAREST_ANCHOR_REPLAY,
            economic_archetypes=("capacity_yield_levered",),
            scenario_assignment_method=LEGACY_NEAREST_ASSIGNMENT,
            evidence_path_ids=EVIDENCE,
            frozen_legacy_replay_receipt="sha256:abc",
        )
    )
    assert result.status is DistributionRouteStatus.BLOCKED
    assert "CAPACITY_YIELD_ROUTE_FORBIDS_NEAREST_ANCHOR_PROBABILITY" in result.blocking_reasons


def test_scenario_anchor_assignment_cannot_enter_pathwise_route():
    result = authorize_distribution_route(
        DistributionRouteRequest(
            route=DistributionIntegrationRoute.PATHWISE_VALUE_DISTRIBUTION,
            economic_archetypes=("capacity_yield_levered",),
            scenario_assignment_method=LEGACY_NEAREST_ASSIGNMENT,
            evidence_path_ids=EVIDENCE,
            calibrated_driver_distribution=True,
            financing_waterfall_authorized=True,
            future_shareholder_payoffs_authorized=True,
            future_payoff_authorization_receipt="sha256:pathwise-payoffs",
            payoff_horizon_years=5,
            payoff_model_case_count=1000,
        )
    )
    assert result.status is DistributionRouteStatus.BLOCKED
    assert "PATHWISE_ROUTE_FORBIDS_SCENARIO_ANCHOR_ASSIGNMENT" in result.blocking_reasons


def test_future_payoff_boolean_without_evaluator_receipt_cannot_authorize_entry():
    result = authorize_distribution_route(
        DistributionRouteRequest(
            route=DistributionIntegrationRoute.PRIOR_AMBIGUITY_VALUE_RANGE,
            economic_archetypes=("capacity_yield_levered",),
            scenario_assignment_method=NO_SCENARIO_ASSIGNMENT,
            evidence_path_ids=EVIDENCE,
            signed_values_authorized=True,
            ambiguity_set_validated=True,
            ambiguity_vector_count=3,
            future_shareholder_payoffs_authorized=True,
        )
    )
    assert result.status is DistributionRouteStatus.CONDITIONAL
    assert result.expected_value_interval_authorized
    assert not result.entry_price_authorized
    assert "FUTURE_PAYOFF_AUTHORIZATION_RECEIPT_REQUIRED" in result.blocking_reasons
    assert "DATED_FUTURE_PAYOFF_HORIZON_REQUIRED" in result.blocking_reasons
    assert "COMPLETE_PAYOFF_MODEL_CASE_REQUIRED" in result.blocking_reasons


def test_ambiguity_route_accepts_engine_authorized_dated_payoff_models_for_entry():
    result = authorize_distribution_route(
        DistributionRouteRequest(
            route=DistributionIntegrationRoute.PRIOR_AMBIGUITY_VALUE_RANGE,
            economic_archetypes=("capacity_yield_levered",),
            scenario_assignment_method=NO_SCENARIO_ASSIGNMENT,
            evidence_path_ids=EVIDENCE,
            signed_values_authorized=True,
            ambiguity_set_validated=True,
            ambiguity_vector_count=3,
            future_shareholder_payoffs_authorized=True,
            future_payoff_authorization_receipt="sha256:two-layer-payoffs",
            payoff_horizon_years=5,
            payoff_model_case_count=9,
        )
    )
    assert result.status is DistributionRouteStatus.AUTHORIZED
    assert result.expected_value_interval_authorized
    assert result.entry_price_authorized
    assert not result.point_target_authorized
    assert not result.success_probability_claim_authorized
