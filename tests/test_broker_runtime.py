from __future__ import annotations

from types import SimpleNamespace

import pytest

from valuation_engine.broker_research import (
    BrokerClaim,
    BrokerFieldClass,
    BrokerReportType,
)
from valuation_engine.broker_runtime import (
    BrokerPreFreezeUse,
    BrokerResearchBatch,
    BrokerResearchObservation,
    broker_aware_module_requirement_plan_adapter,
    broker_research_audit_adapter,
    build_broker_prefreeze_result,
    pre_freeze_use,
)
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.records import (
    AffectedVariable,
    BridgeRecord,
    Direction,
    EvidenceRecord,
    EvidenceSourceLayer,
    HypothesisRecord,
)


def claim(
    claim_id: str,
    field: BrokerFieldClass,
    *,
    target_specific: bool,
    report_date: str = "2026-08-07",
) -> BrokerClaim:
    return BrokerClaim(
        claim_id=claim_id,
        source_id="KR_MIRAE_RESEARCH",
        broker_family="MiraeAssetSecurities",
        report_type=BrokerReportType.COMPANY_UPDATE,
        field_class=field,
        industry_node="power_transformers",
        statement=f"derived broker claim {claim_id}",
        target_company_specific=target_specific,
        underlying_data_families=("company_disclosure",),
        report_date=report_date,
    )


def observation(
    claim_id: str,
    field: BrokerFieldClass,
    *,
    target_specific: bool,
    metrics=(),
    report_date: str = "2026-08-07",
) -> BrokerResearchObservation:
    return BrokerResearchObservation(
        claim(
            claim_id,
            field,
            target_specific=target_specific,
            report_date=report_date,
        ),
        "core",
        "https://example.com/broker/report",
        tuple(metrics),
        ("verify in company filing",) if metrics else (),
        ("company filing",) if metrics else (),
    )


def batch(*, checked_at: str = "2026-08-26") -> BrokerResearchBatch:
    return BrokerResearchBatch(
        checked_at=checked_at,
        observations=(
            observation(
                "B:INDUSTRY",
                BrokerFieldClass.MECHANISM_CANDIDATE,
                target_specific=False,
                metrics=("utilization",),
            ),
            observation(
                "B:FACT_LEAD",
                BrokerFieldClass.UNDERLYING_DATA_REFERENCE,
                target_specific=True,
                metrics=("expansion_land_control",),
            ),
        ),
        source_refs=("https://example.com/broker/report",),
    )


def profile() -> IndustryDNAProfile:
    return IndustryDNAProfile(
        segment_id="core",
        sector_adapter="power.transformer_switchgear",
        archetypes=(EconomicArchetype.CAPACITY_MANUFACTURING,),
        revenue_recognition="delivery",
        price_formation="contract",
        asset_ownership="owned",
        capital_intensity="high",
        regulation_intensity="medium",
        customer_structure="utilities",
        reinvestment_model="capacity",
        cashflow_duration="backlog",
        evidence_keys=("E:SEGMENT",),
    )


def evidence(
    evidence_id: str,
    metric: str,
    *,
    layer: EvidenceSourceLayer = EvidenceSourceLayer.REALIZED_OR_FILING,
) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        target="T",
        metric=metric,
        value=1,
        unit="count" if metric != "utilization" else "ratio",
        source_layer=layer,
        effective_date="2026-06-30",
        observed_date="2026-08-26",
        source_name="company filing",
        source_ref="https://example.com/filing",
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def runtime_context(plan_stage, ledger, *, hypotheses=(), bridges=()):
    return {
        **plan_stage.outputs,
        "data_cutoff": "2026-08-26",
        "evidence_ledger": ledger,
        "hypotheses": hypotheses,
        "bridges": bridges,
        "broker_research_rocket_connected": True,
        "scanner_findings": (SimpleNamespace(scanner_id="BROKER_RESEARCH"),),
    }


def make_plan(loader=lambda _: batch()):
    return broker_aware_module_requirement_plan_adapter(
        registry_path="config/archetype_module_registry.yaml",
        control_requirements_path="config/archetype_control_requirements.yaml",
        loader=loader,
        require_broker_research=True,
    )(
        OrchestratorContext(
            "R",
            ExecutionMode.LIVE_PRIMARY,
            {
                "industry_dna_profiles": (profile(),),
                "data_cutoff": "2026-08-26",
            },
        )
    )


def test_prefreeze_use_separates_context_verification_and_locked_fields():
    assert pre_freeze_use(
        claim("C", BrokerFieldClass.MECHANISM_CANDIDATE, target_specific=False)
    ) is BrokerPreFreezeUse.CONTEXT
    assert pre_freeze_use(
        claim("V", BrokerFieldClass.UNDERLYING_DATA_REFERENCE, target_specific=True)
    ) is BrokerPreFreezeUse.PRIMARY_VERIFICATION_ONLY
    assert pre_freeze_use(
        claim("Q", BrokerFieldClass.TARGET_PRICE, target_specific=True)
    ) is BrokerPreFreezeUse.QUARANTINED


def test_broker_result_contains_only_safe_prefreeze_claims():
    result = build_broker_prefreeze_result(
        batch(),
        known_segments=("core",),
        data_cutoff="2026-08-26",
    )

    assert tuple(item.claim_id for item in result.context_claims) == ("B:INDUSTRY",)
    assert tuple(item.claim_id for item in result.primary_verification_claims) == (
        "B:FACT_LEAD",
    )
    assert result.quarantined_claims == ()
    assert result.verification_rows == (
        ("core", "utilization"),
        ("core", "expansion_land_control"),
    )
    assert result.snapshot_hash


