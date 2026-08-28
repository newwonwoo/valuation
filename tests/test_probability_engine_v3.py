from dataclasses import fields
from decimal import Decimal

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption, CompiledAssumptionSet
from valuation_engine.continuous_predictive_weight import PredictiveEvidenceProfile
from valuation_engine.dynamic_hierarchical_posterior import DataIntegrityAssessment
from valuation_engine.probability_engine_v3 import (
    ProbabilityEngineV3Result,
    ProbabilityEngineV3Spec,
    ProbabilityEngineV3Status,
    ProbabilityEventInput,
    ProbabilityLevelInput,
    apply_v3_probabilities_to_compiled_assumptions,
    run_probability_engine_v3,
)
from valuation_engine.probability_value_binding import (
    ScenarioIntrinsicValue,
    bind_frozen_probabilities_to_intrinsic_values,
)
from valuation_engine.records import CalibrationStatus
from valuation_engine.scenario_binding import ScenarioBindingSpec, bind_scenarios
from valuation_engine.scenario_posterior_monte_carlo import CorrelationDependence, PosteriorScenarioRule


def _profile(*, bss: str = "-0.02", resolved: int = 8) -> PredictiveEvidenceProfile:
    return PredictiveEvidenceProfile(
        resolved_events=resolved,
        company_count=3,
        quarter_count=3,
        brier_skill_windows=(Decimal(bss),),
        brier_skill_interval=(Decimal("-0.15"), Decimal("0.10")),
        ece=Decimal("0.12"),
        regime_similarity=Decimal("0.55"),
    )


def _spec(*, integrity: DataIntegrityAssessment = DataIntegrityAssessment()) -> ProbabilityEngineV3Spec:
    events = (
        ProbabilityEventInput(
            event_id="revenue_miss",
            root_prior_mean=Decimal("0.30"),
            root_prior_strength=Decimal("8"),
            levels=(
                ProbabilityLevelInput("semiconductor", 5, 8, "REV-SEM", _profile(), integrity),
                ProbabilityLevelInput("memory", 1, 2, "REV-MEM", _profile(resolved=2), integrity),
            ),
        ),
        ProbabilityEventInput(
            event_id="margin_compression",
            root_prior_mean=Decimal("0.40"),
            root_prior_strength=Decimal("10"),
            levels=(
                ProbabilityLevelInput("semiconductor", 4, 8, "MAR-SEM", _profile(bss="0.01"), integrity),
                ProbabilityLevelInput("memory", 1, 2, "MAR-MEM", _profile(resolved=2), integrity),
            ),
        ),
    )
    rules = (
        PosteriorScenarioRule("Bull", forbidden_event_ids=("revenue_miss", "margin_compression")),
        PosteriorScenarioRule("Core", required_event_ids=("revenue_miss",), forbidden_event_ids=("margin_compression",)),
        PosteriorScenarioRule("Down", required_event_ids=("margin_compression",)),
    )
    dependence = CorrelationDependence(
        version="semiconductor-parent-rho-v1",
        event_ids=("revenue_miss", "margin_compression"),
        correlation_matrix=((Decimal("1"), Decimal("0.35")), (Decimal("0.35"), Decimal("1"))),
    )
    return ProbabilityEngineV3Spec(
        cohort_key="scenario_probability|12m|v3",
        horizon="12m",
        events=events,
        scenario_rules=rules,
        dependence=dependence,
        outer_draws=40,
        inner_draws=50,
        seed=17,
    )


def _assumption(scenario: str) -> CompiledAssumption:
    return CompiledAssumption(
        key="scenario_probability",
        scenario_id=scenario,
        measure=Measure(Decimal("0.333333333333"), "ratio", "2026-08-29"),
        bridge_id=f"B-{scenario}",
        evidence_ids=(f"E-{scenario}",),
        hypothesis_id=f"H-{scenario}",
        economic_path_id=f"probability:{scenario}",
        transform_id="identity_observation",
        input_evidence_hash=f"HASH-{scenario}",
        calibration_status=CalibrationStatus.UNCALIBRATED,
    )


def test_v3_computes_probabilities_even_when_oos_skill_is_weak():
    result = run_probability_engine_v3(_spec())
    assert result.status is ProbabilityEngineV3Status.ESTIMATED
    assert result.numeric_weighting_allowed
    assert abs(sum(value for _, value in result.scenario_probabilities) - Decimal("1")) < Decimal("1e-12")
    assert all(lower <= dict(result.scenario_probabilities)[scenario] <= upper for scenario, lower, upper in result.scenario_intervals)
    assert all(weight.likelihood_weight > 0 for event in result.event_results for _, weight in event.level_weights)


