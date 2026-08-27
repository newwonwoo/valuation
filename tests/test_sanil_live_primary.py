from pathlib import Path

from valuation_engine.control_plane import StageStatus
from valuation_engine.records import EvidenceSourceLayer
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
    assert market.as_of == snapshot.cutoff
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
    assert compiled.get("expansion_capex", "Core").measure.amount == 42
    assert compiled.get("uhv_property_capex", "Core").measure.amount == 69.25
    assert compiled.get("uhv_fcff_year_5", "Core").measure.amount == 42
    assert compiled.get("uhv_ramp_years", "Core").measure.amount == 2

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
    assert tuple(
        item.claim_id for item in broker_result.primary_verification_claims
    ) == (
        "B:SANIL:MIRAE:2Q26_PRIMARY_LEADS",
        "B:SANIL:MIRAE:UHV_PRIMARY_LEADS",
    )
    assert tuple(item.claim_id for item in broker_result.quarantined_claims) == (
        "B:SANIL:MIRAE:FORWARD_FORECAST",
        "B:SANIL:MIRAE:TARGET_PRICE",
    )
    assert not any(
        "securities.miraeasset.com" in item.source_ref
        for item in ledger.active()
    )
    assert result.data["intelligence_proposal"].requested_evidence

    trace_index = {
        trace.stage: index for index, trace in enumerate(result.stage_traces)
    }
    assert trace_index["INTRINSIC_VALUE_FREEZE"] < trace_index["STREET_REFERENCE_LOAD"]
    assert trace_index["INTRINSIC_VALUE_FREEZE"] < trace_index["MARKET_PRICE_LOAD"]
    assert result.data["street_comparison"].consensus.report_count == 2

    attestation = attest_controlled_run(result)
    report = render_controlled_run_report(result)
    assert attestation.passed
    assert "실행 상태: **검증·고정 완료 (`VERIFIED_FROZEN`)**" in report
    assert "Beta" in report and "WACC" in report
    assert "SANIL_SECOND_FACTORY_RAMP" in str(
        result.data["capacity_commitment_assessment"]
    )
    assert "SANIL_UHV_PROPERTY_ACQUISITION_20260826" in str(
        result.data["capacity_commitment_assessment"]
    )
    assert "산일전기" in result.data["final_report"]
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
    assert "가치평가 가정 · 위험 · 출처" in assumptions_svg
    assert "href=\"https://" in summary_svg and "href=\"https://" in assumptions_svg
    assert "SANIL_UHV_PROPERTY_ACQUISITION_20260826" in str(assessment)
    assert "no incremental Core capacity path is required" not in result.data["final_report"]

    assert (run_root / "final_report.md").exists()
    assert (tmp_path / "state" / TICKER / "current_state.json").exists()


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
    assert market.as_of == "2026-08-26"


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
