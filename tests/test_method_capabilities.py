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
    MethodKind,
    MethodRuntimeStatus,
    load_method_capability_registry,
    require_execution_family,
)

ROOT = Path(__file__).resolve().parents[1]
METHOD_REGISTRY = ROOT / "config" / "valuation_method_capability_registry.yaml"
ARCHETYPE_REGISTRY = ROOT / "config" / "archetype_module_registry.yaml"
READINESS_REGISTRY = ROOT / "config" / "live_primary_readiness.yaml"
STAGE_REGISTRY = ROOT / "config" / "control_plane_stage_registry.yaml"


def registry():
    value = load_method_capability_registry(METHOD_REGISTRY)
    value.validate(
        archetype_registry_path=ARCHETYPE_REGISTRY,
        repo_root=ROOT,
    )
    return value


def test_method_capability_registry_covers_every_exact_binding_once():
    summary = registry().coverage_summary()
    assert summary.total == 42
    assert len(summary.runtime_ready) == 28
    assert len(summary.partial_runtime) == 14
    assert summary.not_implemented == ()


def test_backlog_burn_dcf_is_a_distinct_implemented_family():
    """The backlog family is an addition; the normalized_dcf stub is untouched."""
    value = registry()
    backlog = value.get("contracted_backlog", "backlog_burn_dcf")
    stub = value.get("contracted_backlog", "normalized_dcf")
    assert backlog.execution_family == "contracted_backlog_dcf"
    assert backlog.runtime_status is MethodRuntimeStatus.RUNTIME_READY
    assert backlog.kind is MethodKind.SEGMENT_EVALUATOR
    assert backlog.output_kind == "enterprise_value"
    assert backlog.requires_beta and backlog.requires_wacc
    assert stub.execution_family == "explicit_fcff_dcf"


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
    assert reserve_nav.output_kind == "equity_value"


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
    assert coverage.total == 32
    assert len(coverage.runtime_ready) == 18
    assert len(coverage.partial_runtime) == 14
    assert coverage.not_implemented == ()
    assert "asset_yield_nav/nav" in coverage.runtime_ready
    assert "reserve_depletion/nav" in coverage.runtime_ready


def test_false_live_ready_promotion_is_rejected_while_methods_are_partial():
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
    with pytest.raises(
        ValueError,
        match="cannot be promoted above PARTIAL_LIVE",
    ):
        validate_method_readiness_alignment(promoted, value)


def test_partial_method_coverage_allows_honest_stage_downgrades():
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
        """version: 1.2
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


def test_runtime_rejects_forged_injected_capability_registry():
    value = registry()
    explicit_family = value.family("explicit_fcff_dcf")
    forged_capabilities = tuple(
        replace(
            item,
            execution_family=explicit_family.family,
            kind=explicit_family.kind,
            runtime_status=explicit_family.runtime_status,
            output_kind="enterprise_value",
            requires_beta=explicit_family.requires_beta,
            requires_wacc=explicit_family.requires_wacc,
            canonical_refs=explicit_family.canonical_refs,
            stage=explicit_family.stage,
        )
        if item.identity == ("capacity_manufacturing", "warranted_per")
        else item
        for item in value.capabilities
    )
    forged = replace(value, capabilities=forged_capabilities)

    with pytest.raises(ValueError, match="warranted_per"):
        require_execution_family(
            archetype="capacity_manufacturing",
            method="warranted_per",
            expected_family="explicit_fcff_dcf",
            registry=forged,
        )


def test_runtime_accepts_an_equivalent_validated_registry_copy():
    value = registry()
    capability = require_execution_family(
        archetype="capacity_manufacturing",
        method="driver_dcf",
        expected_family="explicit_fcff_dcf",
        registry=replace(value),
    )
    assert capability.execution_family == "explicit_fcff_dcf"
