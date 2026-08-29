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
    assert len(report.stages) == 33
    assert report.deterministic_method_coverage is not None


def test_live_readiness_records_the_provider_gaps_it_used_to_hide():
    """Stages whose provider has no company-neutral implementation are gaps.

    This assertion set previously pinned ROCKET_INSIGHT_SCAN, UPSTREAM_FUNDING_SCAN
    and the LLM staff stages as ready and asserted ``not unresolved_live_stages``,
    which is how "Explicit runtime gaps: 0" survived while nine required provider
    slots were empty. Readiness is now probe-derived; the test tracks it.
    """
    report = load_report()
    by_stage = {item.stage: item for item in report.stages}

    # Providers this repository genuinely supplies without per-company code.
    assert by_stage["HIERARCHICAL_BETA_ESTIMATION"].status is LiveReadinessStatus.LIVE_READY
    assert by_stage["WACC_VALIDATION"].status is LiveReadinessStatus.LIVE_READY
    assert by_stage["HIERARCHICAL_WARRANTED_PER"].status is LiveReadinessStatus.LIVE_READY
    assert by_stage["VALUATION_METHOD_INTENT"].status is LiveReadinessStatus.RUNTIME_READY
    assert by_stage["INTRINSIC_VALUE_FREEZE"].status is LiveReadinessStatus.RUNTIME_READY

    # Providers it does not.
    for stage in (
        "ROCKET_INSIGHT_SCAN",
        "UPSTREAM_FUNDING_SCAN",
        "RESEARCHER_A",
        "BLIND_RED_TEAM_B",
        "EVIDENCE_TO_ASSUMPTION_BRIDGE",
        "DETERMINISTIC_VALUATION",
    ):
        assert by_stage[stage].status is LiveReadinessStatus.PROVIDER_REQUIRED
        assert "PROVIDER GAP:" in by_stage[stage].reason

    assert by_stage["PRIMARY_EVIDENCE_COLLECTION"].status is LiveReadinessStatus.PARTIAL_LIVE
    assert by_stage["STREET_REFERENCE_LOAD"].status is LiveReadinessStatus.PARTIAL_LIVE

    unresolved = {item.stage for item in report.unresolved_live_stages}
    assert len(unresolved) == 10
    assert report.canonical_live_ready_count == 20
    assert len(report.partial_live_stages) == 3

    coverage = report.deterministic_method_coverage
    assert coverage is not None
    assert not coverage.complete  # DCF/rNPV families still declare PARTIAL_RUNTIME breadth.
    assert coverage.not_implemented == ()


def test_every_readiness_status_agrees_with_the_capability_probe():
    """The registry cannot drift back into optimism without this failing."""
    from valuation_engine.stage_capability import (
        DerivedCapability,
        build_stage_capability_report,
        load_stage_capability_declarations,
    )

    declarations, company_bound = load_stage_capability_declarations(
        ROOT / "config" / "stage_capability_declarations.yaml"
    )
    capability = build_stage_capability_report(
        declarations=declarations,
        company_bound_modules=company_bound,
        canonical_stages=load_stage_sequence(STAGES),
    )
    optimistic = {
        LiveReadinessStatus.LIVE_READY,
        LiveReadinessStatus.RUNTIME_READY,
        LiveReadinessStatus.PARTIAL_LIVE,
    }
    for row in load_report().stages:
        derived = capability.by_stage(row.stage).derived
        if derived is DerivedCapability.PROVIDER_REQUIRED:
            assert row.status not in optimistic, row.stage
        if row.status is LiveReadinessStatus.PROVIDER_REQUIRED:
            assert derived is DerivedCapability.PROVIDER_REQUIRED, row.stage
