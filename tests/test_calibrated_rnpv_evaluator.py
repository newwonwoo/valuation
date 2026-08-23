from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption
from valuation_engine.control_plane import ExecutionMode
from valuation_engine.evaluator_registry import ModelKey
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.probability_calibration import CalibrationCertificate
from valuation_engine.records import CalibrationStatus
from valuation_engine.risk import HierarchicalBetaEstimate
from valuation_engine.risk_adapters import (
    LiveBetaStageResult,
    LiveCapitalStructureObservation,
    LiveWACCStageResult,
    TargetCapitalStructureMethod,
)
from valuation_engine.rnpv_evaluator import LiveRNPVRegistration, live_rnpv_registry_loader
from valuation_engine.scenario_binding import BoundScenario
from valuation_engine.wacc import WACCResult


def assumption(
    key: str,
    value: str,
    unit: str,
    path: str,
    *,
    calibration_status: CalibrationStatus | None = None,
) -> CompiledAssumption:
    return CompiledAssumption(
        key=key,
        scenario_id="Base",
        measure=Measure(Decimal(value), unit, "2026-06-30"),
        bridge_id=f"B-{key}",
        evidence_ids=(f"E-{key}",),
        hypothesis_id=f"H-{key}",
        economic_path_id=path,
        transform_id="identity_observation",
        input_evidence_hash=f"HASH-{key}",
        calibration_status=calibration_status,
    )


def live_wacc(rate: float = 0.10) -> LiveWACCStageResult:
    structure = LiveCapitalStructureObservation(
        0.75,
        0.25,
        0.22,
        TargetCapitalStructureMethod.PEER_NORMALIZED_MARKET_VALUE,
        "2026-08-22",
        ("CAPITAL:1",),
        "normalized target structure",
    )
    beta = LiveBetaStageResult(
        HierarchicalBetaEstimate(0.9, 0.01, ()),
        0.9,
        1.1,
        structure,
        ("P1", "P2", "P3", "P4"),
        ("BETA:SOURCE",),
        ("EV-BETA-1",),
        "BETA-HASH",
    )
    return LiveWACCStageResult(
        beta,
        WACCResult(0.10, 0.04, 0.75, 0.25, rate),
        None,
        ("WACC:SOURCE",),
        ("EV-CREDIT-1",),
        False,
        "WACC-HASH",
    )


def certificate(cohort: str = "clinical_poc|5y") -> CalibrationCertificate:
    return CalibrationCertificate(
        cohort,
        cohort.split("|")[0],
        cohort.split("|")[1],
        "1.0",
        "clinical-map-v1",
        "CAL-HASH",
        CalibrationStatus.CALIBRATED,
    )


def scenario(probability: str = "0.5", *, calibrated: bool = True) -> BoundScenario:
    return BoundScenario(
        "Base",
        (
            assumption("asset_unconditional_cashflow_year_0", "-50", "KRW_billion", "asset:development0"),
            assumption("asset_unconditional_cashflow_year_1", "-20", "KRW_billion", "asset:development1"),
            assumption("asset_unconditional_cashflow_year_2", "0", "KRW_billion", "asset:development2"),
            assumption("asset_unconditional_cashflow_year_3", "0", "KRW_billion", "asset:development3"),
            assumption("asset_contingent_cashflow_year_0", "0", "KRW_billion", "asset:commercial0"),
            assumption("asset_contingent_cashflow_year_1", "0", "KRW_billion", "asset:commercial1"),
            assumption("asset_contingent_cashflow_year_2", "100", "KRW_billion", "asset:commercial2"),
            assumption("asset_contingent_cashflow_year_3", "100", "KRW_billion", "asset:commercial3"),
            assumption(
                "asset_probability_of_success",
                probability,
                "ratio",
                "asset:clinical-success",
                calibration_status=CalibrationStatus.CALIBRATED if calibrated else CalibrationStatus.CALIBRATING,
            ),
        ),
    )


