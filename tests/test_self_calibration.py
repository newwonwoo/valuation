"""Scenario probability fitted on the target alone.

The cohort route asks where a company sits inside its industry's distribution
and needs other companies to answer. This route asks a question one issuer can
answer about itself: given how much its own drivers have actually moved, how
far out does each declared scenario's assumed path sit?

The tests below pin the property that makes it worth having — the answer moves
with the declared assumption, so it is a measurement and not a constant — and
the refusals that keep it honest when the company has no distribution yet.
"""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

import pytest

from valuation_engine.continuous_calibration_factory import (
    CalibrationFactoryError,
    ConditioningDeclaration,
    write_artifact_files,
)
from valuation_engine.continuous_probability_assembly import (
    ContinuousCalibrationBinding,
    ContinuousCalibrationError,
    build_continuous_probability_snapshot,
    conditioning_from_mapping,
)
from valuation_engine.continuous_probability_snapshot import CalibrationStatus
from valuation_engine.self_calibration_factory import (
    DeclaredScenarioDriverPaths,
    SELF_PROBABILITY_SOURCE,
    TargetObservation,
    build_self_calibration_artifact,
)

DRIVERS = ("revenue_growth", "operating_margin")
SCENARIOS = ("Down", "Base", "Bull")
YEARS = 5
TICKER = "068270"
SOURCE = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json?corp_code=00413046"
CONDITIONING_HASH = "e" * 64
FIRST_SEEN = "2026-03-16T09:00:00+00:00"

# Eight filed years of one company on one reporting basis. Growth drifts around
# 9% and margin around 21%, both with real year-to-year movement — the spread
# the probabilities are read against.
HISTORY = [
    ("2018-12-31", "2019-03-20", 0.11, 0.185),
    ("2019-12-31", "2020-03-20", 0.06, 0.230),
    ("2020-12-31", "2021-03-19", 0.14, 0.205),
    ("2021-12-31", "2022-03-18", 0.04, 0.245),
    ("2022-12-31", "2023-03-17", 0.12, 0.190),
    ("2023-12-31", "2024-03-19", 0.08, 0.225),
    ("2024-12-31", "2025-03-18", 0.10, 0.200),
    ("2025-12-31", "2026-03-16", 0.07, 0.215),
]


def _observations(rows=None) -> tuple[TargetObservation, ...]:
    return tuple(
        TargetObservation(
            period_end=period,
            published_at=f"{published}T09:00:00+00:00",
            values=(("revenue_growth", growth), ("operating_margin", margin)),
            source_ref=SOURCE,
        )
        for period, published, growth, margin in (rows or HISTORY)
    )


def _conditioning() -> ConditioningDeclaration:
    return ConditioningDeclaration(
        values=(("revenue_growth", 0.07), ("operating_margin", 0.215)),
        source_ref=SOURCE,
        first_seen_at=FIRST_SEEN,
        source_hash=CONDITIONING_HASH,
    )


def _scenarios(bull_growth: float = 0.16) -> DeclaredScenarioDriverPaths:
    """Three declared cases. Only Bull's growth moves between tests."""
    def path(growth: float, margin: float):
        return (
            ("revenue_growth", tuple(growth for _ in range(YEARS))),
            ("operating_margin", tuple(margin for _ in range(YEARS))),
        )

    return DeclaredScenarioDriverPaths(
        paths=(
            ("Down", path(0.02, 0.185)),
            ("Base", path(0.09, 0.215)),
            ("Bull", path(bull_growth, 0.245)),
        ),
        rationale=(
            "Down/Base/Bull assume the growth and margin the declared FCFF "
            "paths are built on, taken from the filed statements each "
            "rationale cites."
        ),
    )


