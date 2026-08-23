import pytest

from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.module_plan import ModuleRequirementPlan, SegmentModuleRequirementPlan
from valuation_engine.orchestrator import run_controlled_workflow
from valuation_engine.records import AffectedVariable, EvidenceRecord, EvidenceSourceLayer
from valuation_engine.scanner_runtime import (
    ScannerFinding,
    ScannerFindingStatus,
    ScannerHandlerSpec,
    rocket_insight_scan_adapter,
    run_scanner_loadout,
)


def evidence(evidence_id: str, metric: str) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        target="T",
        metric=metric,
        value=1,
        unit="dimensionless",
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-06-30",
        observed_date="2026-07-01",
        source_name="filing",
        source_ref=f"source#{evidence_id}",
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def plan(*scanners: str) -> ModuleRequirementPlan:
    segment = SegmentModuleRequirementPlan(
        segment_id="core",
        sector_adapter="power.transformer_switchgear",
        archetypes=("contracted_backlog",),
        required_evidence=("backlog",),
        required_kpis=("book_to_bill",),
        mandatory_scanners=scanners,
        kill_conditions=("backlog conversion fails",),
        normalization_rules=("orders_vs_backlog",),
        beta_peer_features=("backlog_duration",),
        per_peer_features=("visibility",),
        scenario_variables=("backlog_conversion",),
        funding_scans=(),
        terminal_policies=("normalize_backlog",),
        double_count_traps=("backlog_plus_same_revenue",),
        forbidden_methods=("raw_backlog_multiple",),
        allowed_valuation_methods=("normalized_dcf",),
    )
    result = ModuleRequirementPlan(
        segments=(segment,),
        common_core_modules=("evidence_gate",),
        required_evidence=("backlog",),
        required_kpis=("book_to_bill",),
        mandatory_scanners=scanners,
        kill_conditions=("backlog conversion fails",),
        scenario_variables=("backlog_conversion",),
        double_count_traps=("backlog_plus_same_revenue",),
        forbidden_methods=("raw_backlog_multiple",),
    )
    result.validate()
    return result


def finding_handler(scanner_id: str, *, status=ScannerFindingStatus.PASS):
    def run(request, ledger):
        assert request.scanner_id == scanner_id
        ledger.get("E1")
        return ScannerFinding(
            scanner_id=scanner_id,
            status=status,
            summary=f"{scanner_id} checked",
            supporting_evidence_ids=("E1",),
            affected_variables=(AffectedVariable.QUANTITY,),
            economic_path_ids=("demand_visibility",),
        )

    return run


def missing_handler(scanner_id: str):
    def run(request, ledger):
        return ScannerFinding(
            scanner_id=scanner_id,
            status=ScannerFindingStatus.MISSING_EVIDENCE,
            summary="need cancellation terms",
            missing_evidence_metrics=("cancellation_terms",),
        )

    return run


def test_all_mandatory_scanners_execute_with_traceable_findings():
    ledger = EvidenceLedger((evidence("E1", "backlog"),))
    result = run_scanner_loadout(
        target_id="T",
        run_id="R1",
        plan=plan("BACKLOG_QUALITY", "LEAD_TIME_SLOT"),
        ledger=ledger,
        handler_specs=(
            ScannerHandlerSpec("BACKLOG_QUALITY", finding_handler("BACKLOG_QUALITY")),
            ScannerHandlerSpec("LEAD_TIME_SLOT", finding_handler("LEAD_TIME_SLOT")),
        ),
    )
    assert result.complete
    assert {item.scanner_id for item in result.findings} == {
        "BACKLOG_QUALITY",
        "LEAD_TIME_SLOT",
    }
    assert result.snapshot_hash


def test_snapshot_hash_is_independent_of_handler_registration_order():
    ledger = EvidenceLedger((evidence("E1", "backlog"),))
    specs = (
        ScannerHandlerSpec("BACKLOG_QUALITY", finding_handler("BACKLOG_QUALITY")),
        ScannerHandlerSpec("LEAD_TIME_SLOT", finding_handler("LEAD_TIME_SLOT")),
    )
    one = run_scanner_loadout(
        target_id="T",
        run_id="R1",
        plan=plan("BACKLOG_QUALITY", "LEAD_TIME_SLOT"),
        ledger=ledger,
        handler_specs=specs,
    )
    two = run_scanner_loadout(
        target_id="T",
        run_id="R1",
        plan=plan("BACKLOG_QUALITY", "LEAD_TIME_SLOT"),
        ledger=ledger,
        handler_specs=tuple(reversed(specs)),
    )
    assert one.snapshot_hash == two.snapshot_hash


