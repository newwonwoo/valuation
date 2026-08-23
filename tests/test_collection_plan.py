from dataclasses import replace
from pathlib import Path

from valuation_engine.collection_plan import (
    CollectionReadiness,
    CollectorCapability,
    SourceMatchKind,
    compile_company_collection_plan,
    load_source_descriptors,
)
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.evidence_adapter import (
    EvidenceCollectorSelection,
    SelectedEvidenceCollector,
    primary_evidence_collection_adapter,
)
from valuation_engine.evidence_collection import static_evidence_collector
from valuation_engine.live_primary_adapters import ResolvedCompanyIdentity
from valuation_engine.module_plan import (
    ModuleRequirementPlan,
    SegmentModuleRequirementPlan,
)
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer


ROOT = Path(__file__).resolve().parents[1]


def identity(jurisdiction: str = "KR") -> ResolvedCompanyIdentity:
    external_key = "corp_code" if jurisdiction == "KR" else "cik"
    return ResolvedCompanyIdentity(
        target_id="T",
        legal_name="Target Co",
        ticker="000000",
        jurisdiction=jurisdiction,
        external_ids=((external_key, "00000000"),),
        source_refs=(f"fixture://identity/{jurisdiction}",),
    )


def segment_plan(
    segment_id: str,
    *,
    required=("utilization", "backlog"),
    sector_adapter="power.transformer_switchgear",
    archetypes=("capacity_manufacturing",),
    methods=("driver_dcf",),
) -> SegmentModuleRequirementPlan:
    segment = SegmentModuleRequirementPlan(
        segment_id=segment_id,
        sector_adapter=sector_adapter,
        archetypes=archetypes,
        required_evidence=required,
        required_kpis=(*required, "lead_time"),
        mandatory_scanners=("CAPACITY_UTILIZATION",),
        kill_conditions=("utilization collapse",),
        normalization_rules=("utilization_definition",),
        beta_peer_features=("fixed_cost_intensity",),
        per_peer_features=("utilization_duration",),
        scenario_variables=("utilization",),
        funding_scans=(),
        terminal_policies=("normalize utilization",),
        double_count_traps=("growth_without_capex",),
        forbidden_methods=(),
        allowed_valuation_methods=methods,
    )
    segment.validate()
    return segment


def module_plan(
    *segments: SegmentModuleRequirementPlan,
) -> ModuleRequirementPlan:
    items = segments or (segment_plan("core"),)
    plan = ModuleRequirementPlan(
        segments=tuple(items),
        common_core_modules=("evidence_gate",),
        required_evidence=tuple(
            dict.fromkeys(
                metric
                for segment in items
                for metric in segment.required_evidence
            )
        ),
        required_kpis=tuple(
            dict.fromkeys(
                metric
                for segment in items
                for metric in segment.required_kpis
            )
        ),
        mandatory_scanners=tuple(
            dict.fromkeys(
                value
                for segment in items
                for value in segment.mandatory_scanners
            )
        ),
        kill_conditions=tuple(
            dict.fromkeys(
                value
                for segment in items
                for value in segment.kill_conditions
            )
        ),
        scenario_variables=tuple(
            dict.fromkeys(
                value
                for segment in items
                for value in segment.scenario_variables
            )
        ),
        double_count_traps=tuple(
            dict.fromkeys(
                value
                for segment in items
                for value in segment.double_count_traps
            )
        ),
        forbidden_methods=tuple(
            dict.fromkeys(
                value
                for segment in items
                for value in segment.forbidden_methods
            )
        ),
    )
    plan.validate()
    return plan


def dynamic_context(
    plan: ModuleRequirementPlan,
    company: ResolvedCompanyIdentity | None = None,
) -> OrchestratorContext:
    company = company or identity()
    return OrchestratorContext(
        "RUN",
        ExecutionMode.LIVE_PRIMARY,
        {
            "target_id": company.target_id,
            "jurisdiction": company.jurisdiction,
            "resolved_company_identity": company,
            "module_requirement_plan": plan,
            "required_evidence": plan.required_evidence,
        },
    )