def _build(tmp_path: Path, scenarios: DeclaredScenarioDriverPaths, rows=None):
    result = build_self_calibration_artifact(
        observations=_observations(rows),
        conditioning=_conditioning(),
        scenarios=scenarios,
        driver_ids=DRIVERS,
        scenario_ids=SCENARIOS,
        path_length=YEARS,
        target_ticker=TICKER,
        series_basis=(
            "Consolidated IFRS revenue and operating income on one reporting "
            "basis; no merger, spin-off or restatement divides the series."
        ),
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    artifact_path = tmp_path / "self_artifact.json"
    provenance_path = tmp_path / "self_provenance.json"
    write_artifact_files(
        result, artifact_path=artifact_path, provenance_path=provenance_path
    )
    constants = result.constants
    binding = ContinuousCalibrationBinding(
        cohort_key="kr.self.celltrion|5y_path|self_v1",
        forecast_class="kr.self.continuous_financial_path",
        horizon="5y_path",
        method_version="probability_engine_v3.2_self_v1",
        mapping_version="celltrion_self_v1",
        driver_ids=DRIVERS,
        scenario_ids=SCENARIOS,
        path_length=YEARS,
        artifact_path=artifact_path,
        provenance_path=provenance_path,
        expected_artifact_sha256=constants.expected_artifact_sha256,
        expected_provenance_artifact_sha256=(
            constants.expected_provenance_artifact_sha256
        ),
        expected_dataset_sha256=constants.expected_dataset_sha256,
        expected_provenance_hash=constants.expected_provenance_hash,
        expected_source_row_count=len(rows or HISTORY),
        expected_source_company_count=1,
        excluded_ticker=TICKER,
        self_calibrated=True,
        seed=20260904,
        outer_draws=200,
        inner_draws=100,
    )
    conditioning = conditioning_from_mapping(
        {driver_id: value for driver_id, value in _conditioning().values},
        binding=binding,
        source_ref=SOURCE,
        first_seen_at=FIRST_SEEN,
        source_hash=CONDITIONING_HASH,
    )
    return build_continuous_probability_snapshot(
        binding=binding,
        conditioning=conditioning,
        as_of_date="2026-09-04",
    )


def test_one_company_calibrates_itself_and_says_which_basis_it_used(tmp_path: Path):
    snapshot = _build(tmp_path, _scenarios())
    assert snapshot.status is CalibrationStatus.CALIBRATED
    assert snapshot.probability_source == SELF_PROBABILITY_SOURCE
    assert snapshot.certificate().status is CalibrationStatus.CALIBRATED
    total = sum((item.probability for item in snapshot.estimates), Decimal("0"))
    assert abs(total - Decimal("1")) < Decimal("1e-12")


def test_one_load_bearing_driver_is_valid_for_target_history(tmp_path: Path):
    driver = ("operating_profit_growth",)
    observations = tuple(
        TargetObservation(
            period_end=period,
            published_at=f"{published}T09:00:00+00:00",
            values=(("operating_profit_growth", growth),),
            source_ref=SOURCE,
        )
        for period, published, growth, _margin in HISTORY
    )
    conditioning = ConditioningDeclaration(
        values=(("operating_profit_growth", 0.07),),
        source_ref=SOURCE,
        first_seen_at=FIRST_SEEN,
        source_hash=CONDITIONING_HASH,
    )
    scenarios = DeclaredScenarioDriverPaths(
        paths=(
            ("Down", (("operating_profit_growth", (-0.30,)),)),
            ("Base", (("operating_profit_growth", (0.00,)),)),
            ("Bull", (("operating_profit_growth", (0.30,)),)),
        ),
        rationale=(
            "The three paths are the operating-earnings shocks explicitly "
            "carried by the filed-figure valuation cases."
        ),
    )
    result = build_self_calibration_artifact(
        observations=observations,
        conditioning=conditioning,
        scenarios=scenarios,
        driver_ids=driver,
        scenario_ids=SCENARIOS,
        path_length=1,
        target_ticker=TICKER,
        series_basis=(
            "Consolidated IFRS operating income on one reporting basis; no "
            "merger, spin-off or restatement divides the series."
        ),
    )
    artifact_path = tmp_path / "one_driver_artifact.json"
    provenance_path = tmp_path / "one_driver_provenance.json"
    write_artifact_files(
        result, artifact_path=artifact_path, provenance_path=provenance_path
    )
    constants = result.constants
    binding = ContinuousCalibrationBinding(
        cohort_key="kr.self.example|1y_earnings|self_v1",
        forecast_class="kr.self.continuous_financial_path",
        horizon="1y_earnings_state",
        method_version="probability_engine_v3.2_self_v1",
        mapping_version="example_self_v1",
        driver_ids=driver,
        scenario_ids=SCENARIOS,
        path_length=1,
        artifact_path=artifact_path,
        provenance_path=provenance_path,
        expected_artifact_sha256=constants.expected_artifact_sha256,
        expected_provenance_artifact_sha256=(
            constants.expected_provenance_artifact_sha256
        ),
        expected_dataset_sha256=constants.expected_dataset_sha256,
        expected_provenance_hash=constants.expected_provenance_hash,
        expected_source_row_count=len(observations),
        expected_source_company_count=1,
        excluded_ticker=TICKER,
        self_calibrated=True,
        seed=20260904,
        outer_draws=100,
        inner_draws=100,
    )
    snapshot = build_continuous_probability_snapshot(
        binding=binding,
        conditioning=conditioning_from_mapping(
            {"operating_profit_growth": 0.07},
            binding=binding,
            source_ref=SOURCE,
            first_seen_at=FIRST_SEEN,
            source_hash=CONDITIONING_HASH,
        ),
        as_of_date="2026-09-04",
    )
    assert snapshot.status is CalibrationStatus.CALIBRATED
    assert snapshot.probability_source == SELF_PROBABILITY_SOURCE


def test_a_bull_case_the_company_has_never_run_is_scored_lower(tmp_path: Path):
    """The property that makes this a measurement rather than a constant.

    Both runs use the same company, the same eight years and the same Down and
    Base cases. Only the growth Bull assumes changes. A Bull at 16% sits far
    outside a company whose own growth has moved between 4% and 14%; a Bull at
    10.5% sits inside it. If the anchors were fitted from the same dispersion
    they are scored against, these two would return the same number.
    """
    reachable = _build(tmp_path / "near", _scenarios(bull_growth=0.105))
    stretched = _build(tmp_path / "far", _scenarios(bull_growth=0.16))

    def bull(snapshot):
        return next(
            item.probability
            for item in snapshot.estimates
            if item.scenario_id == "Bull"
        )

    assert bull(stretched) < bull(reachable)


def test_two_years_of_history_is_refused_rather_than_fitted(tmp_path: Path):
    """A company just past a merger has observations, not a distribution."""
    with pytest.raises(CalibrationFactoryError, match="own transitions"):
        _build(tmp_path, _scenarios(), rows=HISTORY[-3:])


def test_two_scenarios_assuming_the_same_path_are_one_scenario(tmp_path: Path):
    duplicated = DeclaredScenarioDriverPaths(
        paths=(
            ("Down", (("revenue_growth", (0.09,) * YEARS),
                      ("operating_margin", (0.215,) * YEARS))),
            ("Base", (("revenue_growth", (0.09,) * YEARS),
                      ("operating_margin", (0.215,) * YEARS))),
            ("Bull", (("revenue_growth", (0.16,) * YEARS),
                      ("operating_margin", (0.245,) * YEARS))),
        ),
        rationale="two of these assume exactly the same economics on purpose",
    )
    with pytest.raises(CalibrationFactoryError, match="one scenario, not two"):
        _build(tmp_path, duplicated)


def test_a_series_spanning_a_structural_break_must_be_declared(tmp_path: Path):
    with pytest.raises(CalibrationFactoryError, match="series_basis"):
        build_self_calibration_artifact(
            observations=_observations(),
            conditioning=_conditioning(),
            scenarios=_scenarios(),
            driver_ids=DRIVERS,
            scenario_ids=SCENARIOS,
            path_length=YEARS,
            target_ticker=TICKER,
            series_basis="",
        )


def test_a_peer_cohort_artifact_cannot_be_bound_as_self_calibrated(tmp_path: Path):
    """The two bases are opposite claims about the same ticker.

    A cohort artifact records the ticker it withheld; a self artifact records
    the ticker it is entirely made of. Requiring the self key present and the
    cohort key absent means neither can be read as the other, whichever way
    the binding is written.
    """
    result = build_self_calibration_artifact(
        observations=_observations(),
        conditioning=_conditioning(),
        scenarios=_scenarios(),
        driver_ids=DRIVERS,
        scenario_ids=SCENARIOS,
        path_length=YEARS,
        target_ticker=TICKER,
        series_basis=(
            "Consolidated IFRS revenue and operating income on one reporting "
            "basis; no merger, spin-off or restatement divides the series."
        ),
    )
    from valuation_engine.continuous_probability_assembly import stable_hash

    artifact = dict(result.artifact)
    artifact.pop("self_calibrated")
    artifact["target_ticker_excluded"] = TICKER
    # Re-seal it so every hash in the disguise checks out. Otherwise the hash
    # guard rejects it for being edited and this test would never reach the
    # identity guard it exists to pin.
    artifact.pop("artifact_sha256")
    artifact["artifact_sha256"] = stable_hash(artifact)
    artifact_path = tmp_path / "masquerade.json"
    provenance_path = tmp_path / "provenance.json"
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    provenance_path.write_text(
        json.dumps(result.provenance, ensure_ascii=False), encoding="utf-8"
    )
    binding = ContinuousCalibrationBinding(
        cohort_key="kr.self.celltrion|5y_path|self_v1",
        forecast_class="kr.self.continuous_financial_path",
        horizon="5y_path",
        method_version="probability_engine_v3.2_self_v1",
        mapping_version="celltrion_self_v1",
        driver_ids=DRIVERS,
        scenario_ids=SCENARIOS,
        path_length=YEARS,
        artifact_path=artifact_path,
        provenance_path=provenance_path,
        expected_artifact_sha256=artifact["artifact_sha256"],
        expected_provenance_artifact_sha256=stable_hash(result.provenance),
        expected_dataset_sha256=result.constants.expected_dataset_sha256,
        expected_provenance_hash=artifact["provenance_hash"],
        expected_source_row_count=len(HISTORY),
        expected_source_company_count=1,
        excluded_ticker=TICKER,
        self_calibrated=True,
    )
    conditioning = conditioning_from_mapping(
        {driver_id: value for driver_id, value in _conditioning().values},
        binding=binding,
        source_ref=SOURCE,
        first_seen_at=FIRST_SEEN,
        source_hash=CONDITIONING_HASH,
    )
    with pytest.raises(ContinuousCalibrationError, match="does not"):
        build_continuous_probability_snapshot(
            binding=binding,
            conditioning=conditioning,
            as_of_date="2026-09-04",
        )