def test_missing_mandatory_handler_is_explicit_and_optional_cannot_replace_it():
    ledger = EvidenceLedger((evidence("E1", "backlog"),))
    result = run_scanner_loadout(
        target_id="T",
        run_id="R1",
        plan=plan("BACKLOG_QUALITY"),
        ledger=ledger,
        handler_specs=(
            ScannerHandlerSpec("OPTIONAL_REINFORCEMENT", finding_handler("OPTIONAL_REINFORCEMENT")),
        ),
        reinforcement_scanner_ids=("OPTIONAL_REINFORCEMENT",),
    )
    assert result.missing_mandatory_handlers == ("BACKLOG_QUALITY",)
    assert {item.scanner_id for item in result.findings} == {"OPTIONAL_REINFORCEMENT"}


def test_invented_or_inactive_evidence_reference_fails_closed():
    ledger = EvidenceLedger((evidence("E1", "backlog"),))

    def bad(request, ledger):
        return ScannerFinding(
            scanner_id=request.scanner_id,
            status=ScannerFindingStatus.PASS,
            summary="bad",
            supporting_evidence_ids=("INVENTED",),
            affected_variables=(AffectedVariable.QUANTITY,),
        )

    result = run_scanner_loadout(
        target_id="T",
        run_id="R1",
        plan=plan("BACKLOG_QUALITY"),
        ledger=ledger,
        handler_specs=(ScannerHandlerSpec("BACKLOG_QUALITY", bad),),
    )
    assert result.failed_scanner_ids == ("BACKLOG_QUALITY",)
    assert not result.complete


def test_missing_evidence_finding_enters_recovery():
    ledger = EvidenceLedger((evidence("E1", "backlog"),))
    result = run_scanner_loadout(
        target_id="T",
        run_id="R1",
        plan=plan("CANCELLATION_TERMS"),
        ledger=ledger,
        handler_specs=(
            ScannerHandlerSpec("CANCELLATION_TERMS", missing_handler("CANCELLATION_TERMS")),
        ),
    )
    assert result.missing_evidence_metrics == ("cancellation_terms",)
    assert not result.complete


def test_mandatory_scanner_cannot_hide_as_not_applicable():
    ledger = EvidenceLedger((evidence("E1", "backlog"),))

    def not_applicable(request, ledger):
        return ScannerFinding(
            scanner_id=request.scanner_id,
            status=ScannerFindingStatus.NOT_APPLICABLE,
            summary="skip",
        )

    result = run_scanner_loadout(
        target_id="T",
        run_id="R1",
        plan=plan("BACKLOG_QUALITY"),
        ledger=ledger,
        handler_specs=(ScannerHandlerSpec("BACKLOG_QUALITY", not_applicable),),
    )
    assert result.failed_scanner_ids == ("BACKLOG_QUALITY",)


def test_control_plane_stage_executes_typed_scanner_runtime():
    ledger = EvidenceLedger((evidence("E1", "backlog"),))
    result = run_controlled_workflow(
        run_id="SCAN",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_sequence=("ROCKET_INSIGHT_SCAN",),
        adapters={
            "ROCKET_INSIGHT_SCAN": rocket_insight_scan_adapter(
                handler_specs=(
                    ScannerHandlerSpec(
                        "BACKLOG_QUALITY",
                        finding_handler("BACKLOG_QUALITY"),
                    ),
                )
            )
        },
        required_stages=("ROCKET_INSIGHT_SCAN",),
        initial_data={
            "target_id": "T",
            "module_requirement_plan": plan("BACKLOG_QUALITY"),
            "evidence_ledger": ledger,
        },
    )
    assert result.blocked_reasons == ()
    assert result.stage_traces[0].status is StageStatus.PASS
    assert result.data["scanner_snapshot_hash"]


def test_control_plane_missing_handler_is_not_implemented_not_silent_pass():
    ledger = EvidenceLedger((evidence("E1", "backlog"),))
    result = run_controlled_workflow(
        run_id="SCAN-MISSING",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_sequence=("ROCKET_INSIGHT_SCAN",),
        adapters={"ROCKET_INSIGHT_SCAN": rocket_insight_scan_adapter(handler_specs=())},
        required_stages=("ROCKET_INSIGHT_SCAN",),
        initial_data={
            "target_id": "T",
            "module_requirement_plan": plan("BACKLOG_QUALITY"),
            "evidence_ledger": ledger,
        },
    )
    assert result.blocked_reasons
    assert result.stage_traces[0].status is StageStatus.NOT_IMPLEMENTED