def test_v3_probability_contract_cannot_accept_market_or_valuation_inputs():
    forbidden_tokens = {
        "price",
        "market",
        "target",
        "value",
        "valuation",
        "intrinsic",
        "return",
        "entry",
        "upside",
    }
    spec_names = {item.name.lower() for item in fields(ProbabilityEngineV3Spec)}
    result_names = {item.name.lower() for item in fields(ProbabilityEngineV3Result)}
    for name in spec_names | result_names:
        assert not any(token in name for token in forbidden_tokens), name


def test_v3_value_binding_occurs_only_after_probability_snapshot_is_frozen():
    result = run_probability_engine_v3(_spec())
    frozen_probabilities = result.scenario_probabilities
    frozen_snapshot_hash = result.snapshot_hash
    low_values = (
        ScenarioIntrinsicValue("Down", Decimal("100")),
        ScenarioIntrinsicValue("Core", Decimal("200")),
        ScenarioIntrinsicValue("Bull", Decimal("300")),
    )
    high_values = (
        ScenarioIntrinsicValue("Down", Decimal("1000000")),
        ScenarioIntrinsicValue("Core", Decimal("9000000")),
        ScenarioIntrinsicValue("Bull", Decimal("50000000")),
    )
    low = bind_frozen_probabilities_to_intrinsic_values(result, low_values)
    high = bind_frozen_probabilities_to_intrinsic_values(result, high_values)
    assert low.intrinsic_value != high.intrinsic_value
    assert result.scenario_probabilities == frozen_probabilities
    assert result.snapshot_hash == frozen_snapshot_hash
    assert low.probability_snapshot_hash == high.probability_snapshot_hash == frozen_snapshot_hash


def test_v3_hard_blocks_only_integrity_failure():
    result = run_probability_engine_v3(_spec(integrity=DataIntegrityAssessment(no_outcome_leakage=False)))
    assert result.status is ProbabilityEngineV3Status.DATA_BLOCKED
    assert not result.numeric_weighting_allowed
    assert result.scenario_probabilities == ()
    assert result.integrity_violations


def test_v3_certificate_and_existing_scenario_binding_are_compatible():
    result = run_probability_engine_v3(_spec())
    certificate = result.certificate(cohort_key="scenario_probability|12m|v3")
    compiled = CompiledAssumptionSet(
        target_id="TEST",
        assumptions=(_assumption("Down"), _assumption("Core"), _assumption("Bull")),
        assumption_set_hash="BASE",
    )
    updated = apply_v3_probabilities_to_compiled_assumptions(compiled, result)
    assert all(item.calibration_status is CalibrationStatus.CALIBRATED for item in updated.assumptions)
    binding = bind_scenarios(
        updated,
        ScenarioBindingSpec(
            scenario_ids=("Down", "Core", "Bull"),
            required_keys=("scenario_probability",),
            probability_key="scenario_probability",
            calibration_cohort_key="scenario_probability|12m|v3",
        ),
        calibration_certificate=certificate,
        require_calibration_certificate=True,
    )
    assert binding.passed
    assert binding.scenario_set is not None
    assert binding.scenario_set.numeric_weighting_allowed
    assert binding.scenario_set.calibration_snapshot_hash == result.snapshot_hash


def test_v3_sparse_leaf_inherits_strength_instead_of_becoming_unavailable():
    spec = _spec()
    sparse_events = tuple(
        ProbabilityEventInput(
            event_id=event.event_id,
            root_prior_mean=event.root_prior_mean,
            root_prior_strength=event.root_prior_strength,
            levels=(
                ProbabilityLevelInput(
                    node_id="memory",
                    success_count=0,
                    total_count=0,
                    dataset_hash=f"{event.event_id}-EMPTY",
                    predictive_profile=PredictiveEvidenceProfile(0, 0, 0),
                ),
            ),
        )
        for event in spec.events
    )
    sparse = ProbabilityEngineV3Spec(
        cohort_key=spec.cohort_key,
        horizon=spec.horizon,
        events=sparse_events,
        scenario_rules=spec.scenario_rules,
        dependence=spec.dependence,
        outer_draws=30,
        inner_draws=40,
        seed=19,
    )
    result = run_probability_engine_v3(sparse)
    assert result.status is ProbabilityEngineV3Status.ESTIMATED
    assert result.numeric_weighting_allowed
    assert len(result.scenario_intervals) == 3