def source_registry(
    tmp_path: Path,
    *,
    include_automotive=False,
) -> Path:
    path = tmp_path / "sources.yaml"
    automotive = (
        """
- id: KR_KAMA
  authority: industry_association
  roles: [observed_state]
  access: public_file
  industries: [automotive]
  metrics: [production]
"""
        if include_automotive
        else ""
    )
    path.write_text(
        """sources:
- id: KR_KOSIS_API
  authority: official_statistics
  roles: [observed_state]
  access: api
  industries: [cross_industry]
  metrics: [utilization, production]
- id: KR_OPENDART
  authority: regulator_primary
  roles: [observed_state, company_primary]
  access: api
  industries: [listed_companies]
  metrics: [financials, contracts]
- id: US_SEC
  authority: regulator_primary
  roles: [observed_state, company_primary]
  access: api
  industries: [listed_companies]
  metrics: [financials]
"""
        + automotive,
        encoding="utf-8",
    )
    return path


def evidence(
    metric: str,
    *,
    segment: str = "core",
) -> EvidenceRecord:
    return EvidenceRecord(
        id=f"EV-{segment}-{metric}",
        target="T",
        metric=metric,
        value=1,
        unit="ratio" if metric == "utilization" else "KRW",
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-06-30",
        observed_date="2026-08-01",
        source_name="fixture",
        source_ref="fixture://primary",
        source_grade="A",
        confidence=1.0,
        segment=segment,
    )


def collector(
    source_id: str,
    *records: EvidenceRecord,
    fingerprint: str = "fixture-hash",
):
    return static_evidence_collector(
        source_id=source_id,
        checked_at="2026-08-01",
        records=tuple(records),
        source_fingerprint=fingerprint,
    )


def test_repo_industry_source_registry_is_compatible_with_collection_planner():
    sources = load_source_descriptors(
        ROOT / "config" / "industry_source_registry.yaml"
    )
    by_id = {item.source_id: item for item in sources}
    assert "KR_OPENDART" in by_id
    assert "company_primary" in by_id["KR_OPENDART"].roles
    assert any("utilization" in item.metrics for item in sources)


def test_company_collection_plan_distinguishes_source_candidate_from_runnable_collector(
    tmp_path,
):
    capability = CollectorCapability(
        "dart-backlog",
        "KR_OPENDART",
        ("backlog",),
        ("KR",),
        "tests.fixture",
    )
    result = compile_company_collection_plan(
        module_plan(),
        company=identity(),
        source_registry_path=source_registry(tmp_path),
        collector_capabilities=(capability,),
    )
    by_metric = {
        item.metric: item
        for item in result.requirements
        if item.segment_id == "core"
    }
    assert result.version == "0.5.2"
    assert result.plan_id.startswith("COLLECTION_")
    assert result.routing_hash
    assert (
        by_metric["utilization"].readiness
        is CollectionReadiness.SOURCE_CANDIDATE_ONLY
    )
    assert (
        by_metric["utilization"].source_candidates[0].source_id
        == "KR_KOSIS_API"
    )
    assert (
        by_metric["utilization"].source_candidates[0].match_kind
        is SourceMatchKind.EXACT_METRIC
    )
    assert (
        by_metric["backlog"].readiness
        is CollectionReadiness.COLLECTOR_READY
    )
    assert (
        by_metric["backlog"].source_candidates[0].source_id
        == "KR_OPENDART"
    )
    assert (
        by_metric["backlog"].source_candidates[0].match_kind
        is SourceMatchKind.COMPANY_PRIMARY_FALLBACK
    )
    assert result.missing_required_metrics == ("utilization",)
    assert result.missing_required_requirements == (
        "core:required_evidence:utilization",
    )
    assert result.runnable_collector_ids == ("dart-backlog",)
    assert result.authorized_segment_metrics_for_collector(
        "dart-backlog"
    ) == (("core", "backlog"),)
    assert result.task_for_collector("dart-backlog").source_id == "KR_OPENDART"


