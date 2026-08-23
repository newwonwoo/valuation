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


def test_live_readiness_does_not_mislabel_shadow_gaps_as_live():
    root = Path(__file__).resolve().parents[1]
    report = load_live_primary_readiness(
        readiness_path=root / "config" / "live_primary_readiness.yaml",
        stage_registry_path=root / "config" / "control_plane_stage_registry.yaml",
    )
    by_stage = {item.stage: item for item in report.stages}
    assert by_stage["PRIMARY_EVIDENCE_COLLECTION"].status is LiveReadinessStatus.PARTIAL_LIVE
    assert by_stage["ROCKET_INSIGHT_SCAN"].status is LiveReadinessStatus.SHADOW_ONLY
    assert by_stage["UPSTREAM_FUNDING_SCAN"].status is LiveReadinessStatus.CONDITIONAL_NOT_IMPLEMENTED
    assert by_stage["INTRINSIC_VALUE_FREEZE"].status is LiveReadinessStatus.RUNTIME_READY
    assert report.unresolved_live_stages
