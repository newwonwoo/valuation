from pathlib import Path

from valuation_engine.control_plane import StageStatus
from valuation_engine.doctrine_runtime import build_doctrine_coverage
from valuation_engine.orchestrator import StageTrace
from valuation_engine.unit_contracts import load_unit_contract_registry


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = load_unit_contract_registry(ROOT / "config" / "unit_contract_registry.yaml")


def test_pre_audit_and_freeze_coverage_are_distinct_and_complete():
    traces = (
        StageTrace("SCENARIO_BUILD", StageStatus.PASS, "compiled and bound", False),
        StageTrace("DETERMINISTIC_VALUATION", StageStatus.PASS, "valued", False),
    )
    pre = build_doctrine_coverage(
        REGISTRY,
        relevant_stages=("SCENARIO_BUILD", "DETERMINISTIC_VALUATION"),
        stage_traces=traces,
        required_stages=("SCENARIO_BUILD", "DETERMINISTIC_VALUATION"),
    )
    pre_ids = set(pre.expected_unit_ids)
    assert {"DOCTRINE_CONSTITUTION", "VALUATION_CONTROL_PLANE"}.issubset(pre_ids)
    assert {"ASSUMPTION_COMPILER", "SCENARIO_ENGINE", "DETERMINISTIC_VALUATION", "SOTP_AGGREGATOR"}.issubset(pre_ids)
    assert "AUDIT_GATE" not in pre_ids
    assert "INTRINSIC_FREEZE" not in pre_ids

    final = build_doctrine_coverage(
        REGISTRY,
        relevant_stages=(
            "SCENARIO_BUILD",
            "DETERMINISTIC_VALUATION",
            "AUDIT_GATE",
            "INTRINSIC_VALUE_FREEZE",
        ),
        stage_traces=traces
        + (StageTrace("AUDIT_GATE", StageStatus.PASS, "impact then audit", False),),
        required_stages=(
            "SCENARIO_BUILD",
            "DETERMINISTIC_VALUATION",
            "AUDIT_GATE",
            "INTRINSIC_VALUE_FREEZE",
        ),
        prospective_pass_stages=("INTRINSIC_VALUE_FREEZE",),
    )
    final_ids = set(final.expected_unit_ids)
    assert {"DECISION_IMPACT", "AUDIT_GATE", "INTRINSIC_FREEZE"}.issubset(final_ids)
    assert all(not item.unresolved_blocker for item in final.entries)


def test_missing_required_stage_trace_is_visible_and_blocking():
    coverage = build_doctrine_coverage(
        REGISTRY,
        relevant_stages=("DETERMINISTIC_VALUATION",),
        stage_traces=(),
        required_stages=("DETERMINISTIC_VALUATION",),
    )
    by_id = {item.module_id: item for item in coverage.entries}
    assert by_id["DETERMINISTIC_VALUATION"].status is StageStatus.NOT_IMPLEMENTED
    assert by_id["DETERMINISTIC_VALUATION"].unresolved_blocker
    assert by_id["SOTP_AGGREGATOR"].unresolved_blocker
