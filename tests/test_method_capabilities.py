from dataclasses import replace
from pathlib import Path

import pytest

from valuation_engine.live_readiness import (
    LivePrimaryReadinessReport,
    LiveReadinessStatus,
    load_live_primary_readiness,
    validate_method_readiness_alignment,
)
from valuation_engine.method_capabilities import MethodKind, MethodRuntimeStatus, load_method_capability_registry

ROOT = Path(__file__).resolve().parents[1]
METHOD_REGISTRY = ROOT / "config" / "valuation_method_capability_registry.yaml"
ARCHETYPE_REGISTRY = ROOT / "config" / "archetype_module_registry.yaml"
READINESS_REGISTRY = ROOT / "config" / "live_primary_readiness.yaml"
STAGE_REGISTRY = ROOT / "config" / "control_plane_stage_registry.yaml"


def registry():
    value = load_method_capability_registry(METHOD_REGISTRY)
    value.validate(archetype_registry_path=ARCHETYPE_REGISTRY, repo_root=ROOT)
    return value


def test_method_capability_registry_covers_every_exact_binding_once():
    summary = registry().coverage_summary()
    assert summary.total == 41
    assert len(summary.runtime_ready) == 16
    assert len(summary.partial_runtime) == 14
    assert len(summary.not_implemented) == 11


def test_execution_roles_are_separate_from_segment_model_keys():
    value = registry()
    warranted = value.get("capacity_manufacturing", "warranted_per")
    sotp = value.get("project_finance", "sotp")
    driver = value.get("capacity_manufacturing", "driver_dcf")
    assert warranted.kind is MethodKind.CROSS_METHOD_ENGINE
    assert warranted.execution_family == "warranted_per"
    assert warranted.runtime_status is MethodRuntimeStatus.RUNTIME_READY
    assert warranted.stage == "HIERARCHICAL_WARRANTED_PER"
    assert sotp.kind is MethodKind.AGGREGATOR
    assert sotp.execution_family == "sotp"
    assert driver.kind is MethodKind.SEGMENT_EVALUATOR
    assert driver.execution_family == "explicit_fcff_dcf"
    assert driver.runtime_status is MethodRuntimeStatus.PARTIAL_RUNTIME


def test_same_method_name_remains_distinct_across_archetypes():
    value = registry()
    reit_nav = value.get("asset_yield_nav", "nav")
    reserve_nav = value.get("reserve_depletion", "nav")
    assert reit_nav.identity != reserve_nav.identity
    assert reit_nav.output_kind == "equity_value"
    assert reserve_nav.output_kind == "unresolved"


def test_deterministic_readiness_uses_exact_method_coverage():
    report = load_live_primary_readiness(
        readiness_path=READINESS_REGISTRY,
        stage_registry_path=STAGE_REGISTRY,
        method_capability_path=METHOD_REGISTRY,
        archetype_registry_path=ARCHETYPE_REGISTRY,
        repo_root=ROOT,
    )
    coverage = report.deterministic_method_coverage
    assert coverage is not None
    assert coverage.total == 31
    assert len(coverage.runtime_ready) == 6
    assert len(coverage.partial_runtime) == 14
    assert len(coverage.not_implemented) == 11
    assert "asset_yield_nav/nav" in coverage.not_implemented
    assert "reserve_depletion/nav" in coverage.not_implemented


def test_false_live_ready_promotion_is_rejected_while_methods_are_incomplete():
    value = registry()
    report = load_live_primary_readiness(
        readiness_path=READINESS_REGISTRY,
        stage_registry_path=STAGE_REGISTRY,
    )
    promoted = LivePrimaryReadinessReport(
        tuple(
            replace(item, status=LiveReadinessStatus.LIVE_READY)
            if item.stage == "DETERMINISTIC_VALUATION"
            else item
            for item in report.stages
        )
    )
    with pytest.raises(ValueError, match="cannot be promoted above PARTIAL_LIVE"):
        validate_method_readiness_alignment(promoted, value)


def test_incomplete_method_coverage_allows_honest_stage_downgrades():
    value = registry()
    report = load_live_primary_readiness(
        readiness_path=READINESS_REGISTRY,
        stage_registry_path=STAGE_REGISTRY,
    )
    for status in (
        LiveReadinessStatus.ADAPTER_REQUIRED,
        LiveReadinessStatus.SHADOW_ONLY,
        LiveReadinessStatus.CONDITIONAL_NOT_IMPLEMENTED,
    ):
        downgraded = LivePrimaryReadinessReport(
            tuple(
                replace(item, status=status)
                if item.stage == "DETERMINISTIC_VALUATION"
                else item
                for item in report.stages
            )
        )
        coverage = validate_method_readiness_alignment(downgraded, value)
        assert not coverage.complete


def test_method_capability_yaml_rejects_duplicate_binding_keys(tmp_path):
    path = tmp_path / "duplicate.yaml"
    path.write_text(
        """version: 1.1
execution_families:
  missing:
    kind: segment_evaluator
    runtime_status: NOT_IMPLEMENTED
    requires_beta: false
    requires_wacc: false
    canonical_refs: [config/archetype_module_registry.yaml]
bindings:
  capacity_manufacturing:
    driver_dcf: {execution_family: missing, output_kind: unresolved}
    driver_dcf: {execution_family: missing, output_kind: unresolved}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate YAML key: 'driver_dcf'"):
        load_method_capability_registry(path)
