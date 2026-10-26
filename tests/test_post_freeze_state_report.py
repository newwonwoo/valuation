from decimal import Decimal
from hashlib import sha256

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


def run_post_freeze(tmp_path, *, expected=None, street_reports=None):
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
            "STREET_REFERENCE_LOAD": street_reference_load_adapter(
                loader=(reports if street_reports is None else lambda: street_reports)
            ),
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
            "ledger_snapshot_hash": "LEDGER_HASH",
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
    assert result.freeze_token.ledger_snapshot_hash == "LEDGER_HASH"
    street = result.data["street_comparison"]
    market = result.data["market_comparison"]
    assert result.data["street_reports"] == reports()
    from valuation_engine.broker_comparison import broker_content_hash
    # Full semantic hash intentionally replaces the old price-only digest.
    assert result.data["street_reference_hash"] == broker_content_hash(reports())
    assert street.envelope.expected_gap is None
    assert street.envelope.get("Base").gap_per_share == Decimal("0")
    assert market.envelope.get("Base").gap_per_share == Decimal("5")
    assert market.envelope.expected_gap is None
    assert "**확률가중 기대값:** 미산출" in result.data["final_report"]
    assert result.data["saved_current_state"]["expected_value_per_share"] is None
    assert (tmp_path / "state" / "EXM" / "current_state.json").exists()
    assert (tmp_path / "runs" / "EXM" / "POST-FREEZE-1" / "final_report.md").exists()


def test_calibrated_expected_value_gets_post_freeze_comparisons(tmp_path):
    result = run_post_freeze(tmp_path, expected="72")
    assert result.blocked_reasons == ()
    assert result.data["street_comparison"].envelope.expected_gap.gap_per_share == Decimal("2")
    assert result.data["market_comparison"].envelope.expected_gap.gap_per_share == Decimal("7")
    assert "**확률가중 기대값:** 주당 72원" in result.data["final_report"]


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


def test_empty_eligible_reference_set_withholds_street_without_changing_value(tmp_path):
    """No supplied numeric target permits withholding, not a coverage claim."""
    result = run_post_freeze(tmp_path, street_reports=())
    assert result.blocked_reasons == ()
    statuses = {trace.stage: trace.status for trace in result.stage_traces}
    assert statuses["STREET_REFERENCE_LOAD"] is StageStatus.SKIPPED_NOT_APPLICABLE
    assert statuses["STREET_GAP_ANALYZER"] is StageStatus.SKIPPED_NOT_APPLICABLE
    assert statuses["FINAL_REPORT"] is StageStatus.PASS
    assert result.data["street_reports"] == ()
    assert "street_reference_hash" not in result.data
    assert "street_comparison" not in result.data
    assert result.data["generic_valuation_result"] == valuation()
    assert result.freeze_token.valuation_hash == "VALUATION_HASH"
    for trace in result.stage_traces:
        if trace.stage in {"STREET_REFERENCE_LOAD", "STREET_GAP_ANALYZER"}:
            assert "in the supplied authorized export" in trace.rationale
            assert "no sell-side coverage" not in trace.rationale
    assert "확보되지 않았습니다" in result.data["final_report"]


def test_empty_authorized_street_export_loads_as_no_eligible_numeric_references(tmp_path):
    import json as _json

    from valuation_engine.official_market_data import (
        street_loader_from_authorized_export,
    )

    path = tmp_path / "street.json"
    path.write_text(
        _json.dumps({"authorization_basis": "explicit_permission", "reports": []}),
        encoding="utf-8",
    )
    assert street_loader_from_authorized_export(str(path))() == ()
    # An export WITHOUT the authorization basis stays refused, empty or not.
    bad = tmp_path / "bad.json"
    bad.write_text(_json.dumps({"reports": []}), encoding="utf-8")
    import pytest as _pytest

    with _pytest.raises(Exception, match="authorization_basis"):
        street_loader_from_authorized_export(str(bad))()


def test_legacy_street_hash_binds_estimates_base_year_and_argument(tmp_path):
    from dataclasses import replace
    from valuation_engine.street import StreetEstimate
    original=reports()
    old=run_post_freeze(tmp_path/'base',street_reports=original)
    variants=(replace(original[0],base_year='2028'),
              replace(original[0],argument_summary='Different source-bound economic argument'),
              replace(original[0],valuation_basis_note='Different accounting perimeter'),
              replace(original[0],estimates=(StreetEstimate('revenue','2027',100,'KRW_billion'),)))
    for index,changed in enumerate(variants):
        run=run_post_freeze(tmp_path/str(index),street_reports=(changed,original[1]))
        assert run.data['street_reference_hash'] != old.data['street_reference_hash']
        assert run.freeze_token == old.freeze_token
        assert run.data['generic_valuation_result'] == old.data['generic_valuation_result']
