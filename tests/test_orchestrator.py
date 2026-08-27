from pathlib import Path

import pytest

from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.orchestrator import (
    MajorGateDefinition,
    ReportingContract,
    StageExecutionResult,
    load_reporting_contract,
    load_stage_sequence,
    run_controlled_workflow,
)


def test_canonical_stage_registry_loads_unique_freeze_boundary():
    root = Path(__file__).resolve().parents[1]
    sequence = load_stage_sequence(root / "config" / "control_plane_stage_registry.yaml")
    assert len(sequence) == len(set(sequence))
    assert sequence.index("AUDIT_GATE") < sequence.index("INTRINSIC_VALUE_FREEZE")
    assert sequence.index("INTRINSIC_VALUE_FREEZE") < sequence.index("MARKET_PRICE_LOAD")


def reporting_contract(*gates):
    return ReportingContract(
        contract_id="test-major-gates/v1",
        major_gates=tuple(gates),
        main_body_target_pages=(6, 8),
        audit_appendix_target_pages=(3, 4),
        total_page_cap=12,
        visual_pages_included_in_main_body=2,
        body_min_pt=13,
        primary_heading_min_pt=22,
        section_heading_min_pt=18,
        dense_wide_tables_forbidden=True,
        direct_http_links_required=True,
        claim_source_mapping_required=True,
        non_http_source_refs_forbidden_in_live_reports=True,
        llm_insight_separate_section_required=True,
        llm_insight_max_chars=1000,
        deterministic_outputs_separated_from_llm=True,
    )


def test_canonical_reporting_contract_partitions_all_33_stages_once():
    root = Path(__file__).resolve().parents[1]
    contract = load_reporting_contract(
        root / "config" / "control_plane_stage_registry.yaml"
    )

    assert [len(item.stages) for item in contract.major_gates] == [9, 5, 5, 7, 7]
    assert tuple(stage for gate in contract.major_gates for stage in gate.stages) == (
        load_stage_sequence(root / "config" / "control_plane_stage_registry.yaml")
    )
    assert contract.main_body_target_pages == (3, 4)
    assert contract.audit_appendix_target_pages == (1, 2)
    assert contract.total_page_cap == 6
    assert contract.visual_pages_included_in_main_body == 2
    assert contract.body_min_pt == 13
    assert contract.llm_insight_max_chars == 1000
    assert contract.visible_language == "ko"
    assert contract.primary_section_order[:2] == ("투자 요약", "가치평가")
    assert contract.decision_report_precedes_audit_appendix
    assert contract.technical_identifiers_collapsed


def test_orchestrator_emits_one_summary_only_when_each_major_gate_completes():
    seen = []
    contract = reporting_contract(
        MajorGateDefinition("G1", "First", ("A", "B")),
        MajorGateDefinition("G2", "Second", ("C",)),
    )
    result = run_controlled_workflow(
        run_id="REPORTING-1",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("A", "B", "C"),
        adapters={
            stage: (lambda _, stage=stage: StageExecutionResult(StageStatus.PASS, stage))
            for stage in ("A", "B", "C")
        },
        required_stages=("A", "B", "C"),
        reporting_contract=contract,
        major_gate_reporter=seen.append,
    )

    assert tuple(item.gate_id for item in seen) == ("G1", "G2")
    assert result.major_gate_summaries == tuple(seen)
    assert result.major_gate_summaries[0].completed_stage_count == 2
    assert result.major_gate_summaries[-1].next_action == "FINAL_RESULT_REPORT"


