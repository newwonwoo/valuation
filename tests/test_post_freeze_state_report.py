from decimal import Decimal

from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.generic_reporting import (
    final_report_adapter,
    save_state_adapter,
    thesis_delta_adapter,
)
from valuation_engine.orchestrator import run_controlled_workflow
from valuation_engine.post_freeze_adapters import (
    market_compare_adapter,
    market_price_load_adapter,
    street_gap_analyzer_adapter,
    street_reference_load_adapter,
)
from valuation_engine.records import AuditReport, MarketObservation
from valuation_engine.sotp import ScenarioEquityAggregation
from valuation_engine.street import StreetResearchReport
from valuation_engine.valuation_execution import GenericValuationResult, ScenarioPerShareValue


def valuation(*, expected=None):
    scenarios = (
        ScenarioPerShareValue("Bear", Decimal("500"), "KRW", Decimal("10"), Decimal("50"), "A", ("P1",)),
        ScenarioPerShareValue("Base", Decimal("700"), "KRW", Decimal("10"), Decimal("70"), "B", ("P2",)),
        ScenarioPerShareValue("Bull", Decimal("900"), "KRW", Decimal("10"), Decimal("90"), "C", ("P3",)),
    )
    return GenericValuationResult(
        scenarios=scenarios,
        equity_aggregation=ScenarioEquityAggregation((), None, expected is not None),
        expected_value_per_share=Decimal(str(expected)) if expected is not None else None,
        reporting_unit="KRW",
        valuation_hash="VALUATION_HASH",
    )


def reports():
    return (
        StreetResearchReport(
            broker="BrokerA",
            analyst="AnalystA",
            published_date="2026-08-01",
            target_price=60.0,
            target_price_currency="KRW",
            valuation_method="DCF",
            base_year="2027",
            estimates=(),
            source_ref="report-a",
        ),
        StreetResearchReport(
            broker="BrokerB",
            analyst="AnalystB",
            published_date="2026-08-05",
            target_price=80.0,
            target_price_currency="KRW",
            valuation_method="PER",
            base_year="2027",
            estimates=(),
            source_ref="report-b",
        ),
    )


def run_post_freeze(tmp_path, *, expected=None):
    sequence = (
        "INTRINSIC_VALUE_FREEZE",
        "STREET_REFERENCE_LOAD",
        "STREET_GAP_ANALYZER",
        "MARKET_PRICE_LOAD",
        "MARKET_COMPARE",
        "THESIS_DELTA",
        "SAVE_STATE",
        "FINAL_REPORT",
    )
    return run_controlled_workflow(
        run_id="POST-FREEZE-1",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=sequence,
        adapters={
            "STREET_REFERENCE_LOAD": street_reference_load_adapter(loader=reports),
            "STREET_GAP_ANALYZER": street_gap_analyzer_adapter(),
            "MARKET_PRICE_LOAD": market_price_load_adapter(
                loader=lambda: MarketObservation(65.0, "2026-08-23", "market-source"),
                currency="KRW",
            ),
            "MARKET_COMPARE": market_compare_adapter(),
            "THESIS_DELTA": thesis_delta_adapter(),
            "SAVE_STATE": save_state_adapter(state_root=tmp_path),
            "FINAL_REPORT": final_report_adapter(),
        },
        required_stages=sequence,
        initial_data={
            "company": "Example",
            "ticker": "EXM",
            "company_state": {"thesis": "old thesis", "last_completed_run": "PRIOR"},
            "current_thesis": "new thesis",
            "generic_valuation_result": valuation(expected=expected),
            "audit_passed": True,
            "decision_impact_completed": True,
            "generic_audit_report": AuditReport(()),
            "assumption_set_hash": "ASSUMPTION_HASH",
            "valuation_hash": "VALUATION_HASH",
            "audit_hash": "AUDIT_HASH",
            "industry_snapshot_hash": "INDUSTRY_HASH",
            "source_snapshot_hash": "SOURCE_HASH",
        },
    )


def test_post_freeze_scenario_envelope_state_and_report(tmp_path):
    result = run_post_freeze(tmp_path)
    assert result.blocked_reasons == ()
    assert result.freeze_token is not None
    street = result.data["street_comparison"]
    market = result.data["market_comparison"]
    assert street.envelope.expected_gap is None
    assert street.envelope.get("Base").gap_per_share == Decimal("0")
    assert market.envelope.get("Base").gap_per_share == Decimal("5")
    assert market.envelope.expected_gap is None
    assert "Expected Value: 미산출" in result.data["final_report"]
    assert result.data["saved_current_state"]["expected_value_per_share"] is None
    assert (tmp_path / "state" / "EXM" / "current_state.json").exists()
    assert (tmp_path / "runs" / "EXM" / "POST-FREEZE-1" / "final_report.md").exists()


def test_calibrated_expected_value_gets_post_freeze_comparisons(tmp_path):
    result = run_post_freeze(tmp_path, expected="72")
    assert result.blocked_reasons == ()
    assert result.data["street_comparison"].envelope.expected_gap.gap_per_share == Decimal("2")
    assert result.data["market_comparison"].envelope.expected_gap.gap_per_share == Decimal("7")
    assert "Expected Value: 72 KRW/share" in result.data["final_report"]


def test_post_freeze_stage_cannot_run_without_token():
    result = run_controlled_workflow(
        run_id="NO-FREEZE",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("MARKET_PRICE_LOAD",),
        adapters={
            "MARKET_PRICE_LOAD": market_price_load_adapter(
                loader=lambda: MarketObservation(65.0, "2026-08-23", "market-source"),
                currency="KRW",
            )
        },
        required_stages=("MARKET_PRICE_LOAD",),
    )
    assert result.blocked_reasons
    assert result.stage_traces[0].status is StageStatus.BLOCKED
    assert "IntrinsicFreezeToken" in result.stage_traces[0].rationale