def test_locked_target_price_is_rejected_before_it_enters_runtime_state():
    locked = BrokerResearchBatch(
        checked_at="2026-08-26",
        observations=(
            observation(
                "B:TARGET",
                BrokerFieldClass.TARGET_PRICE,
                target_specific=True,
            ),
        ),
        source_refs=("https://example.com/broker/report",),
    )
    with pytest.raises(ValueError, match="must not be loaded before Intrinsic Freeze"):
        build_broker_prefreeze_result(
            locked,
            known_segments=("core",),
            data_cutoff="2026-08-26",
        )


def test_target_company_lead_requires_metric_backed_verification():
    lead = observation(
        "B:LEAD",
        BrokerFieldClass.UNDERLYING_DATA_REFERENCE,
        target_specific=True,
        metrics=(),
    )
    with pytest.raises(ValueError, match="metric-backed"):
        lead.validate()


def test_broker_cutoff_rejects_future_checked_at_and_report_date():
    with pytest.raises(ValueError, match="checked_at"):
        build_broker_prefreeze_result(
            batch(checked_at="2026-08-27"),
            known_segments=("core",),
            data_cutoff="2026-08-26",
        )
    future_claim = BrokerResearchBatch(
        checked_at="2026-08-26",
        observations=(
            observation(
                "B:FUTURE",
                BrokerFieldClass.MECHANISM_CANDIDATE,
                target_specific=False,
                report_date="2026-08-27",
            ),
        ),
        source_refs=("https://example.com/broker/report",),
    )
    with pytest.raises(ValueError, match="look-ahead"):
        build_broker_prefreeze_result(
            future_claim,
            known_segments=("core",),
            data_cutoff="2026-08-26",
        )


def test_broker_aware_plan_blocks_when_required_loader_is_missing():
    stage = broker_aware_module_requirement_plan_adapter(
        registry_path="config/archetype_module_registry.yaml",
        control_requirements_path="config/archetype_control_requirements.yaml",
        loader=None,
        require_broker_research=True,
    )(
        OrchestratorContext(
            "R",
            ExecutionMode.LIVE_PRIMARY,
            {"industry_dna_profiles": (profile(),), "data_cutoff": "2026-08-26"},
        )
    )

    assert stage.status is StageStatus.NOT_IMPLEMENTED
    assert stage.blocking


def test_broker_aware_plan_extends_required_primary_evidence():
    stage = make_plan()

    assert stage.status is StageStatus.PASS
    plan = stage.outputs["module_requirement_plan"]
    assert "expansion_land_control" in plan.plan_for_segment("core").required_evidence
    assert stage.outputs["broker_research_snapshot_hash"]
    assert stage.outputs["broker_quarantined_claims"] == ()


def test_broker_audit_rejects_non_primary_layer_as_verification():
    plan_stage = make_plan()
    ledger = EvidenceLedger()
    ledger.append(evidence("E:U", "utilization"))
    ledger.append(
        evidence(
            "E:L",
            "expansion_land_control",
            layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
        )
    )

    audit = broker_research_audit_adapter(required=True)(
        OrchestratorContext(
            "R",
            ExecutionMode.LIVE_PRIMARY,
            runtime_context(plan_stage, ledger),
        )
    )

    assert audit.status is StageStatus.BLOCKED
    assert "broker_verification_company_primary" in audit.rationale


def test_broker_audit_passes_after_company_primary_verification_and_binds_ids():
    plan_stage = make_plan()
    ledger = EvidenceLedger()
    ledger.append(evidence("E:U", "utilization"))
    ledger.append(evidence("E:L", "expansion_land_control"))
    hypothesis = HypothesisRecord(
        id="H",
        statement="primary evidence supports capacity",
        causal_chain=("filing", "capacity", "value"),
        supporting_evidence_ids=("E:L",),
        kill_conditions=("filing reverses",),
    )
    bridge = BridgeRecord(
        id="BR",
        evidence_ids=("E:L",),
        hypothesis_id="H",
        affected_variable=AffectedVariable.QUANTITY,
        direction=Direction.UP,
        old_value=0,
        new_value=1,
        unit="count",
        rationale="primary evidence only",
        confidence=0.8,
        kill_condition="filing reverses",
        verification_event="next filing",
        economic_path_id="P",
    )

    audit = broker_research_audit_adapter(required=True)(
        OrchestratorContext(
            "R",
            ExecutionMode.LIVE_PRIMARY,
            runtime_context(
                plan_stage,
                ledger,
                hypotheses=(hypothesis,),
                bridges=(bridge,),
            ),
        )
    )

    assert audit.status is StageStatus.PASS
    assert audit.outputs["broker_research_audit_passed"]
    bindings = audit.outputs["broker_primary_verification_bindings"]
    assert ("core", "utilization", ("E:U",)) in bindings
    assert ("core", "expansion_land_control", ("E:L",)) in bindings


def test_broker_audit_rejects_snapshot_hash_drift():
    plan_stage = make_plan()
    ledger = EvidenceLedger()
    ledger.append(evidence("E:U", "utilization"))
    ledger.append(evidence("E:L", "expansion_land_control"))
    data = runtime_context(plan_stage, ledger)
    data["broker_research_snapshot_hash"] = "stale"

    audit = broker_research_audit_adapter(required=True)(
        OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, data)
    )

    assert audit.status is StageStatus.BLOCKED
    assert "broker_snapshot_exact_hash" in audit.rationale