def test_industry_specific_source_is_not_routed_to_unrelated_segment(
    tmp_path,
):
    power_plan = module_plan(
        segment_plan("core", required=("production",))
    )
    result = compile_company_collection_plan(
        power_plan,
        company=identity(),
        source_registry_path=source_registry(
            tmp_path,
            include_automotive=True,
        ),
        collector_capabilities=(
            CollectorCapability(
                "kama-production",
                "KR_KAMA",
                ("production",),
                ("KR",),
                "tests.fixture",
            ),
        ),
    )
    requirement = result.required_evidence[0]
    assert "KR_KAMA" not in {
        item.source_id for item in requirement.source_candidates
    }
    assert "kama-production" not in requirement.collector_ids
    assert "KR_KOSIS_API" in {
        item.source_id for item in requirement.source_candidates
    }


def test_company_collection_plan_does_not_mix_kr_sources_into_us_target(
    tmp_path,
):
    result = compile_company_collection_plan(
        module_plan(),
        company=identity("US"),
        source_registry_path=source_registry(tmp_path),
    )
    by_metric = {
        item.metric: item
        for item in result.requirements
        if item.segment_id == "core"
    }
    assert tuple(
        item.source_id
        for item in by_metric["utilization"].source_candidates
    ) == ("US_SEC",)
    assert (
        by_metric["utilization"].source_candidates[0].match_kind
        is SourceMatchKind.COMPANY_PRIMARY_FALLBACK
    )
    assert all(
        not candidate.source_id.startswith("KR_")
        for item in result.requirements
        for candidate in item.source_candidates
    )


def test_primary_evidence_stage_accepts_runtime_company_collection_plan(
    tmp_path,
):
    current_plan = module_plan()
    plan = compile_company_collection_plan(
        current_plan,
        company=identity(),
        source_registry_path=source_registry(tmp_path),
        collector_capabilities=(
            CollectorCapability(
                "c-combined",
                "KR_OPENDART",
                ("utilization", "backlog"),
                ("KR",),
                "tests.fixture",
            ),
        ),
    )
    selected = collector(
        "KR_OPENDART",
        evidence("utilization"),
        evidence("backlog"),
    )
    adapter = primary_evidence_collection_adapter(
        selection_loader=lambda _: EvidenceCollectorSelection(
            plan,
            (SelectedEvidenceCollector("c-combined", selected),),
        )
    )
    result = adapter(dynamic_context(current_plan))
    assert result.status is StageStatus.PASS
    assert result.outputs["collection_plan"] == plan
    assert result.outputs["collection_selected_collector_ids"] == (
        "c-combined",
    )
    assert result.outputs["collection_missing_required_requirements"] == ()


def test_segment_level_coverage_cannot_be_satisfied_by_other_segment(
    tmp_path,
):
    current_plan = module_plan(
        segment_plan("A", required=("backlog",)),
        segment_plan("B", required=("backlog",)),
    )
    plan = compile_company_collection_plan(
        current_plan,
        company=identity(),
        source_registry_path=source_registry(tmp_path),
        collector_capabilities=(
            CollectorCapability(
                "c-backlog",
                "KR_OPENDART",
                ("backlog",),
                ("KR",),
                "tests.fixture",
            ),
        ),
    )
    selected = collector(
        "KR_OPENDART",
        evidence("backlog", segment="A"),
    )
    adapter = primary_evidence_collection_adapter(
        selection_loader=lambda _: EvidenceCollectorSelection(
            plan,
            (SelectedEvidenceCollector("c-backlog", selected),),
        )
    )
    result = adapter(dynamic_context(current_plan))
    assert result.status is StageStatus.RECOVERY_REQUIRED
    assert result.blocking
    assert result.outputs["collection_missing_required_requirements"] == (
        "B:required_evidence:backlog",
    )


