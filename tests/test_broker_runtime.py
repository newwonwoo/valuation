from __future__ import annotations

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
        report_date="2026-08-07",
    )


def observation(
    claim_id: str,
    field: BrokerFieldClass,
    *,
    target_specific: bool,
    metrics=(),
) -> BrokerResearchObservation:
    return BrokerResearchObservation(
        claim(claim_id, field, target_specific=target_specific),
        "core",
        "https://example.com/broker/report",
        tuple(metrics),
        ("verify in company filing",) if metrics else (),
        ("company filing",) if metrics else (),
    )


def batch() -> BrokerResearchBatch:
    return BrokerResearchBatch(
        checked_at="2026-08-26",
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
            observation(
                "B:TARGET",
                BrokerFieldClass.TARGET_PRICE,
                target_specific=True,
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


def evidence(evidence_id: str, metric: str) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        target="T",
        metric=metric,
        value=1,
        unit="count" if metric != "utilization" else "ratio",
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-06-30",
        observed_date="2026-08-26",
        source_name="company filing",
        source_ref="https://example.com/filing",
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def test_prefreeze_use_separates_context_verification_and_quarantine():
    assert pre_freeze_use(
        claim(
            "C",
            BrokerFieldClass.MECHANISM_CANDIDATE,
            target_specific=False,
        )
    ) is BrokerPreFreezeUse.CONTEXT
    assert pre_freeze_use(
        claim(
            "V",
            BrokerFieldClass.UNDERLYING_DATA_REFERENCE,
            target_specific=True,
        )
    ) is BrokerPreFreezeUse.PRIMARY_VERIFICATION_ONLY
    assert pre_freeze_use(
        claim(
            "Q",
            BrokerFieldClass.TARGET_PRICE,
            target_specific=True,
        )
    ) is BrokerPreFreezeUse.QUARANTINED


def test_broker_result_turns_only_allowed_leads_into_primary_verification():
    result = build_broker_prefreeze_result(batch(), known_segments=("core",))

    assert tuple(item.claim_id for item in result.context_claims) == ("B:INDUSTRY",)
    assert tuple(item.claim_id for item in result.primary_verification_claims) == (
        "B:FACT_LEAD",
    )
    assert tuple(item.claim_id for item in result.quarantined_claims) == ("B:TARGET",)
    assert result.verification_rows == (
        ("core", "utilization"),
        ("core", "expansion_land_control"),
    )
    assert result.snapshot_hash


def test_broker_aware_plan_blocks_when_required_loader_is_missing(tmp_path):
    stage = broker_aware_module_requirement_plan_adapter(
        registry_path="config/archetype_module_registry.yaml",
        control_requirements_path="config/archetype_control_requirements.yaml",
        loader=None,
        require_broker_research=True,
    )(
        OrchestratorContext(
            "R",
            ExecutionMode.LIVE_PRIMARY,
            {"industry_dna_profiles": (profile(),)},
        )
    )

    assert stage.status is StageStatus.NOT_IMPLEMENTED
    assert stage.blocking


def test_broker_aware_plan_extends_required_primary_evidence():
    stage = broker_aware_module_requirement_plan_adapter(
        registry_path="config/archetype_module_registry.yaml",
        control_requirements_path="config/archetype_control_requirements.yaml",
        loader=lambda _: batch(),
        require_broker_research=True,
    )(
        OrchestratorContext(
            "R",
            ExecutionMode.LIVE_PRIMARY,
            {"industry_dna_profiles": (profile(),)},
        )
    )

    assert stage.status is StageStatus.PASS
    plan = stage.outputs["module_requirement_plan"]
    assert "expansion_land_control" in plan.plan_for_segment("core").required_evidence
    assert stage.outputs["broker_research_snapshot_hash"]


def test_broker_audit_requires_primary_evidence_for_discovered_metrics():
    plan_stage = broker_aware_module_requirement_plan_adapter(
        registry_path="config/archetype_module_registry.yaml",
        control_requirements_path="config/archetype_control_requirements.yaml",
        loader=lambda _: batch(),
        require_broker_research=True,
    )(
        OrchestratorContext(
            "R",
            ExecutionMode.LIVE_PRIMARY,
            {"industry_dna_profiles": (profile(),)},
        )
    )
    ledger = EvidenceLedger()
    ledger.append(evidence("E:U", "utilization"))
    data = {
        **plan_stage.outputs,
        "evidence_ledger": ledger,
        "hypotheses": (),
        "bridges": (),
    }

    audit = broker_research_audit_adapter(required=True)(
        OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, data)
    )

    assert audit.status is StageStatus.BLOCKED
    assert "broker_verification_primary_evidence" in audit.rationale


def test_broker_audit_passes_after_primary_verification_and_no_direct_claim_use():
    plan_stage = broker_aware_module_requirement_plan_adapter(
        registry_path="config/archetype_module_registry.yaml",
        control_requirements_path="config/archetype_control_requirements.yaml",
        loader=lambda _: batch(),
        require_broker_research=True,
    )(
        OrchestratorContext(
            "R",
            ExecutionMode.LIVE_PRIMARY,
            {"industry_dna_profiles": (profile(),)},
        )
    )
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
    data = {
        **plan_stage.outputs,
        "evidence_ledger": ledger,
        "hypotheses": (hypothesis,),
        "bridges": (bridge,),
    }

    audit = broker_research_audit_adapter(required=True)(
        OrchestratorContext("R", ExecutionMode.LIVE_PRIMARY, data)
    )

    assert audit.status is StageStatus.PASS
    assert audit.outputs["broker_research_audit_passed"]
    assert audit.outputs["broker_research_audit_hash"]
