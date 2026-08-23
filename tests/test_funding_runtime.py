from decimal import Decimal

from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.funding_adapter import FundingRuntimeConfig, upstream_funding_runtime_adapter
from valuation_engine.funding_runtime import FundingSourceUseBinding, assess_funding_sources_and_uses
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.orchestrator import run_controlled_workflow
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer
from valuation_engine.wacc import CustomerAdvanceCreditEvidence


def evidence(eid: str, metric: str, value: int, *, date: str = "2026-06-30") -> EvidenceRecord:
    return EvidenceRecord(
        id=eid,
        target="T",
        metric=metric,
        value=value,
        unit="KRW",
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date=date,
        observed_date="2026-08-14",
        source_name="filing",
        source_ref=f"filing#{eid}",
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def binding() -> FundingSourceUseBinding:
    return FundingSourceUseBinding(
        need_metrics=("growth_capex", "incremental_nwc_need"),
        source_metrics=("growth_related_customer_advances", "committed_financing"),
        reporting_unit="KRW",
        segment="core",
    )


def ledger(*, customer_advances=50, financing=20, capex=60, nwc=10, financing_date="2026-06-30"):
    return EvidenceLedger((
        evidence("E_CAPEX", "growth_capex", capex),
        evidence("E_NWC", "incremental_nwc_need", nwc),
        evidence("E_ADV", "growth_related_customer_advances", customer_advances),
        evidence("E_FIN", "committed_financing", financing, date=financing_date),
    ))


def test_fully_funded_sources_and_uses_without_automatic_wacc_credit():
    result = assess_funding_sources_and_uses(target_id="T", ledger=ledger(), binding=binding())
    assert result.passed
    assert result.assessment.fully_funded
    assert result.assessment.funding_need == Decimal("70")
    assert result.assessment.verified_funding_sources == Decimal("70")
    assert result.assessment.funding_gap == Decimal("0")
    assert result.assessment.funding_coverage_ratio == Decimal("1")
    assert not result.assessment.credit_improvement_candidate


def test_funding_gap_is_measured_not_hidden():
    result = assess_funding_sources_and_uses(
        target_id="T",
        ledger=ledger(customer_advances=20, financing=10),
        binding=binding(),
    )
    assert result.passed
    assert result.assessment.funding_gap == Decimal("40")
    assert result.assessment.funding_coverage_ratio == Decimal("30") / Decimal("70")


def test_missing_metric_requests_recovery_input():
    incomplete = EvidenceLedger((
        evidence("E_CAPEX", "growth_capex", 60),
        evidence("E_NWC", "incremental_nwc_need", 10),
        evidence("E_ADV", "growth_related_customer_advances", 50),
    ))
    result = assess_funding_sources_and_uses(target_id="T", ledger=incomplete, binding=binding())
    assert not result.passed
    assert result.missing_metrics == ("committed_financing",)


def test_misaligned_dates_fail_closed():
    result = assess_funding_sources_and_uses(
        target_id="T",
        ledger=ledger(financing_date="2026-03-31"),
        binding=binding(),
    )
    assert not result.passed
    assert any("effective dates do not align" in item for item in result.blocking_findings)


def test_six_part_credit_evidence_only_creates_candidate_not_wacc_value():
    passed_credit = CustomerAdvanceCreditEvidence(True, True, True, True, True, True)
    result = assess_funding_sources_and_uses(
        target_id="T", ledger=ledger(), binding=binding(), credit_evidence=passed_credit
    )
    assert result.passed
    assert result.assessment.credit_improvement_candidate
    assert not hasattr(result.assessment, "wacc")
    assert not hasattr(result.assessment, "wacc_reduction")


def test_partial_credit_evidence_never_creates_candidate():
    partial = CustomerAdvanceCreditEvidence(True, True, True, True, False, True)
    result = assess_funding_sources_and_uses(
        target_id="T", ledger=ledger(), binding=binding(), credit_evidence=partial
    )
    assert result.passed
    assert not result.assessment.credit_improvement_candidate


class SegmentPlan:
    funding_scans = ("mandatory",)


class ModulePlan:
    segments = (SegmentPlan(),)


def test_control_plane_funding_adapter_passes_and_emits_typed_outputs():
    result = run_controlled_workflow(
        run_id="FUNDING1",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("UPSTREAM_FUNDING_SCAN",),
        adapters={
            "UPSTREAM_FUNDING_SCAN": upstream_funding_runtime_adapter(
                config=FundingRuntimeConfig(binding=binding())
            )
        },
        required_stages=("UPSTREAM_FUNDING_SCAN",),
        initial_data={
            "target_id": "T",
            "evidence_ledger": ledger(),
            "module_requirement_plan": ModulePlan(),
        },
    )
    assert not result.blocked_reasons
    assert result.stage_traces[0].status is StageStatus.PASS
    assert result.data["funded_demand_assessment"] == "FULLY_FUNDED"
    assert result.data["funding_gap"] == Decimal("0")
    assert not result.data["credit_improvement_candidate"]


def test_control_plane_funding_adapter_warns_on_gap_without_blocking():
    result = run_controlled_workflow(
        run_id="FUNDING2",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("UPSTREAM_FUNDING_SCAN",),
        adapters={
            "UPSTREAM_FUNDING_SCAN": upstream_funding_runtime_adapter(
                config=FundingRuntimeConfig(binding=binding())
            )
        },
        required_stages=("UPSTREAM_FUNDING_SCAN",),
        initial_data={
            "target_id": "T",
            "evidence_ledger": ledger(customer_advances=20, financing=10),
            "module_requirement_plan": ModulePlan(),
        },
    )
    assert not result.blocked_reasons
    assert result.stage_traces[0].status is StageStatus.WARNING
    assert result.data["funded_demand_assessment"] == "FUNDING_GAP"
    assert result.data["funding_gap"] == Decimal("40")


def test_control_plane_funding_adapter_requests_recovery_without_binding():
    result = run_controlled_workflow(
        run_id="FUNDING3",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("UPSTREAM_FUNDING_SCAN",),
        adapters={
            "UPSTREAM_FUNDING_SCAN": upstream_funding_runtime_adapter(
                config=FundingRuntimeConfig()
            )
        },
        required_stages=("UPSTREAM_FUNDING_SCAN",),
        initial_data={
            "target_id": "T",
            "evidence_ledger": ledger(),
            "module_requirement_plan": ModulePlan(),
        },
    )
    assert result.blocked_reasons
    assert result.stage_traces[0].status is StageStatus.RECOVERY_REQUIRED
