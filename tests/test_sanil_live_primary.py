from pathlib import Path

from valuation_engine.control_plane import StageStatus
from valuation_engine.report_form import attest_controlled_run, render_controlled_run_report
from valuation_engine.sanil_live_primary import (
    TARGET_ID,
    TICKER,
    build_sanil_live_primary_config,
    load_sanil_snapshot,
    run_sanil_live_primary,
)


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "config" / "sanil_live_snapshot.yaml"


def test_sanil_snapshot_is_explicit_and_source_backed():
    snapshot = load_sanil_snapshot(SNAPSHOT)

    assert snapshot.company["target_id"] == TARGET_ID
    assert snapshot.company["ticker"] == TICKER
    assert snapshot.cutoff == "2026-08-25"
    assert tuple(snapshot.scenarios) == ("Down", "Core", "Bull")
    assert all(
        str(source["source_ref"]).startswith("https://")
        and len(str(source["document_hash"])) == 64
        for source in snapshot.sources.values()
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
        "valuation_hash",
        "audit_hash",
    ):
        assert isinstance(result.data[key], str) and result.data[key]

    beta_trace = next(
        trace for trace in result.stage_traces
        if trace.stage == "HIERARCHICAL_BETA_ESTIMATION"
    )
    wacc_trace = next(
        trace for trace in result.stage_traces
        if trace.stage == "WACC_VALIDATION"
    )
    assert beta_trace.status is StageStatus.PASS
    assert wacc_trace.status is StageStatus.PASS

    valuation = result.data["generic_valuation_result"]
    values = {
        item.scenario_id: item.value_per_share
        for item in valuation.scenarios
    }
    assert values["Down"] < values["Core"] < values["Bull"]
    assert valuation.expected_value_per_share is None

    assessment = result.data["capacity_commitment_assessment"]
    assert assessment.core_inclusion_required_projects == (
        "SANIL_SECOND_FACTORY_RAMP",
    )
    assert result.data["capacity_audit_passed"]

    attestation = attest_controlled_run(result)
    report = render_controlled_run_report(result)
    assert attestation.passed
    assert "Run status: **VERIFIED_FROZEN**" in report
    assert "Beta" in report and "WACC" in report
    assert "SANIL_SECOND_FACTORY_RAMP" in str(result.data["capacity_commitment_assessment"])
    assert "산일전기" in result.data["final_report"]

    run_root = tmp_path / "runs" / TICKER / "SANIL-062040-20260825"
    assert (run_root / "final_report.md").exists()
    assert (tmp_path / "state" / TICKER / "current_state.json").exists()


def test_sanil_config_requires_driver_dcf_and_capacity_core(tmp_path):
    config = build_sanil_live_primary_config(tmp_path)

    assert config.capacity_core_scenario_id == "Core"
    assert config.method_choices[0].method == "driver_dcf"
    assert config.providers.beta_loader is not None
    assert config.providers.wacc_loader is not None
    assert config.providers.capacity_commitment_loader is not None
    assert config.providers.capacity_bridge_consumption_loader is not None
