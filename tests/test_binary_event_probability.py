"""The binary-event route reaches the runtime through the same socket as the rest.

``probability_engine_v3`` had no caller inside the engine and no snapshot type
the SCENARIO_BUILD calibration socket would accept, which stranded it together
with the only two callers of ``simulate_scenario_posterior`` and
``build_dynamic_hierarchical_posterior``. These tests exercise the bridge that
ends that: a binding, a sealed snapshot, a canonical certificate, and an
external probability binding into a scenario set.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption, CompiledAssumptionSet
from valuation_engine.binary_event_probability import (
    PROBABILITY_SOURCE,
    BinaryEventCalibrationBinding,
    BinaryEventCalibrationError,
    BinaryEventProbabilityBlocked,
    BinaryEventProbabilityCalibrationSnapshot,
    binary_event_probability_loader,
    build_binary_event_probability_snapshot,
)
from valuation_engine.continuous_predictive_weight import PredictiveEvidenceProfile
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.dynamic_hierarchical_posterior import DataIntegrityAssessment
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.probability_adapter import (
    CALIBRATION_SNAPSHOT_CONTRACTS,
    EXTERNAL_PROBABILITY_SNAPSHOT_CONTRACTS,
    EXTERNAL_PROBABILITY_SNAPSHOT_KEYS,
    probability_calibration_load_adapter,
)
from valuation_engine.probability_calibration import CalibrationCertificate
from valuation_engine.probability_engine_v3 import (
    ProbabilityEventInput,
    ProbabilityLevelInput,
)
from valuation_engine.records import CalibrationStatus
from valuation_engine.scenario_binding import (
    ScenarioBindingSpec,
    ScenarioBindingStatus,
    bind_external_calibrated_probabilities,
    bind_scenarios,
)
from valuation_engine.scenario_posterior_monte_carlo import (
    CorrelationDependence,
    PosteriorScenarioRule,
)


COHORT = "scenario_probability|12m|binary_event_v3"
SCENARIOS = ("Bull", "Core", "Down")
AS_OF = "2026-08-29"
TARGET = "KR:DART:00164779"


def _profile(*, resolved: int = 8) -> PredictiveEvidenceProfile:
    return PredictiveEvidenceProfile(
        resolved_events=resolved,
        company_count=3,
        quarter_count=3,
        brier_skill_windows=(Decimal("-0.02"),),
        brier_skill_interval=(Decimal("-0.15"), Decimal("0.10")),
        ece=Decimal("0.12"),
        regime_similarity=Decimal("0.55"),
    )


def _events(
    integrity: DataIntegrityAssessment = DataIntegrityAssessment(),
) -> tuple[ProbabilityEventInput, ...]:
    return (
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
                ProbabilityLevelInput("semiconductor", 4, 8, "MAR-SEM", _profile(), integrity),
                ProbabilityLevelInput("memory", 1, 2, "MAR-MEM", _profile(resolved=2), integrity),
            ),
        ),
    )


RULES = (
    PosteriorScenarioRule("Bull", forbidden_event_ids=("revenue_miss", "margin_compression")),
    PosteriorScenarioRule(
        "Core", required_event_ids=("revenue_miss",), forbidden_event_ids=("margin_compression",)
    ),
    PosteriorScenarioRule("Down", required_event_ids=("margin_compression",)),
)

DEPENDENCE = CorrelationDependence(
    version="semiconductor-parent-rho-v1",
    event_ids=("revenue_miss", "margin_compression"),
    correlation_matrix=((Decimal("1"), Decimal("0.35")), (Decimal("0.35"), Decimal("1"))),
)


def _binding(**overrides) -> BinaryEventCalibrationBinding:
    defaults = dict(
        cohort_key=COHORT,
        forecast_class="semiconductor.memory.binary_event",
        horizon="12m",
        method_version="probability_engine_v3_binary_event_v1",
        mapping_version="binary_event_scenario_rules_v1",
        scenario_ids=SCENARIOS,
        outer_draws=40,
        inner_draws=50,
        seed=17,
    )
    defaults.update(overrides)
    return BinaryEventCalibrationBinding(**defaults)


def _snapshot(**overrides) -> BinaryEventProbabilityCalibrationSnapshot:
    return build_binary_event_probability_snapshot(
        binding=_binding(**overrides.pop("binding_overrides", {})),
        events=overrides.pop("events", _events()),
        scenario_rules=RULES,
        dependence=DEPENDENCE,
        as_of_date=AS_OF,
        **overrides,
    )


# ------------------------------------------------------------------- the bridge


def test_the_engine_now_produces_a_sealed_snapshot():
    snapshot = _snapshot()
    assert snapshot.status is CalibrationStatus.CALIBRATED
    assert snapshot.probability_source == PROBABILITY_SOURCE
    assert tuple(item.scenario_id for item in snapshot.estimates) == SCENARIOS
    total = sum((item.probability for item in snapshot.estimates), Decimal("0"))
    assert abs(total - Decimal("1")) <= Decimal("1e-12")
    assert not snapshot.integrity_findings


def test_that_snapshot_issues_the_canonical_certificate():
    certificate = _snapshot().certificate()
    assert isinstance(certificate, CalibrationCertificate)
    certificate.validate_for_weighting()
    assert certificate.cohort_key == COHORT
    assert certificate.dataset_hash


def test_event_lineage_survives_into_the_snapshot():
    snapshot = _snapshot()
    assert {event_id for event_id, _ in snapshot.event_snapshot_hashes} == {
        "revenue_miss",
        "margin_compression",
    }
    assert all(digest for _, digest in snapshot.event_snapshot_hashes)
    assert snapshot.simulation_hash


def test_the_snapshot_hash_covers_the_distribution():
    snapshot = _snapshot()
    head = snapshot.estimates[0]
    moved = replace(head, probability=head.probability + Decimal("0.01"))
    tampered = replace(snapshot, estimates=(moved,) + snapshot.estimates[1:])
    with pytest.raises(BinaryEventCalibrationError):
        tampered.validate()
    assert tampered.expected_hash() != snapshot.snapshot_hash

    reordered = replace(snapshot, estimates=tuple(reversed(snapshot.estimates)))
    with pytest.raises(BinaryEventCalibrationError, match="snapshot hash mismatch"):
        reordered.validate()


def test_a_different_seed_moves_the_snapshot_hash():
    first = _snapshot()
    second = _snapshot(binding_overrides={"seed": 23})
    assert first.snapshot_hash != second.snapshot_hash


# ------------------------------------------------------------------- the socket


def _adapter_result(snapshot, *, cohort: str = COHORT):
    adapter = probability_calibration_load_adapter(
        loader=lambda _: snapshot, expected_cohort_key=cohort
    )
    return adapter(OrchestratorContext("RUN", ExecutionMode.LIVE_PRIMARY, {}))


def test_the_calibration_socket_accepts_a_binary_event_snapshot():
    snapshot = _snapshot()
    result = _adapter_result(snapshot)
    assert result.status is StageStatus.PASS
    certificate = result.outputs["probability_calibration_certificate"]
    assert certificate.snapshot_hash == snapshot.snapshot_hash
    assert result.outputs["binary_event_probability_calibration_snapshot"] is snapshot
    # Each route publishes only its own key, so a continuous run's context is
    # unchanged by this route's existence.
    assert "continuous_probability_calibration_snapshot" not in result.outputs


def test_the_socket_still_enforces_the_expected_cohort():
    result = _adapter_result(_snapshot(), cohort="some_other_cohort|12m")
    assert result.status is StageStatus.BLOCKED
    assert result.blocking


def test_both_probability_routes_are_registered_contracts():
    from valuation_engine.continuous_probability_snapshot import (
        ContinuousProbabilityCalibrationSnapshot,
    )

    assert BinaryEventProbabilityCalibrationSnapshot in CALIBRATION_SNAPSHOT_CONTRACTS
    assert set(EXTERNAL_PROBABILITY_SNAPSHOT_CONTRACTS) == {
        ContinuousProbabilityCalibrationSnapshot,
        BinaryEventProbabilityCalibrationSnapshot,
    }
    assert dict(EXTERNAL_PROBABILITY_SNAPSHOT_KEYS)[
        BinaryEventProbabilityCalibrationSnapshot
    ] == "binary_event_probability_calibration_snapshot"


def test_the_provider_loader_is_what_the_runtime_calls():
    loader = binary_event_probability_loader(
        binding=_binding(),
        events=_events(),
        scenario_rules=RULES,
        dependence=DEPENDENCE,
        as_of_date=AS_OF,
    )
    result = _adapter_result(loader(None))
    assert result.status is StageStatus.PASS


# ------------------------------------------------------------- external binding


def _compiled() -> CompiledAssumptionSet:
    assumptions = tuple(
        CompiledAssumption(
            key="fcff_year_1",
            scenario_id=scenario,
            measure=Measure(Decimal("100"), "KRW_billion", AS_OF),
            bridge_id=f"B-{scenario}",
            evidence_ids=(f"E-{scenario}",),
            hypothesis_id=f"H-{scenario}",
            economic_path_id=f"fcff:{scenario}",
            transform_id="identity_observation",
            input_evidence_hash=f"HASH-{scenario}",
        )
        for scenario in SCENARIOS
    )
    return CompiledAssumptionSet(TARGET, assumptions, "assumption-set-hash")


def test_a_binary_event_snapshot_binds_as_an_external_probability_source():
    snapshot = _snapshot()
    compiled = _compiled()
    base_spec = ScenarioBindingSpec(SCENARIOS, ("fcff_year_1",))
    base = bind_scenarios(compiled, base_spec)
    assert base.status is ScenarioBindingStatus.BOUND

    spec = ScenarioBindingSpec(
        SCENARIOS,
        ("fcff_year_1",),
        None,
        COHORT,
        PROBABILITY_SOURCE,
    )
    bound = bind_external_calibrated_probabilities(
        compiled,
        base.scenario_set,
        spec,
        probabilities=snapshot.probabilities,
        calibration_certificate=snapshot.certificate(),
        probability_source=snapshot.probability_source,
    )
    assert bound.status is ScenarioBindingStatus.BOUND
    assert bound.scenario_set.numeric_weighting_allowed
    assert bound.scenario_set.calibration_status is CalibrationStatus.CALIBRATED
    assert bound.scenario_set.calibration_snapshot_hash == snapshot.snapshot_hash


def test_binding_refuses_a_snapshot_from_the_wrong_probability_source():
    snapshot = _snapshot()
    compiled = _compiled()
    base = bind_scenarios(compiled, ScenarioBindingSpec(SCENARIOS, ("fcff_year_1",)))
    spec = ScenarioBindingSpec(
        SCENARIOS,
        ("fcff_year_1",),
        None,
        COHORT,
        "continuous_financial_path_monte_carlo",
    )
    bound = bind_external_calibrated_probabilities(
        compiled,
        base.scenario_set,
        spec,
        probabilities=snapshot.probabilities,
        calibration_certificate=snapshot.certificate(),
        probability_source=snapshot.probability_source,
    )
    assert bound.status is ScenarioBindingStatus.BLOCKED


# ------------------------------------------------------------------- integrity


def test_blocked_event_evidence_refuses_to_seal_a_distribution():
    blocked = DataIntegrityAssessment(
        first_seen_valid=False,
        no_outcome_leakage=False,
    )
    with pytest.raises(BinaryEventProbabilityBlocked) as excinfo:
        _snapshot(events=_events(blocked))
    assert excinfo.value.violations
    assert excinfo.value.event_snapshot_hashes


def test_the_socket_reports_a_blocked_engine_rather_than_a_bare_load_failure():
    blocked = DataIntegrityAssessment(
        first_seen_valid=False,
        no_outcome_leakage=False,
    )

    def loader(_context):
        return build_binary_event_probability_snapshot(
            binding=_binding(),
            events=_events(blocked),
            scenario_rules=RULES,
            dependence=DEPENDENCE,
            as_of_date=AS_OF,
        )

    adapter = probability_calibration_load_adapter(
        loader=loader, expected_cohort_key=COHORT
    )
    result = adapter(OrchestratorContext("RUN", ExecutionMode.LIVE_PRIMARY, {}))
    assert result.status is StageStatus.BLOCKED
    assert "data-blocked" in result.rationale


def test_a_degraded_snapshot_refuses_to_issue_a_certificate():
    snapshot = _snapshot()
    degraded = BinaryEventProbabilityCalibrationSnapshot.build(
        binding=_binding(),
        as_of_date=AS_OF,
        estimates=snapshot.estimates,
        event_snapshot_hashes=snapshot.event_snapshot_hashes,
        simulation_hash=snapshot.simulation_hash,
        dataset_hash=snapshot.dataset_hash,
        integrity_findings=("normalised_probability_outside_interval:Down",),
    )
    assert degraded.status is CalibrationStatus.DEGRADED
    with pytest.raises(PermissionError, match="has not passed integrity calibration"):
        degraded.certificate()
    assert _adapter_result(degraded).status is StageStatus.WARNING


# --------------------------------------------------------------- binding rules


def test_scenario_rules_must_cover_exactly_the_bound_scenarios():
    with pytest.raises(BinaryEventCalibrationError, match="cover exactly the bound scenarios"):
        _snapshot(binding_overrides={"scenario_ids": ("Bull", "Core")})


def test_binding_rejects_a_single_scenario():
    with pytest.raises(BinaryEventCalibrationError, match="two distinct scenarios"):
        _binding(scenario_ids=("Core",)).validate()


def test_binding_rejects_an_empty_identity():
    with pytest.raises(BinaryEventCalibrationError, match="identity is incomplete"):
        _binding(mapping_version="").validate()


def test_binding_rejects_a_degenerate_credible_level():
    with pytest.raises(BinaryEventCalibrationError, match="credible level"):
        _binding(credible_level=Decimal("1")).validate()


def test_run_probability_engine_v3_now_has_an_engine_internal_caller():
    """The island is closed: the assembler is inside the package, not only in tests."""
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / "src" / "valuation_engine"
    callers = {
        path.name
        for path in package.glob("*.py")
        if "run_probability_engine_v3(" in path.read_text(encoding="utf-8")
        and path.name != "probability_engine_v3.py"
    }
    assert "binary_event_probability.py" in callers
