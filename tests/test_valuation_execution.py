from decimal import Decimal

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.evaluator_registry import ModelKey
from valuation_engine.orchestrator import run_controlled_workflow
from valuation_engine.records import CalibrationStatus
from valuation_engine.scenario_binding import BoundScenario, BoundScenarioSet
from valuation_engine.valuation_adapter import deterministic_valuation_adapter
from valuation_engine.valuation_execution import (
    CompanyValuationPlan,
    SegmentValuationPlan,
    default_evaluator_registry,
    execute_company_valuation,
)


def assumption(scenario: str, key: str, value: str, unit: str, path: str) -> CompiledAssumption:
    return CompiledAssumption(
        key=key,
        scenario_id=scenario,
        measure=Measure(Decimal(value), unit, "2026-06-30"),
        bridge_id=f"B:{scenario}:{key}",
        evidence_ids=(f"E:{scenario}:{key}",),
        hypothesis_id=f"H:{scenario}:{key}",
        economic_path_id=path,
        transform_id="identity_observation",
        input_evidence_hash=f"hash:{scenario}:{key}",
    )


def scenario(name: str, ebitda: str, multiple: str, probability: str | None) -> BoundScenario:
    return BoundScenario(
        name,
        (
            assumption(name, "normalized_ebitda", ebitda, "KRW_billion", f"PATH:{name}:EBITDA"),
            assumption(name, "normalized_multiple", multiple, "multiple", f"PATH:{name}:MULTIPLE"),
            assumption(name, "ownership", "1", "ratio", f"PATH:{name}:OWNERSHIP"),
            assumption(name, "ev_adjustment", "-100", "KRW_billion", f"PATH:{name}:DEBT"),
            assumption(name, "diluted_shares", "10000000", "shares", f"PATH:{name}:SHARES"),
        ),
        Decimal(probability) if probability is not None else None,
    )


def scenario_set(*, calibrated: bool = True) -> BoundScenarioSet:
    probabilities = ("0.2", "0.5", "0.3") if calibrated else (None, None, None)
    return BoundScenarioSet(
        target_id="T",
        scenarios=(
            scenario("Bear", "80", "7", probabilities[0]),
            scenario("Base", "100", "8", probabilities[1]),
            scenario("Bull", "120", "9", probabilities[2]),
        ),
        calibration_status=CalibrationStatus.CALIBRATED if calibrated else CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=calibrated,
        scenario_set_hash="SCENARIO_HASH",
    )


def plan(*, archetype: str = "commodity_price_taker") -> CompanyValuationPlan:
    return CompanyValuationPlan(
        segments=(
            SegmentValuationPlan(
                asset_id="core",
                segment_id="core",
                model_key=ModelKey(archetype, "normalized_multiple", "1"),
                ownership_key="ownership",
                ev_to_equity_adjustment_key="ev_adjustment",
            ),
        ),
        reporting_unit="KRW",
        diluted_shares_key="diluted_shares",
    )


def test_generic_valuation_produces_scenario_and_expected_per_share_values():
    result = execute_company_valuation(
        scenario_set(calibrated=True),
        plan=plan(),
        registry=default_evaluator_registry(),
    )
    by_id = {item.scenario_id: item for item in result.scenarios}
    assert by_id["Bear"].value_per_share == Decimal("46000")
    assert by_id["Base"].value_per_share == Decimal("70000")
    assert by_id["Bull"].value_per_share == Decimal("98000")
    assert result.expected_value_per_share == Decimal("73600.0")
    assert result.valuation_hash
    assert "PATH:Base:OWNERSHIP" in by_id["Base"].economic_path_ids
    assert "PATH:Base:DEBT" in by_id["Base"].economic_path_ids
    assert "PATH:Base:SHARES" in by_id["Base"].economic_path_ids


def test_uncalibrated_scenarios_do_not_emit_expected_per_share():
    result = execute_company_valuation(
        scenario_set(calibrated=False),
        plan=plan(),
        registry=default_evaluator_registry(),
    )
    assert result.expected_value_per_share is None


def test_valuation_stage_adapter_runs_inside_control_plane():
    result = run_controlled_workflow(
        run_id="VAL-SHADOW",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("DETERMINISTIC_VALUATION",),
        adapters={
            "DETERMINISTIC_VALUATION": deterministic_valuation_adapter(
                registry=default_evaluator_registry(),
                plan=plan(),
            )
        },
        required_stages=("DETERMINISTIC_VALUATION",),
        initial_data={"bound_scenario_set": scenario_set(calibrated=True)},
    )
    assert result.blocked_reasons == ()
    assert result.stage_traces[0].status is StageStatus.PASS
    assert result.data["expected_value_per_share"] == Decimal("73600.0")
    assert result.data["valuation_hash"]


def test_missing_exact_evaluator_is_not_implemented_not_generic_fallback():
    result = run_controlled_workflow(
        run_id="VAL-NO-FALLBACK",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("DETERMINISTIC_VALUATION",),
        adapters={
            "DETERMINISTIC_VALUATION": deterministic_valuation_adapter(
                registry=default_evaluator_registry(),
                plan=plan(archetype="capacity_manufacturing"),
            )
        },
        required_stages=("DETERMINISTIC_VALUATION",),
        initial_data={"bound_scenario_set": scenario_set(calibrated=True)},
    )
    assert result.blocked_reasons
    assert result.stage_traces[0].status is StageStatus.NOT_IMPLEMENTED
