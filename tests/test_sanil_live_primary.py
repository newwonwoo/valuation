from pathlib import Path
from decimal import Decimal
from copy import deepcopy
import importlib.util
import json

import yaml

from valuation_engine.control_plane import StageStatus
from valuation_engine.records import EvidenceSourceLayer
from valuation_engine.probability_forecasting import ProbabilityForecastHistoryStore
from valuation_engine.report_form import attest_controlled_run, render_controlled_run_report
from valuation_engine.sanil_live_primary import (
    TARGET_ID,
    TICKER,
    build_sanil_live_primary_config,
    load_sanil_market_snapshot,
    load_sanil_snapshot,
    run_sanil_live_primary,
)


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "config" / "sanil_live_snapshot.yaml"


def test_sanil_snapshot_is_explicit_and_source_backed():
    snapshot = load_sanil_snapshot(SNAPSHOT)

    assert snapshot.company["target_id"] == TARGET_ID
    assert snapshot.company["ticker"] == TICKER
    assert snapshot.cutoff == "2026-08-26"
    assert tuple(snapshot.scenarios) == ("Down", "Core", "Bull")
    assert "market" not in snapshot.payload
    market = load_sanil_market_snapshot()
    assert market.price > 0
    assert market.as_of == "2026-08-27"
    assert market.as_of >= snapshot.cutoff
    assert market.price == 201500
    assert market.currency == "KRW"
    assert all(
        str(source["source_ref"]).startswith("https://")
        and len(str(source["document_hash"])) == 64
        for source in snapshot.sources.values()
    )


def test_sanil_risk_snapshot_uses_common_regression_contract_with_uncertainty():
    snapshot = load_sanil_snapshot(SNAPSHOT)
    peers = tuple(
        peer
        for level_peers in snapshot.risk["peers"].values()
        for peer in level_peers
    )
    peer_ids = tuple(str(peer["peer_id"]) for peer in peers)

    assert peer_ids == (
        "LS_ELECTRIC_010120",
        "HYOSUNG_HEAVY_INDUSTRIES_298040",
        "HD_HYUNDAI_ELECTRIC_267260",
        "ILJIN_ELECTRIC_103590",
        "TAIHAN_CABLE_001440",
        "CHERYONG_ELECTRIC_033100",
        "KWANGMYUNG_ELECTRIC_017040",
        "CHEIL_ELECTRIC_199820",
    )
    assert snapshot.risk["benchmark_id"] == "FDR_KOSPI_KS11"
    assert snapshot.risk["return_frequency"] == "weekly"
    assert snapshot.risk["as_of"] <= snapshot.cutoff
    assert snapshot.risk["beta_observation_end"] <= snapshot.cutoff
    assert all(
        str(peer["source_ref"]).startswith("https://finance.naver.com/")
        and str(peer["provider_ref"]).startswith(
            "https://github.com/FinanceData/FinanceDataReader"
        )
        and str(peer["capital_source_ref"]).startswith("https://")
        for peer in peers
    )
    assert all(float(peer["beta_standard_error"]) > 0 for peer in peers)
    assert all(int(peer["observations"]) >= 40 for peer in peers)
    assert all(str(peer["end_date"]) <= snapshot.cutoff for peer in peers)
    assert all(len(str(peer["price_series_hash"])) == 64 for peer in peers)
    assert all(
        "common KOSPI weekly OLS" in str(peer["estimation_method"])
        for peer in peers
    )
    assert not any(
        token in peer_id
        for peer_id in peer_ids
        for token in ("TWIN_A", "TWIN_B", "KR_INDUSTRIALS")
    )