def test_blocked_gate_emits_partial_summary_and_never_fabricates_later_gate():
    seen = []
    contract = reporting_contract(
        MajorGateDefinition("G1", "First", ("A", "B", "C")),
        MajorGateDefinition("G2", "Second", ("D",)),
    )
    result = run_controlled_workflow(
        run_id="REPORTING-BLOCKED",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("A", "B", "C", "D"),
        adapters={
            "A": lambda _: StageExecutionResult(StageStatus.PASS, "a"),
            "B": lambda _: StageExecutionResult(
                StageStatus.BLOCKED,
                "material evidence gap",
                blocking=True,
            ),
        },
        required_stages=("A", "B", "C", "D"),
        reporting_contract=contract,
        major_gate_reporter=seen.append,
    )

    assert len(seen) == 1
    assert seen[0].gate_id == "G1"
    assert seen[0].status is StageStatus.BLOCKED
    assert seen[0].completed_stage_count == 2
    assert seen[0].expected_stage_count == 3
    assert seen[0].next_action == "RESOLVE_G1"
    assert seen[0].decisive_result == "B 단계가 blocked 상태로 종료되었습니다"
    assert seen[0].residual_risk == "B:BLOCKED"
    assert "material evidence gap" not in seen[0].decisive_result
    assert tuple(item.gate_id for item in result.major_gate_summaries) == ("G1",)


def test_reporting_contract_must_match_executed_stage_sequence_exactly():
    contract = reporting_contract(
        MajorGateDefinition("G1", "First", ("A", "B")),
    )
    with pytest.raises(ValueError, match="partition"):
        run_controlled_workflow(
            run_id="REPORTING-MISMATCH",
            execution_mode=ExecutionMode.PRIMARY_SHADOW,
            stage_sequence=("A",),
            adapters={},
            required_stages=("A",),
            reporting_contract=contract,
        )


def test_reporter_failure_is_visible_but_cannot_block_valuation_state():
    contract = reporting_contract(
        MajorGateDefinition("G1", "First", ("A",)),
    )

    def fail(_):
        raise RuntimeError("delivery failed")

    result = run_controlled_workflow(
        run_id="REPORTING-DELIVERY",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("A",),
        adapters={
            "A": lambda _: StageExecutionResult(
                StageStatus.PASS,
                "authoritative computation completed",
                {"authoritative_value": 1},
            )
        },
        required_stages=("A",),
        reporting_contract=contract,
        major_gate_reporter=fail,
    )

    assert result.blocked_reasons == ()
    assert result.data["authoritative_value"] == 1
    assert result.reporting_warnings == (
        "G1: major-gate reporter failed (RuntimeError)",
    )


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
        ),
        "AUDIT_GATE": lambda _: StageExecutionResult(
            StageStatus.PASS,
            "audit passed but valuation hashes absent",
            {
                "audit_passed": True,
                "audit_hash": "audit",
                "decision_impact_completed": True,
            },
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


def test_successful_shadow_run_generates_coverage_and_issues_token_before_market_access():
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
                "industry_snapshot_hash": "industry",
                "source_snapshot_hash": "source",
                "ledger_snapshot_hash": "ledger",
            },
        )

    def valuation(_):
        return StageExecutionResult(
            StageStatus.PASS,
            "valuation calculated",
            {"assumption_set_hash": "assumptions", "valuation_hash": "value"},
        )

    def audit(context):
        assert context.data["pre_audit_doctrine_coverage"]
        assert context.data["pre_audit_expected_unit_ids"]
        return StageExecutionResult(
            StageStatus.PASS,
            "decision impact recorded and audit passed",
            {
                "audit_passed": True,
                "audit_hash": "audit",
                "decision_impact_completed": True,
            },
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
    assert result.freeze_token.ledger_snapshot_hash == "ledger"
    assert calls == ["market"]
    assert [trace.stage for trace in result.stage_traces] == list(sequence)
    covered = {item.module_id for item in result.data["runtime_doctrine_coverage"]}
    assert {"DOCTRINE_CONSTITUTION", "VALUATION_CONTROL_PLANE"}.issubset(covered)
    assert {"DETERMINISTIC_VALUATION", "SOTP_AGGREGATOR", "AUDIT_GATE", "DECISION_IMPACT", "INTRINSIC_FREEZE"}.issubset(covered)
    assert tuple(item.module_id for item in result.data["runtime_doctrine_coverage"]) == result.data["runtime_expected_unit_ids"]


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
