from __future__ import annotations

from valuation_engine.capacity_commitment import (
    CapacityCommitmentStage,
    CapacityQuantificationStatus,
    assess_capacity_commitment,
    capacity_commitment_gate_adapter,
)
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.module_plan import (
    ModuleRequirementPlan,
    SegmentModuleRequirementPlan,
)
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer


def segment(*, archetypes=("capacity_manufacturing",)) -> SegmentModuleRequirementPlan:
    value = SegmentModuleRequirementPlan(
        segment_id="core",
        sector_adapter="test.capacity",
        archetypes=archetypes,
        required_evidence=("capacity_commitment_stage",),
        required_kpis=("capacity",),
        mandatory_scanners=("TEST",),
        kill_conditions=("expansion cancelled",),
        normalization_rules=("nameplate vs effective",),
        beta_peer_features=("fixed cost",),
        per_peer_features=("incremental roic",),
        scenario_variables=("capacity", "capex"),
        funding_scans=(),
        terminal_policies=("normalized utilization",),
        double_count_traps=("capacity option plus expansion EBITDA",),
        forbidden_methods=("peak margin perpetuity",),
        allowed_valuation_methods=("driver_dcf",),
    )
    value.validate()
    return value


def plan(*, archetypes=("capacity_manufacturing",)) -> ModuleRequirementPlan:
    item = segment(archetypes=archetypes)
    value = ModuleRequirementPlan(
        segments=(item,),
        common_core_modules=("evidence_gate",),
        required_evidence=item.required_evidence,
        required_kpis=item.required_kpis,
        mandatory_scanners=item.mandatory_scanners,
        kill_conditions=item.kill_conditions,
        scenario_variables=item.scenario_variables,
        double_count_traps=item.double_count_traps,
        forbidden_methods=item.forbidden_methods,
    )
    value.validate()
    return value