def test_sanil_live_primary_runs_every_stage_and_emits_attested_report(tmp_path):
    result = run_sanil_live_primary(tmp_path)

    assert result.blocked_reasons == ()
    assert result.completed
    assert result.freeze_token is not None
    assert len(result.stage_traces) == 33
    assert result.stage_traces[0].stage == "COMPANY_RESOLUTION"
    assert result.stage_traces[-1].stage == "FINAL_REPORT"
    assert all(
        trace.status
        not in {
            StageStatus.NOT_IMPLEMENTED,
            StageStatus.BLOCKED,
            StageStatus.RECOVERY_REQUIRED,
            StageStatus.AWAITING_USER_DECISION,
        }
        for trace in result.stage_traces
    )

    linkage_decision = result.data["context_strength_linkage_decision"]
    assert linkage_decision.status.value == "APPLICABLE"
    assert len(linkage_decision.linkages) == 1
    linkage = linkage_decision.linkages[0]
    assert linkage.id == "CSL:SANIL:POWER_BOTTLENECK_CAPACITY"
    assert linkage.hypothesis_ids == (
        "H:SANIL:CAPACITY",
        "H:SANIL:UHV_CAPACITY",
        "H:SANIL:Core",
    )
    assert {
        "E:SANIL:orders",
        "E:SANIL:backlog",
        "E:SANIL:utilization",
        "E:SANIL:expansion_land_control",
        "E:SANIL:expansion_site_area",
        "E:SANIL:expansion_capex_committed",
    }.issubset(linkage.supporting_evidence_ids)
    assert "price" not in linkage.linkage_thesis.lower()
    assert "target" not in linkage.linkage_thesis.lower()

    for key in (
        "beta_snapshot_hash",
        "wacc_snapshot_hash",
        "capacity_commitment_assessment_hash",
        "capacity_bridge_consumption_hash",
        "capacity_scenario_binding_hash",
        "capacity_valuation_binding_hash",
        "capacity_per_binding_hash",
        "capacity_consistency_hash",
        "capacity_audit_hash",
        "broker_research_snapshot_hash",
        "broker_research_audit_hash",
        "valuation_hash",
        "audit_hash",
    ):
        assert isinstance(result.data[key], str) and result.data[key]

    beta_trace = next(
        trace
        for trace in result.stage_traces
        if trace.stage == "HIERARCHICAL_BETA_ESTIMATION"
    )
    wacc_trace = next(
        trace for trace in result.stage_traces if trace.stage == "WACC_VALIDATION"
    )
    assert beta_trace.status is StageStatus.PASS
    assert wacc_trace.status is StageStatus.PASS

    beta = result.data["live_beta_result"]
    wacc = result.data["live_wacc_result"]
    assert beta.peer_ids == tuple(
        sorted(
            (
                "LS_ELECTRIC_010120",
                "HYOSUNG_HEAVY_INDUSTRIES_298040",
                "HD_HYUNDAI_ELECTRIC_267260",
                "ILJIN_ELECTRIC_103590",
                "TAIHAN_CABLE_001440",
                "CHERYONG_ELECTRIC_033100",
                "KWANGMYUNG_ELECTRIC_017040",
                "CHEIL_ELECTRIC_199820",
            )
        )
    )
    assert any(
        source.startswith("https://finance.naver.com/")
        for source in beta.source_refs
    )
    assert wacc.beta_result.snapshot_hash == beta.snapshot_hash
    assert (
        wacc.beta_result.target_capital_structure
        == beta.target_capital_structure
    )

    ledger = result.data["evidence_ledger"]
    assert (
        ledger.get("E:SANIL:model_core_fcff_year_1").source_layer
        is EvidenceSourceLayer.ANALYST_UNDERWRITING
    )
    assert (
        ledger.get("E:SANIL:model_core_expansion_capex").source_layer
        is EvidenceSourceLayer.ANALYST_UNDERWRITING
    )
    assert (
        ledger.get("E:SANIL:beta_selection_L4_ECONOMIC_TWINS").source_layer
        is EvidenceSourceLayer.AUTHORIZED_MARKET_DATA
    )
    assert (
        ledger.get("E:SANIL:expansion_land_control").source_layer
        is EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN
    )

    valuation = result.data["generic_valuation_result"]
    values = {
        item.scenario_id: item.value_per_share for item in valuation.scenarios
    }
    assert values["Down"] < values["Core"] < values["Bull"]
    assert valuation.expected_value_per_share is None
    assert all(
        scenario.probability is None
        for scenario in result.data["bound_scenario_set"].scenarios
    )
    probability_assessment = result.data["scenario_probability_assessment"]
    assert tuple(
        item.displayed_probability for item in probability_assessment.rows
    ) == (Decimal("0.30"), Decimal("0.50"), Decimal("0.20"))
    assert not probability_assessment.numeric_weighting_allowed
    assert result.data["probability_distribution_status"] == (
        "UNCALIBRATED_PRIOR_CAPTURED"
    )
    assert result.data["probability_forecast_count"] == 2
    core = next(item for item in valuation.scenarios if item.scenario_id == "Core")
    assert f"capacity_project:SANIL_SECOND_FACTORY_RAMP:capex" in core.economic_path_ids
    assert (
        "capacity_project:SANIL_UHV_PROPERTY_ACQUISITION_20260826:capex"
        in core.economic_path_ids
    )
    assert (
        "capacity_project:SANIL_UHV_PROPERTY_ACQUISITION_20260826:capacity"
        in core.economic_path_ids
    )
    compiled = result.data["compiled_assumption_set"]
    assert compiled.get("expansion_capex", "Core").measure.amount == Decimal(
        "9.373"
    )
    assert compiled.get("uhv_property_capex", "Core").measure.amount == 69.25
    assert compiled.get("uhv_equipment_capex", "Core").measure.amount == 60
    assert compiled.get("uhv_fcff_year_5", "Core").measure.amount.quantize(
        Decimal("0.001")
    ) == Decimal("94.454")
    core_ramp_fcff = compiled.get("uhv_fcff_year_3", "Core")
    assert core_ramp_fcff.measure.amount.quantize(Decimal("0.001")) == Decimal(
        "20.383"
    )
    assert core_ramp_fcff.transform_id == "ramp_scaled_money"
    assert core_ramp_fcff.economic_path_id.endswith(":ramp")

    findings = {
        item.scanner_id: item for item in result.data["scanner_findings"]
    }
    assert findings["BACKLOG_QUALITY"].evidence_ids != findings["UTILIZATION"].evidence_ids
    assert findings["CAPEX_EXECUTION"].economic_path_ids == (
        "capacity_project:SANIL_SECOND_FACTORY_RAMP:capex",
    )
    assert findings["CANCELLATION_TERMS"].status is not None

    assessment = result.data["capacity_commitment_assessment"]
    assert assessment.core_inclusion_required_projects == (
        "SANIL_SECOND_FACTORY_RAMP",
        "SANIL_UHV_PROPERTY_ACQUISITION_20260826",
    )
    assert result.data["capacity_audit_passed"]
    assert result.data["broker_research_audit_passed"]
    broker_result = result.data["broker_research_prefreeze_result"]
    primary_claim_ids = tuple(
        item.claim_id for item in broker_result.primary_verification_claims
    )
    assert primary_claim_ids == (
        "B:SANIL:MIRAE:2Q26_PRIMARY_LEADS",
        "B:SANIL:MIRAE:UHV_PRIMARY_LEADS",
        "B:SANIL:IBK:2Q26_PRIMARY_LEADS",
        "B:SANIL:SHINHAN:ORDER_PRIMARY_LEADS",
    )
    assert tuple(item.claim_id for item in broker_result.context_claims) == (
        "B:SANIL:MIRAE:POWER_SOLUTION_CONTEXT",
    )
    # Street forecasts/targets are not merely quarantined from the LLM; they are
    # absent from the entire pre-Freeze orchestrator state and enter only through
    # STREET_REFERENCE_LOAD after Intrinsic Freeze.
    assert broker_result.quarantined_claims == ()
    prefreeze_text = repr(broker_result)
    assert "250000" not in prefreeze_text
    assert "220000" not in prefreeze_text
    assert "310000" not in prefreeze_text
    assert "270000" not in prefreeze_text
    assert result.data["broker_research_rocket_connected"]
    assert "BROKER_RESEARCH" in findings
    primary_broker_families = {
        item.broker_family for item in broker_result.primary_verification_claims
    }
    assert primary_broker_families == {
        "MiraeAssetSecurities",
        "IBKSecurities",
        "ShinhanSecurities",
    }
    broker_domains = (
        "securities.miraeasset.com",
        "yna.co.kr",
        "ibks.com",
        "shinhansec.com",
    )
    assert not any(
        any(domain in item.source_ref for domain in broker_domains)
        for item in ledger.active()
    )
    assert result.data["intelligence_proposal"].requested_evidence

    trace_index = {
        trace.stage: index for index, trace in enumerate(result.stage_traces)
    }
    assert trace_index["INTRINSIC_VALUE_FREEZE"] < trace_index["STREET_REFERENCE_LOAD"]
    assert trace_index["INTRINSIC_VALUE_FREEZE"] < trace_index["MARKET_PRICE_LOAD"]
    assert result.data["street_comparison"].consensus.report_count == 4
    korea_investment = next(
        item
        for item in result.data["street_reports"]
        if item.broker == "Korea Investment Securities"
    )
    assert korea_investment.valuation_method == "2027E EPS × PER 29x"
    assert korea_investment.estimates[0].metric == "uhv_incremental_revenue"
    assert korea_investment.estimates[0].period == "2029E"
    assert korea_investment.estimates[0].value == 200.0

    attestation = attest_controlled_run(result)
    report = render_controlled_run_report(result)
    assert attestation.passed
    assert "검증 상태" not in report
    assert "작성 근거와 계산 과정 보기" in report
    assert "베타" in report and "가중평균자본비용" in report
    assert report.index("## 투자 요약") < report.index(
        "<summary>작성 근거와 계산 과정 보기</summary>"
    )
    summary = result.data["final_report"].split("\n## 가치평가", 1)[0]
    assert all(
        f"**{field}**" in summary
        for field in (
            "투자판단",
            "현재가",
            "기준 내재가치",
            "가치평가 범위",
            "시나리오 가능성",
        )
    )
    assert all(
        f"### {block}" in summary
        for block in ("한 문장 결론", "투자포인트", "판단 변경 조건")
    )
    assert "SANIL_SECOND_FACTORY_RAMP" in str(
        result.data["capacity_commitment_assessment"]
    )
    assert "SANIL_UHV_PROPERTY_ACQUISITION_20260826" in str(
        result.data["capacity_commitment_assessment"]
    )
    assert "산일전기" in result.data["final_report"]
    assert "하방 30% · 기준 50% · 상방 20%" in result.data["final_report"]
    assert "시나리오 발생 가능성 — 미보정 분석가 사전확률" in result.data[
        "final_report"
    ]
    assert "사전에 기록한 사건 예측 — 보정 이력 적립용" in result.data[
        "final_report"
    ]
    assert "증권사별 목표가와 PRISM의 차이" in result.data["final_report"]
    assert "미래에셋증권" in result.data["final_report"]
    assert "신한투자증권" in result.data["final_report"]
    assert "한국투자증권" in result.data["final_report"]
    assert "5년차 DCF 사용 FCFF 4,516억원" in result.data["final_report"]
    assert "기존 3,572억원 + 증분 945억원" in result.data["final_report"]
    assert "PRISM 기준 내재가치는 증권사 평균 목표가보다 9.4% 낮습니다" in result.data[
        "final_report"
    ]
    assert "증설 처리" in result.data["final_report"]
    assert "신한투자증권 목표가는 IBK투자증권보다 90,000원 (40.9%) 높습니다" in result.data[
        "final_report"
    ]
    assert "생산능력 게이트에서 분류" in result.data["final_report"]
    llm_start = result.data["final_report"].index(
        "## 인공지능 인사이트 — 환경 변화 × 기업 강점"
    )
    llm_end = result.data["final_report"].index("\n## ", llm_start + 3)
    assert len(result.data["final_report"][llm_start:llm_end]) <= 1000
    run_root = tmp_path / "runs" / TICKER / "SANIL-062040-20260826"
    assert len(result.data["saved_report_visuals"]) == 2
    for filename in result.data["saved_report_visuals"]:
        assert (run_root / filename).exists()
        assert filename in result.data["final_report"]
    summary_svg = (run_root / result.data["saved_report_visuals"][0]).read_text(
        encoding="utf-8"
    )
    assumptions_svg = (run_root / result.data["saved_report_visuals"][1]).read_text(
        encoding="utf-8"
    )
    assert "회사 강점 · 투자 결론 · 가치평가" in summary_svg
    assert "특정 매수가는 만들지 않고" in summary_svg
    assert ">현재가<" in summary_svg
    assert "현재가 비교" not in summary_svg
    assert "가치평가 가정 · 위험 · 출처" in assumptions_svg
    assert "href=\"https://" in summary_svg and "href=\"https://" in assumptions_svg
    assert "SANIL_UHV_PROPERTY_ACQUISITION_20260826" in str(assessment)
    assert "no incremental Core capacity path is required" not in result.data["final_report"]

    assert (run_root / "final_report.md").exists()
    persisted_report = (run_root / "final_report.md").read_text(encoding="utf-8")
    persisted_trace = json.loads(
        (run_root / "control_plane_trace.json").read_text(encoding="utf-8")
    )
    assert persisted_report == result.data["final_report"]
    assert "<summary>작성 근거와 계산 과정 보기</summary>" in persisted_report
    assert len(persisted_trace) == 33
    assert (run_root / "scenario_probability_assessment.json").exists()
    assert (run_root / "probability_forecast_drafts.json").exists()
    history_store = ProbabilityForecastHistoryStore(tmp_path)
    assert history_store.forecast_run_count(TICKER) == 1
    history = history_store.load_ledger(TICKER)
    assert len(history.forecasts) == 2
    assert all(item.first_seen_at is not None for item in history.forecasts)
    assert result.data["probability_forecast_record_hash"]
    assert len(result.data["probability_forecast_ids"]) == 2
    assert next(
        item
        for item in attestation.checks
        if item.check_id == "probability_reporting_and_history_contract"
    ).passed
    assert (tmp_path / "state" / TICKER / "current_state.json").exists()