def test_dynamic_collection_rejects_plan_from_different_resolved_identity_or_jurisdiction(
    tmp_path,
):
    current_plan = module_plan()
    us_plan = compile_company_collection_plan(
        current_plan,
        company=identity("US"),
        source_registry_path=source_registry(tmp_path),
    )
    adapter = primary_evidence_collection_adapter(
        selection_loader=lambda _: EvidenceCollectorSelection(us_plan, ())
    )
    result = adapter(dynamic_context(current_plan, identity("KR")))
    assert result.status is StageStatus.BLOCKED
    assert "identity does not match" in result.rationale


def test_dynamic_collection_rejects_stale_segment_route_even_when_metric_names_match(
    tmp_path,
):
    old_module_plan = module_plan(
        segment_plan("core", required=("backlog",))
    )
    stale_plan = compile_company_collection_plan(
        old_module_plan,
        company=identity(),
        source_registry_path=source_registry(tmp_path),
    )
    current_module_plan = module_plan(
        segment_plan(
            "core",
            required=("backlog",),
            sector_adapter="software.saas",
            archetypes=("recurring_subscription",),
            methods=("arr_fcf_dcf",),
        )
    )
    adapter = primary_evidence_collection_adapter(
        selection_loader=lambda _: EvidenceCollectorSelection(stale_plan, ())
    )
    result = adapter(dynamic_context(current_module_plan))
    assert result.status is StageStatus.BLOCKED
    assert "routing hash" in result.rationale


def test_dynamic_collection_rejects_corrupted_exact_segment_requirement(
    tmp_path,
):
    current_plan = module_plan()
    plan = compile_company_collection_plan(
        current_plan,
        company=identity(),
        source_registry_path=source_registry(tmp_path),
        collector_capabilities=(
            CollectorCapability(
                "c-combined",
                "KR_OPENDART",
                ("utilization", "backlog"),
                ("KR",),
                "tests.fixture",
            ),
        ),
    )
    original = plan.requirements[0]
    corrupted_id = original.requirement_id.replace("core:", "stale:", 1)
    corrupted_requirement = replace(
        original,
        requirement_id=corrupted_id,
        segment_id="stale",
    )
    corrupted_tasks = tuple(
        replace(
            task,
            requirement_ids=tuple(
                corrupted_id if item == original.requirement_id else item
                for item in task.requirement_ids
            ),
        )
        for task in plan.tasks
    )
    corrupted_plan = replace(
        plan,
        requirements=(corrupted_requirement, *plan.requirements[1:]),
        tasks=corrupted_tasks,
    )
    corrupted_plan.validate()
    adapter = primary_evidence_collection_adapter(
        selection_loader=lambda _: EvidenceCollectorSelection(
            corrupted_plan,
            (),
        )
    )
    result = adapter(dynamic_context(current_plan))
    assert result.status is StageStatus.BLOCKED
    assert "exact segment/metric/kind requirements" in result.rationale


def test_primary_evidence_stage_rejects_collector_not_authorized_by_plan(
    tmp_path,
):
    current_plan = module_plan()
    plan = compile_company_collection_plan(
        current_plan,
        company=identity(),
        source_registry_path=source_registry(tmp_path),
        collector_capabilities=(
            CollectorCapability(
                "c-backlog",
                "KR_OPENDART",
                ("backlog",),
                ("KR",),
                "tests.fixture",
            ),
        ),
    )
    selected = collector("KR_OPENDART", evidence("backlog"))
    adapter = primary_evidence_collection_adapter(
        selection_loader=lambda _: EvidenceCollectorSelection(
            plan,
            (SelectedEvidenceCollector("unplanned", selected),),
        )
    )
    result = adapter(dynamic_context(current_plan))
    assert result.status is StageStatus.BLOCKED
    assert "not authorized" in result.rationale


