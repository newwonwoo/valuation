from dataclasses import replace
from pathlib import Path

import pytest

from valuation_engine.live_readiness import (
    LivePrimaryReadinessReport,
    LiveReadinessStatus,
    load_live_primary_readiness,
    validate_method_readiness_alignment,
)
from valuation_engine.method_capabilities import (
    ExecutionFamily,
    MethodKind,
    MethodRuntimeStatus,
    load_method_capability_registry,
)


ROOT = Path(__file__).resolve().parents[1]
METHOD_REGISTRY = ROOT / "config" / "valuation_method_capability_registry.yaml"
ARCHETYPE_REGISTRY = ROOT / "config" / "archetype_module_registry.yaml"
READINESS_REGISTRY = ROOT / "config" / "live_primary_readiness.yaml"
STAGE_REGISTRY = ROOT / "config" / "control_plane_stage_registry.yaml"


def registry():
    value = load_method_capability_registry(METHOD_REGISTRY)
    value.validate(archetype_registry_path=ARCHETYPE_REGISTRY, repo_root=ROOT)
    return value


def test_method_capability_registry_covers_every_industry_dna_method_once():
    value = registry()
    summary = value.coverage_summary()
    assert summary.total == 28
    assert len(summary.runtime_ready) == 6
    assert len(summary.partial_runtime) == 14
    assert len(summary.not_implemented) == 8


def test_execution_roles_prevent_per_and_sotp_from_becoming_segment_model_keys():
    value = registry()
    warranted = value.get("warranted_per")
    sotp = value.get("sotp")
    driver = value.get("driver_dcf")

    assert warranted.kind is MethodKind.CROSS_METHOD_ENGINE
    assert warranted.execution_family is ExecutionFamily.WARRANTED_PER
    assert warranted.runtime_status is MethodRuntimeStatus.RUNTIME_READY
    assert warranted.stage == "HIERARCHICAL_WARRANTED_PER"

    assert sotp.kind is MethodKind.AGGREGATOR
    assert sotp.execution_family is ExecutionFamily.SOTP
    assert sotp.stage == "DETERMINISTIC_VALUATION"

    assert driver.kind is MethodKind.SEGMENT_EVALUATOR
    assert driver.execution_family is ExecutionFamily.EXPLICIT_FCFF_DCF
    assert driver.runtime_status is MethodRuntimeStatus.PARTIAL_RUNTIME


def test_deterministic_valuation_readiness_is_derived_from_method_coverage():
    report = load_live_primary_readiness(
        readiness_path=READINESS_REGISTRY,
        stage_registry_path=STAGE_REGISTRY,
        method_capability_path=METHOD_REGISTRY,
        archetype_registry_path=ARCHETYPE_REGISTRY,
        repo_root=ROOT,
    )
    coverage = report.deterministic_method_coverage
    assert coverage is not None
    assert coverage.total == 27  # warranted_per is a separate workflow engine, not a segment valuation method.
    assert len(coverage.runtime_ready) == 5
    assert len(coverage.partial_runtime) == 14
    assert len(coverage.not_implemented) == 8
    assert not coverage.complete


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
