from pathlib import Path

import pytest

from valuation_engine.control_plane import DoctrineCoverageEntry, ExecutionMode, StageStatus
from valuation_engine.orchestrator import (
    StageExecutionResult,
    load_stage_sequence,
    run_controlled_workflow,
)


def test_canonical_stage_registry_loads_unique_freeze_boundary():
    root = Path(__file__).resolve().parents[1]
    sequence = load_stage_sequence(root / "config" / "control_plane_stage_registry.yaml")
    assert len(sequence) == len(set(sequence))
    assert sequence.index("AUDIT_GATE") < sequence.index("INTRINSIC_VALUE_FREEZE")
    assert sequence.index("INTRINSIC_VALUE_FREEZE") < sequence.index("MARKET_PRICE_LOAD")


def test_legacy_regression_cannot_enter_new_orchestrator():
    with pytest.raises(ValueError):
        run_controlled_workflow(
            run_id="R1",
            execution_mode=ExecutionMode.LEGACY_REGRESSION,
            stage_sequence=("COMPANY_RESOLUTION",),
            adapters={},
            required_stages=("COMPANY_RESOLUTION",),
        )


def test_missing_required_adapter_fails_closed_and_is_visible():
    result = run_controlled_workflow(
        run_id="R2",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("COMPANY_RESOLUTION", "PRIMARY_EVIDENCE_COLLECTION"),
        adapters={
            "COMPANY_RESOLUTION": lambda _: StageExecutionResult(
                StageStatus.PASS,
                "company resolved",
                {"company": "Example"},
            )
        },
        required_stages=("COMPANY_RESOLUTION", "PRIMARY_EVIDENCE_COLLECTION"),
    )

    assert result.blocked_reasons
    assert result.stage_traces[-1].status is StageStatus.NOT_IMPLEMENTED
    assert result.stage_traces[-1].blocking


def test_market_stage_never_runs_without_freeze_token():
    calls: list[str] = []
    sequence = (
        "COMPANY_RESOLUTION",
        "AUDIT_GATE",
        "INTRINSIC_VALUE_FREEZE",
        "MARKET_PRICE_LOAD",
    )
    adapters = {
        "COMPANY_RESOLUTION": lambda _: StageExecutionResult(
            StageStatus.PASS,
            "resolved",
            {
                "doctrine_coverage": (
                    DoctrineCoverageEntry("M1", StageStatus.PASS, "done"),
                ),
                "expected_module_ids": ("M1",),
            },
        ),
        "AUDIT_GATE": lambda _: StageExecutionResult(
            StageStatus.PASS,
            "audit passed but valuation hashes absent",
            {"audit_passed": True, "audit_hash": "audit"},
        ),
        "MARKET_PRICE_LOAD": lambda _: calls.append("market") or StageExecutionResult(
            StageStatus.PASS,
            "market loaded",
            {"current_market_price": 100.0},
        ),
    }

    result = run_controlled_workflow(
        run_id="R3",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=sequence,
        adapters=adapters,
        required_stages=sequence,
    )

    assert calls == []
    assert result.freeze_token is None
    assert result.stage_traces[-1].stage == "INTRINSIC_VALUE_FREEZE"
    assert result.stage_traces[-1].status is StageStatus.BLOCKED


def test_successful_shadow_run_issues_token_before_market_access():
    calls: list[str] = []
    sequence = (
        "COMPANY_RESOLUTION",
        "DETERMINISTIC_VALUATION",
        "AUDIT_GATE",
        "INTRINSIC_VALUE_FREEZE",
        "MARKET_PRICE_LOAD",
        "FINAL_REPORT",
    )

    def company(_):
        return StageExecutionResult(
            StageStatus.PASS,
            "resolved",
            {
                "company": "Example",
                "doctrine_coverage": (
                    DoctrineCoverageEntry("M1", StageStatus.PASS, "module complete"),
                ),
                "expected_module_ids": ("M1",),
                "industry_snapshot_hash": "industry",
                "source_snapshot_hash": "source",
            },
        )

    def valuation(_):
        return StageExecutionResult(
            StageStatus.PASS,
            "valuation calculated",
            {"assumption_set_hash": "assumptions", "valuation_hash": "value"},
        )

    def audit(_):
        return StageExecutionResult(
            StageStatus.PASS,
            "audit passed",
            {"audit_passed": True, "audit_hash": "audit"},
        )

    def market(context):
        assert context.freeze_token is not None
        calls.append("market")
        return StageExecutionResult(
            StageStatus.PASS,
            "market loaded post-freeze",
            {"current_market_price": 100.0},
        )

    adapters = {
        "COMPANY_RESOLUTION": company,
        "DETERMINISTIC_VALUATION": valuation,
        "AUDIT_GATE": audit,
        "MARKET_PRICE_LOAD": market,
        "FINAL_REPORT": lambda _: StageExecutionResult(StageStatus.PASS, "reported"),
    }
    result = run_controlled_workflow(
        run_id="R4",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=sequence,
        adapters=adapters,
        required_stages=sequence,
    )

    assert result.blocked_reasons == ()
    assert result.freeze_token is not None
    assert calls == ["market"]
    assert [trace.stage for trace in result.stage_traces] == list(sequence)


def test_stage_context_is_append_only_to_prevent_silent_rewrites():
    sequence = ("A", "B")
    result_adapter = {
        "A": lambda _: StageExecutionResult(StageStatus.PASS, "a", {"x": 1}),
        "B": lambda _: StageExecutionResult(StageStatus.PASS, "b", {"x": 2}),
    }
    with pytest.raises(ValueError):
        run_controlled_workflow(
            run_id="R5",
            execution_mode=ExecutionMode.PRIMARY_SHADOW,
            stage_sequence=sequence,
            adapters=result_adapter,
            required_stages=sequence,
        )