def evidence(
    evidence_id: str,
    metric: str,
    value,
    *,
    unit="dimensionless",
    effective_date="2026-01-01",
    source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        target="SANIL",
        metric=metric,
        value=value,
        unit=unit,
        source_layer=source_layer,
        effective_date=effective_date,
        observed_date=effective_date,
        source_name="official filing",
        source_ref=f"https://example.test/{evidence_id}",
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def context(records, *, archetypes=("capacity_manufacturing",)):
    return OrchestratorContext(
        "RUN",
        ExecutionMode.LIVE_PRIMARY,
        {
            "module_requirement_plan": plan(archetypes=archetypes),
            "evidence_ledger": EvidenceLedger(records),
        },
    )


def test_site_contract_is_the_minimum_core_inclusion_threshold():
    stage = evidence(
        "E_STAGE",
        "capacity_commitment_stage",
        "site_contracted",
    )
    site = evidence(
        "E_SITE",
        "expansion_site_area",
        11000,
        unit="pyeong",
    )

    result = capacity_commitment_gate_adapter()(context((stage, site)))

    assert result.status is StageStatus.PASS
    assessment = result.outputs["capacity_commitment_assessment"]
    item = assessment.segments[0]
    assert item.latest_stage is CapacityCommitmentStage.SITE_CONTRACTED
    assert item.core_inclusion_required
    assert (
        item.quantification_status
        is CapacityQuantificationStatus.BOUNDED_INPUTS_AVAILABLE
    )
    assert assessment.core_inclusion_required_segments == ("core",)


def test_announcement_does_not_open_core_capacity():
    result = capacity_commitment_gate_adapter()(
        context(
            (
                evidence(
                    "E_STAGE",
                    "capacity_commitment_stage",
                    "announced",
                ),
            )
        )
    )

    assert result.status is StageStatus.PASS
    item = result.outputs["capacity_commitment_assessment"].segments[0]
    assert not item.core_inclusion_required
    assert item.quantification_status is CapacityQuantificationStatus.NOT_REQUIRED


def test_exact_capacity_is_not_required_for_eligibility_but_sizing_cannot_be_silent():
    result = capacity_commitment_gate_adapter()(
        context(
            (
                evidence(
                    "E_STAGE",
                    "capacity_commitment_stage",
                    "site_contracted",
                ),
            )
        )
    )

    assert result.status is StageStatus.RECOVERY_REQUIRED
    item = result.outputs["capacity_commitment_assessment"].segments[0]
    assert item.core_inclusion_required
    assert item.quantification_status is CapacityQuantificationStatus.UNQUANTIFIED
    assert item.recovery_required
    assert result.blocking


def test_disclosed_committed_capacity_passes_with_disclosed_status():
    assessment = assess_capacity_commitment(
        plan=plan(),
        ledger=EvidenceLedger(
            (
                evidence(
                    "E_STAGE",
                    "capacity_commitment_stage",
                    "site_acquired",
                ),
                evidence(
                    "E_CAPACITY",
                    "expansion_capacity_committed",
                    2000,
                    unit="KRW_100M_revenue_capacity",
                ),
            )
        ),
    )

    item = assessment.segments[0]
    assert item.core_inclusion_required
    assert item.quantification_status is CapacityQuantificationStatus.DISCLOSED
    assert item.committed_capacity_evidence_ids == ("E_CAPACITY",)


def test_external_reference_cannot_open_the_pre_freeze_core_gate():
    result = capacity_commitment_gate_adapter()(
        context(
            (
                evidence(
                    "E_STREET_STAGE",
                    "capacity_commitment_stage",
                    "under_construction",
                    source_layer=EvidenceSourceLayer.EXTERNAL_REFERENCE,
                ),
                evidence(
                    "E_STREET_CAPACITY",
                    "expansion_capacity_committed",
                    3000,
                    unit="KRW_100M_revenue_capacity",
                    source_layer=EvidenceSourceLayer.EXTERNAL_REFERENCE,
                ),
            )
        )
    )

    assert result.status is StageStatus.RECOVERY_REQUIRED
    item = result.outputs["capacity_commitment_assessment"].segments[0]
    assert item.latest_stage is None
    assert not item.core_inclusion_required


def test_latest_cancellation_overrides_prior_acquisition():
    result = capacity_commitment_gate_adapter()(
        context(
            (
                evidence(
                    "E_ACQUIRED",
                    "capacity_commitment_stage",
                    "site_acquired",
                    effective_date="2026-01-01",
                ),
                evidence(
                    "E_CANCELLED",
                    "capacity_commitment_stage",
                    "cancelled",
                    effective_date="2026-02-01",
                ),
                evidence(
                    "E_SITE",
                    "expansion_site_area",
                    11000,
                    unit="pyeong",
                    effective_date="2026-01-01",
                ),
            )
        )
    )

    assert result.status is StageStatus.PASS
    item = result.outputs["capacity_commitment_assessment"].segments[0]
    assert item.latest_stage is CapacityCommitmentStage.CANCELLED
    assert not item.core_inclusion_required
    assert item.quantification_status is CapacityQuantificationStatus.NOT_REQUIRED


def test_non_capacity_route_is_explicitly_not_applicable():
    result = capacity_commitment_gate_adapter()(
        context((), archetypes=("recurring_subscription",))
    )

    assert result.status is StageStatus.SKIPPED_NOT_APPLICABLE
    assert result.outputs["capacity_commitment_assessment"].segments == ()


def test_capacity_route_without_official_stage_evidence_requires_recovery():
    result = capacity_commitment_gate_adapter()(
        context(
            (
                evidence(
                    "E_CAPEX",
                    "expansion_capex_committed",
                    420,
                    unit="KRW_100M",
                ),
            )
        )
    )

    assert result.status is StageStatus.RECOVERY_REQUIRED
    assert result.outputs["capacity_commitment_recovery_segments"] == ("core",)


def test_malformed_canonical_capacity_metric_blocks():
    result = capacity_commitment_gate_adapter()(
        context(
            (
                evidence(
                    "E_STAGE",
                    "capacity_commitment_stage",
                    "site_contracted",
                ),
                evidence(
                    "E_CAPACITY",
                    "expansion_capacity_committed",
                    "unknown",
                    unit="MW",
                ),
            )
        )
    )

    assert result.status is StageStatus.BLOCKED
    assert "must be numeric" in result.rationale


def test_assessment_hash_changes_when_qualifying_evidence_changes():
    first = assess_capacity_commitment(
        plan=plan(),
        ledger=EvidenceLedger(
            (
                evidence(
                    "E_STAGE",
                    "capacity_commitment_stage",
                    "site_contracted",
                ),
                evidence(
                    "E_SITE",
                    "expansion_site_area",
                    11000,
                    unit="pyeong",
                ),
            )
        ),
    )
    second = assess_capacity_commitment(
        plan=plan(),
        ledger=EvidenceLedger(
            (
                evidence(
                    "E_STAGE",
                    "capacity_commitment_stage",
                    "site_contracted",
                ),
                evidence(
                    "E_SITE",
                    "expansion_site_area",
                    12000,
                    unit="pyeong",
                ),
            )
        ),
    )

    assert first.assessment_hash != second.assessment_hash
