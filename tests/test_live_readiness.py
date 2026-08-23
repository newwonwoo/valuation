from pathlib import Path

from valuation_engine.live_readiness import LiveReadinessStatus, load_live_primary_readiness
from valuation_engine.orchestrator import load_stage_sequence


def test_live_readiness_registry_covers_every_canonical_stage_once():
    root = Path(__file__).resolve().parents[1]
    report = load_live_primary_readiness(
        readiness_path=root / "config" / "live_primary_readiness.yaml",
        stage_registry_path=root / "config" / "control_plane_stage_registry.yaml",
    )
    canonical = load_stage_sequence(root / "config" / "control_plane_stage_registry.yaml")
    assert tuple(item.stage for item in report.stages) == canonical
    assert len(report.stages) == 32


def test_live_readiness_tracks_current_gaps_without_freezing_old_shadow_labels():
    root = Path(__file__).resolve().parents[1]
    report = load_live_primary_readiness(
        readiness_path=root / "config" / "live_primary_readiness.yaml",
        stage_registry_path=root / "config" / "control_plane_stage_registry.yaml",
    )
    by_stage = {item.stage: item for item in report.stages}
    assert by_stage["PRIMARY_EVIDENCE_COLLECTION"].status is LiveReadinessStatus.PARTIAL_LIVE
    assert by_stage["ROCKET_INSIGHT_SCAN"].status is LiveReadinessStatus.LIVE_READY
    assert by_stage["UPSTREAM_FUNDING_SCAN"].status is LiveReadinessStatus.LIVE_READY
    assert by_stage["HIERARCHICAL_BETA_ESTIMATION"].status is LiveReadinessStatus.PARTIAL_LIVE
    assert by_stage["WACC_VALIDATION"].status is LiveReadinessStatus.PARTIAL_LIVE
    assert by_stage["HIERARCHICAL_WARRANTED_PER"].status is LiveReadinessStatus.PARTIAL_LIVE
    assert by_stage["INTRINSIC_VALUE_FREEZE"].status is LiveReadinessStatus.RUNTIME_READY
    assert {item.stage for item in report.unresolved_live_stages} == {
        "STREET_REFERENCE_LOAD",
    }
    assert by_stage["STREET_REFERENCE_LOAD"].status is LiveReadinessStatus.ADAPTER_REQUIRED
    assert report.canonical_live_ready_count == 25
    assert len(report.partial_live_stages) == 6
