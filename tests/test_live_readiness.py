from pathlib import Path

from valuation_engine.live_readiness import LiveReadinessStatus, load_live_primary_readiness
from valuation_engine.orchestrator import load_stage_sequence


ROOT = Path(__file__).resolve().parents[1]
READINESS = ROOT / "config" / "live_primary_readiness.yaml"
STAGES = ROOT / "config" / "control_plane_stage_registry.yaml"
METHODS = ROOT / "config" / "valuation_method_capability_registry.yaml"
ARCHETYPES = ROOT / "config" / "archetype_module_registry.yaml"


def load_report():
    return load_live_primary_readiness(
        readiness_path=READINESS,
        stage_registry_path=STAGES,
        method_capability_path=METHODS,
        archetype_registry_path=ARCHETYPES,
        repo_root=ROOT,
    )


def test_live_readiness_registry_covers_every_canonical_stage_once():
    report = load_report()
    canonical = load_stage_sequence(STAGES)
    assert tuple(item.stage for item in report.stages) == canonical
    assert len(report.stages) == 32
    assert report.deterministic_method_coverage is not None


def test_live_readiness_tracks_current_gaps_without_freezing_old_shadow_labels():
    report = load_report()
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

    coverage = report.deterministic_method_coverage
    assert coverage is not None
    assert not coverage.complete
    assert coverage.not_implemented
