from datetime import datetime, timezone
from decimal import Decimal

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption, CompiledAssumptionSet
from valuation_engine.calibration_hierarchy import (
    CalibrationHierarchyLevel,
    CalibrationHierarchyNode,
    CalibrationHierarchyPath,
)
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.hierarchical_calibration import (
    ChildSpecializationPolicy,
    NodeCalibrationEvidence,
    ParentCalibrationPrior,
    build_hierarchical_node_calibration,
)
from valuation_engine.hierarchical_calibration_certificate import (
    build_hierarchical_calibration_snapshot,
)
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.probability_adapter import probability_calibration_load_adapter
from valuation_engine.probability_calibration import CalibrationCertificate
from valuation_engine.records import CalibrationStatus
from valuation_engine.scenario_binding import ScenarioBindingSpec, bind_scenarios


def root_certificate(snapshot_hash: str = "ROOT-SNAPSHOT") -> CalibrationCertificate:
    return CalibrationCertificate(
        "margin_compression|12m",
        "margin_compression",
        "12m",
        "1.0",
        "root-map",
        snapshot_hash,
        CalibrationStatus.CALIBRATED,
        "ROOT-DATASET",
    )


def child_policy() -> ChildSpecializationPolicy:
    return ChildSpecializationPolicy(
        version="2.0-test",
        shrinkage_version="beta_binomial_empirical_bayes/v1",
        min_resolved_events=30,
        min_companies=5,
        min_quarters=4,
        min_effective_sample_size=50,
        min_oos_windows=2,
        max_oos_brier_skill_delta_vs_parent=Decimal("0.02"),
        max_posterior_shift_without_local_promotion=Decimal("0.10"),
        parent_strength_default=40,
        parent_strength_min=10,
        parent_strength_max=200,
        parent_strength_source="training_oos_only",
    )


def path() -> CalibrationHierarchyPath:
    return CalibrationHierarchyPath(
        event_class="margin_compression",
        horizon="12m",
        mapping_version="map-v2",
        nodes=(
            CalibrationHierarchyNode(
                "global",
                CalibrationHierarchyLevel.GLOBAL_EVENT,
                "global",
                None,
                "map-v2",
            ),
            CalibrationHierarchyNode(
                "capacity",
                CalibrationHierarchyLevel.ECONOMIC_ARCHETYPE,
                "capacity_manufacturing",
                "global",
                "map-v2",
            ),
        ),
    )


def hierarchical_snapshot(root_hash: str = "ROOT-SNAPSHOT"):
    root = root_certificate(root_hash)
    child = build_hierarchical_node_calibration(
        evidence=NodeCalibrationEvidence(
            node_id="capacity",
            event_class="margin_compression",
            horizon="12m",
            resolved_events=(),
            oos_brier_skill_windows=(),
            dataset_hash="CAPACITY-DATASET",
        ),
        parent=ParentCalibrationPrior(
            probability=Decimal("0.40"),
            strength=40,
            certified=True,
            event_ids=(),
            snapshot_hash=root.snapshot_hash,
            dataset_hash=root.dataset_hash,
            oos_brier_skill_windows=(Decimal("0.10"), Decimal("0.08")),
        ),
        policy=child_policy(),
    )
    return build_hierarchical_calibration_snapshot(
        path=path(),
        root_certificate=root,
        root_probability=Decimal("0.40"),
        node_calibrations=(child,),
        policy_version="2.0-test",
        shrinkage_version="beta_binomial_empirical_bayes/v1",
    )


def probability_assumption(scenario: str, probability: str) -> CompiledAssumption:
    return CompiledAssumption(
        key="scenario_probability",
        scenario_id=scenario,
        measure=Measure(Decimal(probability), "ratio", "2026-06-30"),
        bridge_id=f"B-{scenario}",
        evidence_ids=(f"E-{scenario}",),
        hypothesis_id=f"H-{scenario}",
        economic_path_id=f"probability:{scenario}",
        transform_id="identity_observation",
        input_evidence_hash=f"HASH-{scenario}",
        calibration_status=CalibrationStatus.CALIBRATED,
    )


def compiled_probabilities() -> CompiledAssumptionSet:
    return CompiledAssumptionSet(
        "T",
        (
            probability_assumption("Bear", "0.2"),
            probability_assumption("Base", "0.5"),
            probability_assumption("Bull", "0.3"),
        ),
        "ASSUMPTION-HASH",
    )


def test_hierarchical_certificate_is_hash_bound_and_authorizable():
    snapshot = hierarchical_snapshot()
    assert snapshot.status is CalibrationStatus.CALIBRATED
    assert snapshot.final_probability == Decimal("0.40")
    certificate = snapshot.certificate()
    certificate.validate_for_weighting()
    assert certificate.ancestor_snapshot_hashes == ("ROOT-SNAPSHOT",)
    assert certificate.node_states == ("INHERITED",)
    assert certificate.lineage_hash


def test_ancestor_change_changes_hierarchical_snapshot_hash():
    first = hierarchical_snapshot("ROOT-SNAPSHOT-A")
    second = hierarchical_snapshot("ROOT-SNAPSHOT-B")
    assert first.snapshot_hash != second.snapshot_hash
    assert first.certificate().lineage_hash != second.certificate().lineage_hash


def test_probability_adapter_accepts_hierarchical_snapshot():
    snapshot = hierarchical_snapshot()
    adapter = probability_calibration_load_adapter(
        loader=lambda _: snapshot,
        expected_cohort_key="margin_compression|12m",
    )
    result = adapter(
        OrchestratorContext("RUN", ExecutionMode.LIVE_PRIMARY, {})
    )
    assert result.status is StageStatus.PASS
    assert result.outputs["probability_calibration_certificate"].snapshot_hash == snapshot.snapshot_hash


def test_scenario_binding_accepts_typed_hierarchical_certificate():
    certificate = hierarchical_snapshot().certificate()
    spec = ScenarioBindingSpec(
        ("Bear", "Base", "Bull"),
        ("scenario_probability",),
        "scenario_probability",
        "margin_compression|12m",
    )
    result = bind_scenarios(
        compiled_probabilities(),
        spec,
        calibration_certificate=certificate,
        require_calibration_certificate=True,
    )
    assert result.passed
    assert result.scenario_set.numeric_weighting_allowed
    assert result.scenario_set.calibration_snapshot_hash == certificate.snapshot_hash
    assert result.scenario_set.calibration_dataset_hash == certificate.dataset_hash