def test_sanil_uhv_ramp_duration_is_a_live_valuation_driver(tmp_path):
    baseline = run_sanil_live_primary(tmp_path / "baseline")
    payload = deepcopy(load_sanil_snapshot(SNAPSHOT).payload)
    payload["scenarios"]["Core"]["uhv_ramp_years"] = 4.0
    slower_snapshot = tmp_path / "sanil_slower_ramp.yaml"
    slower_snapshot.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    slower = run_sanil_live_primary(
        tmp_path / "slower",
        snapshot_path=slower_snapshot,
    )

    baseline_values = {
        item.scenario_id: item.value_per_share
        for item in baseline.data["generic_valuation_result"].scenarios
    }
    slower_values = {
        item.scenario_id: item.value_per_share
        for item in slower.data["generic_valuation_result"].scenarios
    }
    assert slower_values["Core"] < baseline_values["Core"]
    assert slower_values["Down"] == baseline_values["Down"]
    assert slower_values["Bull"] == baseline_values["Bull"]


def test_sanil_brokerage_report_integrates_august_27_update(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "run_sanil_live_primary_script",
        ROOT / "scripts" / "run_sanil_live_primary.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    report, html_report, visuals = module.render_report(tmp_path)

    assert "8월 27일 자료 반영" in report
    assert "8월 27일 신규·정정 공시는 없습니다" in report
    assert "회사 확정치가 아니라 증권사 추정치" in report
    assert "**현재가** | 201,500원 (2026-08-27)" in report
    assert "5년차 DCF 사용 FCFF 4,516억원" in report
    assert "기존 3,572억원 + 증분 945억원" in report
    assert "기준 DCF 기업가치의 84.8%가 영구가치" in report
    assert "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260826000660" in report
    assert len(visuals) == 2
    assert '<html lang="ko">' in html_report
    assert "부지가 아니라 5,026억원의 생산 슬롯을 샀다" in html_report
    assert "기준 목표가 237,906원" in html_report
    assert "전량가동 초고압 마진 민감도" in html_report
    assert "신규공장 제품별 물리적 매출 생산능력" in html_report
    assert "특수변압기" in html_report and "2,231억원" in html_report
    assert "영업현금흐름/영업이익은 76.7%" in html_report
    assert "잉여현금흐름/영업이익은 68.5%" in html_report
    assert "-1.7억원으로 사실상 제자리" in html_report
    assert "2029년 · 램프업" in html_report
    assert "2030년 · 전량가동" in html_report
    assert "4,338억원" in html_report
    assert "4,814억원" in html_report
    assert "5,066억원" in html_report
    assert "기존 CAPA" in html_report
    assert "중복 방지" in html_report
    assert "가능성 산식: 하방·기준·상방 상대점수 3:5:2" in html_report
    assert "증권사 목표가와의 차이" in html_report
    assert "인공지능 인사이트" in html_report
    assert "원문 열기 ↗" in html_report
    assert "검증 상태" not in html_report
    assert "프리즈" not in html_report
    assert "INTRINSIC_VALUE_FREEZE" not in html_report
    assert "SANIL_062040_LIVE_PRIMARY_REPORT.md" in html_report
    assert all(visual.filename in html_report for visual in visuals)


def test_sanil_config_requires_driver_dcf_and_capacity_core(tmp_path):
    config = build_sanil_live_primary_config(tmp_path)

    assert config.capacity_core_scenario_id == "Core"
    assert config.method_choices[0].method == "driver_dcf"
    assert config.require_broker_research
    assert config.providers.broker_research_loader is not None
    assert config.providers.beta_loader is not None
    assert config.providers.wacc_loader is not None
    assert config.providers.capacity_commitment_loader is not None
    assert config.providers.capacity_bridge_consumption_loader is not None
    assert config.providers.street_loader is not None
    assert config.providers.market_loader is not None
    assert not {
        "current_market_price",
        "market_price",
        "target_price",
        "street_reference",
    }.intersection(config.initial_data)
    market = config.providers.market_loader()
    assert market.price > 0
    assert market.as_of == "2026-08-27"


def test_sanil_collector_returns_only_requested_metrics():
    from valuation_engine.evidence_collection import EvidenceCollectionRequest
    from valuation_engine.sanil_live_primary import _primary_collector

    snapshot = load_sanil_snapshot(SNAPSHOT)
    batch = _primary_collector(snapshot)(
        EvidenceCollectionRequest(TARGET_ID, ("backlog", "utilization"))
    )
    assert tuple(item.metric for item in batch.records) == (
        "backlog",
        "utilization",
    )
