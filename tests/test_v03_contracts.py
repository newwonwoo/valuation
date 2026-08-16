from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import yaml

from valuation_engine.audit import (
    ExpansionTreatment,
    ValueContribution,
    assert_no_capex_double_count,
    assert_no_duplicate_value_paths,
)
from valuation_engine.config import load_intrinsic_company_config
from valuation_engine.engine import run_valuation
from valuation_engine.ledger import EvidenceLedger, validate_traceability
from valuation_engine.provenance import build_oci_legacy_trace
from valuation_engine.records import (
    AffectedVariable,
    AssumptionRecord,
    BridgeRecord,
    CriticalIssue,
    Direction,
    EvidenceRecord,
    EvidenceSourceLayer,
    HypothesisRecord,
    RunStatus,
)
from valuation_engine.research import RedTeamOutput, ResearcherOutput
from valuation_engine.router import IndustryModel, oci_routing_decision
from valuation_engine.scenario import ScenarioSet
from valuation_engine.state import StateStore
from valuation_engine.units import convert, usd_to_krw
from valuation_engine.workflow import run_analysis_command


CONFIG = Path(__file__).parents[1] / "examples" / "oci" / "company.yaml"


def evidence(layer=EvidenceSourceLayer.REALIZED_OR_FILING):
    return EvidenceRecord(
        "EV-1", "OCI", "metric", 1.0, "USD/kg", layer,
        "2026-08-14", "2026-08-14", "source", "source://1", "L0", 1.0, "poly",
        critical=True,
    )


def hypothesis():
    return HypothesisRecord(
        "HY-1", "causal hypothesis", ("cause", "economic variable", "value variable"),
        ("EV-1",), kill_conditions=("condition fails",),
    )


def bridge():
    return BridgeRecord(
        "BR-1", ("EV-1",), "HY-1", AffectedVariable.PRICE, Direction.UP,
        1.0, 2.0, "USD/kg", "realized ASP changed", 0.8,
        "ASP reverses", "next filing", "poly:price",
    )


def test_market_comparison_cannot_enter_intrinsic_bridge():
    ledger = EvidenceLedger((evidence(EvidenceSourceLayer.MARKET_COMPARISON),))
    with pytest.raises(ValueError, match="market_comparison"):
        validate_traceability(ledger, (hypothesis(),), (bridge(),), (AssumptionRecord("asp", "Base", 2.0, "USD/kg", "BR-1"),))


def test_policy_only_cannot_become_enterprise_price():
    ledger = EvidenceLedger((evidence(EvidenceSourceLayer.POLICY_PRIMARY_SOURCE),))
    with pytest.raises(ValueError, match="policy price"):
        validate_traceability(ledger, (hypothesis(),), (bridge(),), (AssumptionRecord("asp", "Base", 2.0, "USD/kg", "BR-1"),))


def test_policy_price_change_does_not_change_intrinsic_value(tmp_path):
    changed = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    changed["policy"]["polysilicon_mip_usd_per_kg"] = 99
    changed_path = tmp_path / "policy_changed.yaml"
    changed_path.write_text(yaml.safe_dump(changed, allow_unicode=True), encoding="utf-8")
    shares, scenarios, _ = load_intrinsic_company_config(CONFIG)
    changed_shares, changed_scenarios, _ = load_intrinsic_company_config(changed_path)
    assert run_valuation(scenarios, shares).expected_value_per_share == run_valuation(changed_scenarios, changed_shares).expected_value_per_share


def test_assumption_requires_matching_bridge_value_and_unit():
    ledger = EvidenceLedger((evidence(),))
    with pytest.raises(ValueError, match="unit"):
        validate_traceability(ledger, (hypothesis(),), (bridge(),), (AssumptionRecord("asp", "Base", 2.0, "KRW", "BR-1"),))


def test_oci_legacy_fixture_has_complete_trace():
    _, scenarios, raw = load_intrinsic_company_config(CONFIG)
    trace = build_oci_legacy_trace(raw, run_id="TEST")
    trace.validate()
    expected = 1 + len(raw["common"]) + sum(len(item) - 1 for item in raw["scenarios"])
    assert len(trace.assumptions) == expected
    ScenarioSet(tuple(scenarios)).validate()
    assert ScenarioSet(tuple(scenarios)).calibration_status.value == "UNCALIBRATED"


def test_oci_routes_holding_company_to_segments():
    decision = oci_routing_decision()
    assert decision.company_model is IndustryModel.HOLDING_COMPANY
    assert {item.segment_id for item in decision.segments} == {"polysilicon", "wafer"}


def test_unit_conversions_are_exact():
    assert convert(70, "kMT", "kg") == 70_000_000
    assert convert(11.5, "GW", "W") == 11_500_000_000
    assert convert(1.3, "KRW_trillion", "KRW") == 1_300_000_000_000
    assert usd_to_krw(2, 1420) == 2840
    with pytest.raises(ValueError, match="incompatible"):
        convert(1, "GW", "kg")


def test_duplicate_economic_value_path_is_blocked():
    first = ValueContribution("operating", ("EV-1",), "contract-1:volume", "operating")
    duplicate = ValueContribution("option", ("EV-1",), "contract-1:volume", "option")
    with pytest.raises(ValueError, match="duplicate value path"):
        assert_no_duplicate_value_paths([first, duplicate])