def registry(*, cert: CalibrationCertificate | None = None, rate: float = 0.10):
    loader = live_rnpv_registry_loader(
        registrations=(
            LiveRNPVRegistration(
                "probabilistic_pipeline",
                "rnpv",
                "asset-v1",
                3,
                "clinical_poc|5y",
                assumption_prefix="asset_",
            ),
        )
    )
    data = {"live_wacc_result": live_wacc(rate)}
    if cert is not None:
        data["probability_calibration_certificate"] = cert
    return loader(OrchestratorContext("RUN", ExecutionMode.LIVE_PRIMARY, data))


def test_rnpv_keeps_development_cost_unconditional_and_risks_commercial_cashflow():
    value = registry(cert=certificate()).evaluate(
        ModelKey("probabilistic_pipeline", "rnpv", "asset-v1"),
        scenario("0.5"),
        segment_id="asset",
    )
    expected = (
        Decimal("-50")
        + Decimal("-20") / Decimal("1.10")
        + Decimal("50") / Decimal("1.10") ** 2
        + Decimal("50") / Decimal("1.10") ** 3
    )
    assert value.value.amount == pytest.approx(expected)
    assert "asset:development0" in value.economic_path_ids
    assert "asset:clinical-success" in value.economic_path_ids
    assert "calibration:CAL-HASH:clinical_poc|5y" in value.economic_path_ids
    assert "beta:BETA-HASH:asset" in value.economic_path_ids
    assert "wacc:WACC-HASH:asset" in value.economic_path_ids


def test_higher_calibrated_success_probability_increases_rnpv():
    reg = registry(cert=certificate())
    low = reg.evaluate(ModelKey("probabilistic_pipeline", "rnpv", "asset-v1"), scenario("0.3"), segment_id="asset")
    high = reg.evaluate(ModelKey("probabilistic_pipeline", "rnpv", "asset-v1"), scenario("0.7"), segment_id="asset")
    assert high.value.amount > low.value.amount


def test_higher_wacc_reduces_rnpv_for_positive_later_commercial_cashflows():
    low_rate = registry(cert=certificate(), rate=0.08).evaluate(
        ModelKey("probabilistic_pipeline", "rnpv", "asset-v1"), scenario("0.5"), segment_id="asset"
    )
    high_rate = registry(cert=certificate(), rate=0.12).evaluate(
        ModelKey("probabilistic_pipeline", "rnpv", "asset-v1"), scenario("0.5"), segment_id="asset"
    )
    assert high_rate.value.amount < low_rate.value.amount


def test_rnpv_registry_requires_matching_calibration_certificate():
    with pytest.raises(PermissionError, match="no CalibrationCertificate"):
        registry(cert=None)
    with pytest.raises(PermissionError, match="does not match"):
        registry(cert=certificate("backlog_conversion|5y"))


def test_rnpv_rejects_uncalibrated_probability_assumption_even_with_certificate():
    reg = registry(cert=certificate())
    with pytest.raises(PermissionError, match="must be CALIBRATED"):
        reg.evaluate(
            ModelKey("probabilistic_pipeline", "rnpv", "asset-v1"),
            scenario("0.5", calibrated=False),
            segment_id="asset",
        )


def test_rnpv_has_no_generic_fallback_and_rejects_market_leakage():
    reg = registry(cert=certificate())
    with pytest.raises(KeyError, match="no exact evaluator"):
        reg.evaluate(ModelKey("probabilistic_pipeline", "generic_dcf", "1"), scenario(), segment_id="asset")

    loader = live_rnpv_registry_loader(
        registrations=(LiveRNPVRegistration("probabilistic_pipeline", "rnpv", "asset-v1", 3, "clinical_poc|5y", assumption_prefix="asset_"),)
    )
    with pytest.raises(PermissionError, match="target Street/market"):
        loader(
            OrchestratorContext(
                "LEAK",
                ExecutionMode.LIVE_PRIMARY,
                {
                    "live_wacc_result": live_wacc(),
                    "probability_calibration_certificate": certificate(),
                    "current_market_price": 100000,
                },
            )
        )