def test_primary_evidence_stage_rejects_batch_from_unplanned_source(
    tmp_path,
):
    current_plan = module_plan(
        segment_plan("core", required=("backlog",))
    )
    plan = compile_company_collection_plan(
        current_plan,
        company=identity(),
        source_registry_path=source_registry(tmp_path),
        collector_capabilities=(
            CollectorCapability(
                "c-backlog",
                "KR_OPENDART",
                ("backlog",),
                ("KR",),
                "tests.fixture",
            ),
        ),
    )
    wrong_source = collector("KR_KOSIS_API", evidence("backlog"))
    adapter = primary_evidence_collection_adapter(
        selection_loader=lambda _: EvidenceCollectorSelection(
            plan,
            (SelectedEvidenceCollector("c-backlog", wrong_source),),
        )
    )
    result = adapter(dynamic_context(current_plan))
    assert result.status is StageStatus.BLOCKED
    assert "Collection Plan requires KR_OPENDART" in result.rationale


def test_primary_evidence_stage_supports_partitioned_collectors_from_same_source(
    tmp_path,
):
    current_plan = module_plan()
    plan = compile_company_collection_plan(
        current_plan,
        company=identity(),
        source_registry_path=source_registry(tmp_path),
        collector_capabilities=(
            CollectorCapability(
                "c-backlog",
                "KR_OPENDART",
                ("backlog",),
                ("KR",),
                "tests.fixture.backlog",
            ),
            CollectorCapability(
                "c-utilization",
                "KR_OPENDART",
                ("utilization",),
                ("KR",),
                "tests.fixture.utilization",
            ),
        ),
    )
    selections = (
        SelectedEvidenceCollector(
            "c-backlog",
            collector(
                "KR_OPENDART",
                evidence("backlog"),
                fingerprint="backlog-hash",
            ),
        ),
        SelectedEvidenceCollector(
            "c-utilization",
            collector(
                "KR_OPENDART",
                evidence("utilization"),
                fingerprint="utilization-hash",
            ),
        ),
    )
    adapter = primary_evidence_collection_adapter(
        selection_loader=lambda _: EvidenceCollectorSelection(
            plan,
            selections,
        )
    )
    result = adapter(dynamic_context(current_plan))
    assert result.status is StageStatus.PASS
    assert tuple(
        batch.source_id
        for batch in result.outputs["evidence_collection_result"].batches
    ) == ("KR_OPENDART", "KR_OPENDART")
    assert result.outputs["evidence_missing_metrics"] == ()


def test_primary_evidence_stage_rejects_segment_metric_outside_collector_scope(
    tmp_path,
):
    current_plan = module_plan()
    plan = compile_company_collection_plan(
        current_plan,
        company=identity(),
        source_registry_path=source_registry(tmp_path),
        collector_capabilities=(
            CollectorCapability(
                "c-backlog",
                "KR_OPENDART",
                ("backlog",),
                ("KR",),
                "tests.fixture",
            ),
        ),
    )
    selected = collector(
        "KR_OPENDART",
        evidence("utilization"),
        evidence("backlog"),
    )
    adapter = primary_evidence_collection_adapter(
        selection_loader=lambda _: EvidenceCollectorSelection(
            plan,
            (SelectedEvidenceCollector("c-backlog", selected),),
        )
    )
    result = adapter(dynamic_context(current_plan))
    assert result.status is StageStatus.BLOCKED
    assert "outside Collection Plan" in result.rationale
    assert "core/utilization" in result.rationale


def test_primary_evidence_stage_fails_closed_when_plan_has_no_runnable_collector(
    tmp_path,
):
    current_plan = module_plan()
    plan = compile_company_collection_plan(
        current_plan,
        company=identity(),
        source_registry_path=source_registry(tmp_path),
    )
    adapter = primary_evidence_collection_adapter(
        selection_loader=lambda _: EvidenceCollectorSelection(plan, ())
    )
    result = adapter(dynamic_context(current_plan))
    assert result.status is StageStatus.NOT_IMPLEMENTED
    assert result.blocking
    assert result.outputs["collection_missing_required_requirements"] == (
        "core:required_evidence:utilization",
        "core:required_evidence:backlog",
    )
