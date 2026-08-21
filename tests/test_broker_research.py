import pytest

from valuation_engine.broker_research import (
    BrokerAccessMode,
    BrokerClaim,
    BrokerFieldClass,
    BrokerReportType,
    BrokerSourceSpec,
    canonical_rule_eligible,
    independence_key,
    pre_freeze_allowed,
    raw_storage_allowed,
)


def claim(field, *, target=False, data=()):
    return BrokerClaim(
        claim_id="C1",
        source_id="S1",
        broker_family="BrokerA",
        report_type=BrokerReportType.INDUSTRY_DEEP_DIVE,
        field_class=field,
        industry_node="semiconductor.memory",
        statement="x",
        target_company_specific=target,
        underlying_data_families=data,
        report_date="2026-01-01",
    )


def test_target_price_and_company_forecast_are_blind_locked():
    assert not pre_freeze_allowed(claim(BrokerFieldClass.TARGET_PRICE))
    assert not pre_freeze_allowed(claim(BrokerFieldClass.TARGET_COMPANY_FORECAST))
    assert not pre_freeze_allowed(claim(BrokerFieldClass.KPI_DEFINITION, target=True))


def test_industry_structure_can_enter_candidate_knowledge_pre_freeze():
    assert pre_freeze_allowed(claim(BrokerFieldClass.INDUSTRY_DEFINITION))
    assert pre_freeze_allowed(claim(BrokerFieldClass.MECHANISM_CANDIDATE))
    assert pre_freeze_allowed(claim(BrokerFieldClass.INDUSTRY_FORECAST))


def test_entitled_research_cannot_be_marked_public_raw():
    source = BrokerSourceSpec("UBS", "UBS", BrokerAccessMode.CLIENT_PORTAL, True, "https://example.com")
    with pytest.raises(ValueError):
        source.validate()


def test_public_summary_storage_policy():
    source = BrokerSourceSpec("GS_PUBLIC", "GoldmanSachs", BrokerAccessMode.PUBLIC_SUMMARY, True, "https://example.com")
    assert raw_storage_allowed(source)


def test_underlying_dataset_prevents_fake_independence():
    a = claim(BrokerFieldClass.LEADING_INDICATOR_CANDIDATE, data=("TrendForce",))
    b = BrokerClaim(**{**a.__dict__, "claim_id": "C2", "broker_family": "BrokerB"})
    assert independence_key(a) == independence_key(b) == ("TrendForce",)


def test_broker_only_never_canonizes_rule():
    c = claim(BrokerFieldClass.MECHANISM_CANDIDATE, data=("ChannelCheckA",))
    assert not canonical_rule_eligible((c,), ())
    assert canonical_rule_eligible((c,), ("SEMI",))


from valuation_engine.broker_research import (
    AlternativeDataCandidate,
    AnalystForecastObservation,
    IndicatorRepresentativeness,
    IndicatorRepresentativenessAssessment,
    InvestorDebate,
    ProjectRealizationStage,
    forecast_calibration_score,
    project_stage_can_advance,
)


def test_indicator_representativeness_rejects_low_coverage_high_label():
    assessment = IndicatorRepresentativenessAssessment(
        "spot_dram", "realized_dram_asp", 0.03, "spot",
        IndicatorRepresentativeness.HIGH,
    )
    with pytest.raises(ValueError):
        assessment.validate()


def test_project_realization_state_machine_blocks_backwards_move():
    assert project_stage_can_advance(ProjectRealizationStage.PERMITTED, ProjectRealizationStage.UNDER_CONSTRUCTION)
    assert not project_stage_can_advance(ProjectRealizationStage.UNDER_CONSTRUCTION, ProjectRealizationStage.ANNOUNCED)


def test_forecast_calibration_is_descriptive_not_truth_status():
    obs = (
        AnalystForecastObservation("B1", "A1", "revenue", 110.0, 100.0, "2026-01-01", "2026-04-01"),
        AnalystForecastObservation("B1", "A1", "revenue", 90.0, 100.0, "2026-02-01", "2026-04-01"),
    )
    score = forecast_calibration_score(obs)
    assert score["n"] == 2
    assert score["mean_signed_error"] == 0.0
    assert score["mean_ape"] == pytest.approx(0.1)


def test_investor_debate_requires_resolution_evidence():
    debate = InvestorDebate("D1", "semiconductor.memory", "Is spot price still representative?")
    with pytest.raises(ValueError):
        debate.validate()


def test_alt_data_candidate_requires_license_posture():
    candidate = AlternativeDataCandidate(
        "DATA1", "Provider", ("ai.datacenter",), "satellite imagery",
        "US facilities", "weekly", "",
    )
    with pytest.raises(ValueError):
        candidate.validate()


def test_broker_registry_validator_script():
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    run = subprocess.run(
        [sys.executable, str(root / "scripts" / "validate_broker_research_layer.py")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