def test_capex_ebitda_and_funding_gap_triple_count_is_blocked():
    treatment = ExpansionTreatment("expansion-1", True, True, True)
    with pytest.raises(ValueError, match="CAPEX double count"):
        assert_no_capex_double_count([treatment])


def test_successful_workflow_loads_market_after_audit_and_promotes_state(tmp_path):
    calls = []

    def market_loader():
        calls.append("market")
        from valuation_engine.records import MarketObservation
        return MarketObservation(279000, "2026-08-14", "fixture")

    result = run_analysis_command(
        "분석시작 OCI홀딩스", config_path=CONFIG, state_root=tmp_path,
        market_loader=market_loader, analysis_date=date(2026, 8, 16), run_id="RUN-OK",
    )
    assert result.status is RunStatus.COMPLETED
    assert calls == ["market"]
    assert result.intrinsic_value_per_share == pytest.approx(291802.6044, abs=0.1)
    assert StateStore(tmp_path).load_current("010060")["last_completed_run"] == "RUN-OK"
    assert (tmp_path / "runs" / "010060" / "RUN-OK" / "final_report.md").exists()


def test_unresolved_redteam_blocks_after_three_rounds_without_market_or_promotion(tmp_path):
    calls = []

    def researcher(context):
        return ResearcherOutput(context.hypotheses, "thesis")

    def red_team(context):
        assert not hasattr(context, "market_price")
        assert not hasattr(context, "intrinsic_value")
        return RedTeamOutput((CriticalIssue("CI-1", "missing binding contract"),), "counter")

    def market_loader():
        calls.append("market")
        raise AssertionError("market loader must not run")

    result = run_analysis_command(
        "분석시작 OCI홀딩스", config_path=CONFIG, state_root=tmp_path,
        researcher=researcher, red_team=red_team, market_loader=market_loader,
        analysis_date=date(2026, 8, 16), run_id="RUN-BLOCK",
    )
    assert result.status is RunStatus.VALUATION_BLOCKED
    assert result.intrinsic_value_per_share is None
    assert result.market_price is None
    assert calls == []
    assert StateStore(tmp_path).load_current("010060") is None
    assert (tmp_path / "runs" / "010060" / "RUN-BLOCK" / "valuation.json").read_text().find("suppressed") >= 0


def test_stale_critical_evidence_blocks_before_market(tmp_path):
    called = False

    def market_loader():
        nonlocal called
        called = True
        raise AssertionError

    result = run_analysis_command(
        "분석시작 OCI홀딩스", config_path=CONFIG, state_root=tmp_path,
        market_loader=market_loader, analysis_date=date(2027, 8, 16), run_id="RUN-STALE",
    )
    assert result.status is RunStatus.VALUATION_BLOCKED
    assert called is False
    assert any("stale" in reason for reason in result.blocked_reasons)


def test_audit_failure_suppresses_value_and_market_compare(tmp_path):
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    raw["common"]["poly_capacity_kmt"] = 0
    broken = tmp_path / "broken.yaml"
    broken.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    called = False

    def market_loader():
        nonlocal called
        called = True
        raise AssertionError

    result = run_analysis_command(
        "분석시작 OCI홀딩스", config_path=broken, state_root=tmp_path / "vault",
        market_loader=market_loader, analysis_date=date(2026, 8, 16), run_id="RUN-AUDIT-FAIL",
    )
    assert result.status is RunStatus.VALUATION_BLOCKED
    assert result.intrinsic_value_per_share is None
    assert result.market_price is None
    assert called is False
    assert "suppressed" in (tmp_path / "vault" / "runs" / "010060" / "RUN-AUDIT-FAIL" / "valuation.json").read_text()


def test_run_history_is_immutable(tmp_path):
    store = StateStore(tmp_path)
    result = run_analysis_command(
        "분석시작 OCI홀딩스", config_path=CONFIG, state_root=tmp_path,
        analysis_date=date(2026, 8, 16), run_id="RUN-IMMUTABLE",
    )
    assert result.status is RunStatus.COMPLETED
    with pytest.raises(FileExistsError):
        run_analysis_command(
            "분석시작 OCI홀딩스", config_path=CONFIG, state_root=tmp_path,
            analysis_date=date(2026, 8, 16), run_id="RUN-IMMUTABLE",
        )


def test_blocked_run_preserves_last_good_state(tmp_path):
    first = run_analysis_command(
        "분석시작 OCI홀딩스", config_path=CONFIG, state_root=tmp_path,
        analysis_date=date(2026, 8, 16), run_id="RUN-GOOD",
    )
    assert first.status is RunStatus.COMPLETED

    def researcher(context):
        return ResearcherOutput(context.hypotheses, "changed but unverified thesis")

    def red_team(context):
        return RedTeamOutput((CriticalIssue("CI-2", "unresolved contradiction"),), "counter")

    blocked = run_analysis_command(
        "분석시작 OCI홀딩스", config_path=CONFIG, state_root=tmp_path,
        researcher=researcher, red_team=red_team,
        analysis_date=date(2026, 8, 16), run_id="RUN-LATER-BLOCKED",
    )
    assert blocked.status is RunStatus.VALUATION_BLOCKED
    current = StateStore(tmp_path).load_current("010060")
    assert current["last_completed_run"] == "RUN-GOOD"
    assert current["thesis"] != "changed but unverified thesis"
