from __future__ import annotations

from valuation_engine.capacity_commitment import (
    BaselineInclusionStatus,
    CapacityCommitmentInput,
    CapacityProjectBinding,
    CapacityProjectDisposition,
    CapacityQuantificationStatus,
    CapacitySegmentCommitmentInput,
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
from valuation_engine.signal_intelligence import (
    ProjectGate,
    ProjectGateEvidence,
    ProjectGateSet,
)


def segment(*, archetypes=("capacity_manufacturing",)) -> SegmentModuleRequirementPlan:
    value = SegmentModuleRequirementPlan(
        segment_id="core",
        sector_adapter="test.capacity",
        archetypes=archetypes,
        required_evidence=("expansion_land_control",),
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


def project_gates(
    *,
    project_id="P1",
    land_verified=True,
    land_evidence_id="E_LAND",
    include_land_observation=True,
) -> ProjectGateSet:
    observations = [
        ProjectGateEvidence(
            ProjectGate.ANNOUNCEMENT,
            True,
            ("E_ANNOUNCED",),
            "2025-12-01",
        )
    ]
    if include_land_observation:
        observations.append(
            ProjectGateEvidence(
                ProjectGate.LAND_CONTROL,
                land_verified,
                (land_evidence_id,),
                "2026-01-01",
            )
        )
    value = ProjectGateSet(
        project_id=project_id,
        required_gates=(ProjectGate.ANNOUNCEMENT, ProjectGate.LAND_CONTROL),
        observations=tuple(observations),
    )
    value.validate()
    return value


def binding(
    *,
    project_id="P1",
    land_verified=True,
    include_land_observation=True,
    baseline=BaselineInclusionStatus.NOT_IN_BASELINE,
    disposition=CapacityProjectDisposition.ACTIVE,
    capacity_ids=(),
    site_ids=(),
    capex_ids=(),
    ramp_ids=(),
    equipment_ids=(),
    disposition_ids=(),
) -> CapacityProjectBinding:
    return CapacityProjectBinding(
        project_id=project_id,
        segment_id="core",
        gate_set=project_gates(
            project_id=project_id,
            land_verified=land_verified,
            include_land_observation=include_land_observation,
        ),
        baseline_inclusion=baseline,
        baseline_inclusion_evidence_ids=(
            () if baseline is BaselineInclusionStatus.UNKNOWN else (f"E_BASE_{project_id}",)
        ),
        disposition=disposition,
        disposition_evidence_ids=disposition_ids,
        committed_capacity_evidence_ids=capacity_ids,
        site_area_evidence_ids=site_ids,
        committed_capex_evidence_ids=capex_ids,
        ramp_date_evidence_ids=ramp_ids,
        equipment_commitment_evidence_ids=equipment_ids,
    )


def inputs(*projects: CapacityProjectBinding) -> CapacityCommitmentInput:
    return CapacityCommitmentInput(
        (CapacitySegmentCommitmentInput("core", projects=projects),)
    )


def no_active_inputs() -> CapacityCommitmentInput:
    return CapacityCommitmentInput(
        (
            CapacitySegmentCommitmentInput(
                "core",
                no_active_expansion_evidence_ids=("E_NO_ACTIVE",),
            ),
        )
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


def common_records(
    *,
    project_id="P1",
    land_value=True,
    land_source=EvidenceSourceLayer.REALIZED_OR_FILING,
    baseline=BaselineInclusionStatus.NOT_IN_BASELINE,
):
    values = [
        evidence("E_ANNOUNCED", "capacity_expansion_announcement", True),
        evidence(
            "E_LAND",
            "expansion_land_control",
            land_value,
            source_layer=land_source,
        ),
    ]
    if baseline is not BaselineInclusionStatus.UNKNOWN:
        values.append(
            evidence(
                f"E_BASE_{project_id}",
                "expansion_baseline_inclusion",
                baseline.value,
            )
        )
    return values


def test_land_control_contract_is_the_core_inclusion_threshold():
    records = common_records()
    records.append(evidence("E_SITE", "expansion_site_area", 11000, unit="pyeong"))
    configured = inputs(binding(site_ids=("E_SITE",)))

    result = capacity_commitment_gate_adapter(loader=lambda _: configured)(
        context(tuple(records))
    )

    assert result.status is StageStatus.PASS
    assessment = result.outputs["capacity_commitment_assessment"]
    project = assessment.segments[0].projects[0]
    assert ProjectGate.LAND_CONTROL in project.verified_gates
    assert project.land_control_verified
    assert project.core_inclusion_required
    assert (
        project.quantification_status
        is CapacityQuantificationStatus.BOUNDED_INPUTS_AVAILABLE
    )
    assert assessment.core_inclusion_required_projects == ("P1",)


def test_announcement_without_verified_land_control_does_not_open_core():
    records = common_records(land_value=False)
    configured = inputs(binding(land_verified=False))

    result = capacity_commitment_gate_adapter(loader=lambda _: configured)(
        context(tuple(records))
    )

    assert result.status is StageStatus.PASS
    project = result.outputs["capacity_commitment_assessment"].segments[0].projects[0]
    assert not project.land_control_verified
    assert not project.core_inclusion_required
    assert project.quantification_status is CapacityQuantificationStatus.NOT_REQUIRED


def test_unresolved_land_control_requires_recovery_not_silent_no_expansion():
    records = (
        evidence("E_ANNOUNCED", "capacity_expansion_announcement", True),
        evidence(
            "E_BASE_P1",
            "expansion_baseline_inclusion",
            "not_in_baseline",
        ),
    )
    configured = inputs(binding(include_land_observation=False))

    result = capacity_commitment_gate_adapter(loader=lambda _: configured)(
        context(records)
    )

    assert result.status is StageStatus.RECOVERY_REQUIRED
    project = result.outputs["capacity_commitment_assessment"].segments[0].projects[0]
    assert project.recovery_required
    assert "absence cannot be treated as no contract" in project.rationale


def test_exact_capacity_is_not_required_for_eligibility_but_sizing_cannot_be_zero():
    configured = inputs(binding())

    result = capacity_commitment_gate_adapter(loader=lambda _: configured)(
        context(tuple(common_records()))
    )

    assert result.status is StageStatus.RECOVERY_REQUIRED
    project = result.outputs["capacity_commitment_assessment"].segments[0].projects[0]
    assert project.core_inclusion_required
    assert project.quantification_status is CapacityQuantificationStatus.UNQUANTIFIED
    assert project.recovery_required


def test_disclosed_committed_capacity_passes_with_disclosed_status():
    records = common_records()
    records.append(
        evidence(
            "E_CAPACITY",
            "expansion_capacity_committed",
            2000,
            unit="KRW_100M_revenue_capacity",
        )
    )
    configured = inputs(binding(capacity_ids=("E_CAPACITY",)))

    assessment = assess_capacity_commitment(
        plan=plan(),
        ledger=EvidenceLedger(tuple(records)),
        inputs=configured,
    )

    project = assessment.segments[0].projects[0]
    assert project.core_inclusion_required
    assert project.quantification_status is CapacityQuantificationStatus.DISCLOSED


def test_external_reference_cannot_verify_land_control_pre_freeze():
    records = common_records(
        land_source=EvidenceSourceLayer.EXTERNAL_REFERENCE
    )
    records.append(evidence("E_SITE", "expansion_site_area", 11000, unit="pyeong"))
    configured = inputs(binding(site_ids=("E_SITE",)))

    result = capacity_commitment_gate_adapter(loader=lambda _: configured)(
        context(tuple(records))
    )

    assert result.status is StageStatus.BLOCKED
    assert "cannot use source layer external_reference" in result.rationale


def test_cancelled_project_does_not_remain_in_core():
    records = common_records(baseline=BaselineInclusionStatus.UNKNOWN)
    records.append(evidence("E_CANCELLED", "expansion_cancelled", True))
    configured = inputs(
        binding(
            baseline=BaselineInclusionStatus.UNKNOWN,
            disposition=CapacityProjectDisposition.CANCELLED,
            disposition_ids=("E_CANCELLED",),
        )
    )

    result = capacity_commitment_gate_adapter(loader=lambda _: configured)(
        context(tuple(records))
    )

    assert result.status is StageStatus.PASS
    project = result.outputs["capacity_commitment_assessment"].segments[0].projects[0]
    assert project.disposition is CapacityProjectDisposition.CANCELLED
    assert not project.core_inclusion_required


def test_capacity_already_in_baseline_is_not_added_again():
    records = common_records(baseline=BaselineInclusionStatus.IN_BASELINE)
    records.append(
        evidence(
            "E_CAPACITY",
            "expansion_capacity_committed",
            2000,
            unit="KRW_100M_revenue_capacity",
        )
    )
    configured = inputs(
        binding(
            baseline=BaselineInclusionStatus.IN_BASELINE,
            capacity_ids=("E_CAPACITY",),
        )
    )

    result = capacity_commitment_gate_adapter(loader=lambda _: configured)(
        context(tuple(records))
    )

    assert result.status is StageStatus.PASS
    project = result.outputs["capacity_commitment_assessment"].segments[0].projects[0]
    assert project.land_control_verified
    assert not project.core_inclusion_required
    assert "cannot be added again" in project.rationale


def test_unknown_baseline_treatment_requires_recovery():
    records = common_records(baseline=BaselineInclusionStatus.UNKNOWN)
    records.append(evidence("E_SITE", "expansion_site_area", 11000, unit="pyeong"))
    configured = inputs(
        binding(
            baseline=BaselineInclusionStatus.UNKNOWN,
            site_ids=("E_SITE",),
        )
    )

    result = capacity_commitment_gate_adapter(loader=lambda _: configured)(
        context(tuple(records))
    )

    assert result.status is StageStatus.RECOVERY_REQUIRED
    project = result.outputs["capacity_commitment_assessment"].segments[0].projects[0]
    assert "incremental-vs-baseline treatment is unknown" in project.rationale


def test_explicit_no_active_expansion_is_allowed_but_absence_is_not():
    result = capacity_commitment_gate_adapter(loader=lambda _: no_active_inputs())(
        context(
            (
                evidence(
                    "E_NO_ACTIVE",
                    "no_active_capacity_expansion",
                    True,
                ),
            )
        )
    )

    assert result.status is StageStatus.PASS
    segment_assessment = result.outputs["capacity_commitment_assessment"].segments[0]
    assert segment_assessment.no_active_expansion_verified
    assert segment_assessment.projects == ()


def test_multiple_projects_are_assessed_independently():
    records = common_records(project_id="P1")
    records.extend(
        (
            evidence("E_SITE", "expansion_site_area", 11000, unit="pyeong"),
            evidence("E_ANNOUNCED_2", "capacity_expansion_announcement", True),
            evidence("E_LAND_2", "expansion_land_control", True),
            evidence(
                "E_BASE_P2",
                "expansion_baseline_inclusion",
                "unknown",
            ),
            evidence("E_CANCELLED_2", "expansion_cancelled", True),
        )
    )
    first = binding(project_id="P1", site_ids=("E_SITE",))
    second_gate_set = ProjectGateSet(
        project_id="P2",
        required_gates=(ProjectGate.ANNOUNCEMENT, ProjectGate.LAND_CONTROL),
        observations=(
            ProjectGateEvidence(
                ProjectGate.ANNOUNCEMENT,
                True,
                ("E_ANNOUNCED_2",),
            ),
            ProjectGateEvidence(
                ProjectGate.LAND_CONTROL,
                True,
                ("E_LAND_2",),
            ),
        ),
    )
    second = CapacityProjectBinding(
        project_id="P2",
        segment_id="core",
        gate_set=second_gate_set,
        baseline_inclusion=BaselineInclusionStatus.UNKNOWN,
        baseline_inclusion_evidence_ids=(),
        disposition=CapacityProjectDisposition.CANCELLED,
        disposition_evidence_ids=("E_CANCELLED_2",),
    )
    configured = inputs(first, second)

    result = capacity_commitment_gate_adapter(loader=lambda _: configured)(
        context(tuple(records))
    )

    assert result.status is StageStatus.PASS
    assessment = result.outputs["capacity_commitment_assessment"]
    assert assessment.core_inclusion_required_projects == ("P1",)
    assert tuple(item.project_id for item in assessment.segments[0].projects) == (
        "P1",
        "P2",
    )


def test_non_capacity_route_skips_without_invoking_loader():
    result = capacity_commitment_gate_adapter(loader=None)(
        context((), archetypes=("recurring_subscription",))
    )

    assert result.status is StageStatus.SKIPPED_NOT_APPLICABLE
    assert result.outputs["capacity_commitment_assessment"].segments == ()


def test_capacity_route_without_typed_loader_is_not_implemented():
    result = capacity_commitment_gate_adapter(loader=None)(context(()))

    assert result.status is StageStatus.NOT_IMPLEMENTED
    assert result.blocking


def test_malformed_canonical_capacity_metric_blocks():
    records = common_records()
    records.append(
        evidence(
            "E_CAPACITY",
            "expansion_capacity_committed",
            "unknown",
            unit="MW",
        )
    )
    configured = inputs(binding(capacity_ids=("E_CAPACITY",)))

    result = capacity_commitment_gate_adapter(loader=lambda _: configured)(
        context(tuple(records))
    )

    assert result.status is StageStatus.BLOCKED
    assert "must be numeric" in result.rationale


def test_assessment_hash_changes_when_qualifying_evidence_changes():
    first_records = common_records()
    first_records.append(
        evidence("E_SITE", "expansion_site_area", 11000, unit="pyeong")
    )
    second_records = common_records()
    second_records.append(
        evidence("E_SITE", "expansion_site_area", 12000, unit="pyeong")
    )
    configured = inputs(binding(site_ids=("E_SITE",)))

    first = assess_capacity_commitment(
        plan=plan(),
        ledger=EvidenceLedger(tuple(first_records)),
        inputs=configured,
    )
    second = assess_capacity_commitment(
        plan=plan(),
        ledger=EvidenceLedger(tuple(second_records)),
        inputs=configured,
    )

    assert first.assessment_hash != second.assessment_hash
