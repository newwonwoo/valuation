from valuation_engine.collection_plan import (
    CollectionRequirement,
    CollectionRequirementKind,
    CollectionTask,
    CompanyCollectionPlan,
    SourceCandidate,
    SourceMatchKind,
    CollectorCapability,
)
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.doctrine_runtime import (
    build_doctrine_coverage,
    load_default_unit_contract_registry,
)
from valuation_engine.evidence_collection import (
    EvidenceCollectionBatch,
    EvidenceCollectionRequest,
)
from valuation_engine.live_primary_adapters import (
    CompanyResolutionRequest,
    ResolvedCompanyIdentity,
)
from valuation_engine.live_runtime import (
    LiveCollectorProvider,
    LivePrimaryProviders,
    LivePrimaryRuntimeConfig,
    _task_bound_collector,
    run_prism,
)
from valuation_engine.llm_staff import RedTeamProposal
from valuation_engine.orchestrator import (
    ControlledRunResult,
    OrchestratorContext,
    StageExecutionResult,
    StageTrace,
)
from valuation_engine.records import CriticalIssue
from valuation_engine.runtime_support_adapters import research_loop_recovery_adapter
from valuation_engine.scenario_binding import ScenarioBindingSpec


def identity() -> ResolvedCompanyIdentity:
    return ResolvedCompanyIdentity(
        target_id="T",
        legal_name="Target",
        ticker="000000",
        jurisdiction="KR",
        external_ids=(("corp_code", "00000000"),),
        source_refs=("fixture://identity",),
    )


def collection_plan() -> CompanyCollectionPlan:
    source = SourceCandidate(
        source_id="KR_OPENDART",
        authority="regulator_primary",
        access="api",
        match_kind=SourceMatchKind.EXACT_METRIC,
    )
    requirements = (
        CollectionRequirement(
            "core:required_evidence:backlog",
            "core",
            "backlog",
            CollectionRequirementKind.REQUIRED_EVIDENCE,
            True,
            (source,),
            ("dart",),
        ),
        CollectionRequirement(
            "core:supporting_kpi:lead_time",
            "core",
            "lead_time",
            CollectionRequirementKind.SUPPORTING_KPI,
            False,
            (source,),
            ("dart",),
        ),
    )
    task = CollectionTask(
        "TASK-1",
        "dart",
        "KR_OPENDART",
        tuple(item.requirement_id for item in requirements),
    )
    plan = CompanyCollectionPlan(
        plan_id="COLLECTION-1",
        version="0.5.2",
        company=identity(),
        routing_hash="ROUTE-HASH",
        requirements=requirements,
        tasks=(task,),
    )
    plan.validate()
    return plan


def test_run_prism_entrypoint_is_importable():
    assert callable(run_prism)


def test_run_prism_executes_from_non_repository_working_directory(
    tmp_path,
    monkeypatch,
):
    def unavailable_resolver(_):
        raise RuntimeError("fixture intentionally stops after runtime entry")

    noop = lambda *args, **kwargs: None
    providers = LivePrimaryProviders(
        company_resolver=unavailable_resolver,
        industry_snapshot_loader=noop,
        freshness_loader=noop,
        segment_decomposer=noop,
        industry_dna_router=noop,
        collectors=(
            LiveCollectorProvider(
                CollectorCapability(
                    collector_id="fixture",
                    source_id="KR_OPENDART",
                    supported_metrics=("financials",),
                    jurisdictions=("KR",),
                    implementation_ref="tests.fixture",
                ),
                noop,
            ),
        ),
        scanner_runners={},
        intelligence_officer=noop,
        red_team_officer=noop,
        bridge_analyst=noop,
        evaluator_registry_loader=noop,
        valuation_plan_inputs_loader=noop,
    )
    config = LivePrimaryRuntimeConfig(
        run_id="LIVE-ENTRY-1",
        state_root=tmp_path / "state",
        company_request=CompanyResolutionRequest("000000", "KR"),
        scenario_binding_spec=ScenarioBindingSpec(
            scenario_ids=("base",),
            required_keys=("revenue",),
        ),
        providers=providers,
    )

    monkeypatch.chdir(tmp_path)
    result = run_prism(config)

    assert isinstance(result, ControlledRunResult)
    assert result.execution_mode is ExecutionMode.LIVE_PRIMARY
    assert result.stage_traces[0].stage == "COMPANY_RESOLUTION"
    assert result.stage_traces[0].status is StageStatus.RECOVERY_REQUIRED
    assert result.blocked_reasons


def test_live_collector_provider_rejects_source_lineage_mismatch():
    capability = CollectorCapability(
        "dart",
        "KR_OPENDART",
        ("backlog",),
        ("KR",),
        "fixture",
    )

    def wrong_source(request):
        return EvidenceCollectionBatch(
            source_id="OTHER_SOURCE",
            checked_at="2026-08-24",
            records=(),
            source_fingerprint="HASH",
        )

    provider = LiveCollectorProvider(capability, wrong_source)
    collector = provider.bound_collector()
    try:
        collector(EvidenceCollectionRequest("T", ("backlog",)))
    except ValueError as exc:
        assert "source mismatch" in str(exc)
    else:
        raise AssertionError("source-lineage mismatch must be rejected")


def test_task_bound_collector_requests_required_and_supporting_metrics():
    seen = []
    capability = CollectorCapability(
        "dart",
        "KR_OPENDART",
        ("backlog", "lead_time"),
        ("KR",),
        "fixture",
    )

    def collector(request):
        seen.append(request.required_metrics)
        return EvidenceCollectionBatch(
            source_id="KR_OPENDART",
            checked_at="2026-08-24",
            records=(),
            source_fingerprint="HASH",
        )

    plan = collection_plan()
    provider = LiveCollectorProvider(capability, collector)
    bound = _task_bound_collector(
        provider,
        task=plan.tasks[0],
        collection_plan=plan,
    )
    bound(EvidenceCollectionRequest("T", ("backlog",)))
    assert seen == [("backlog", "lead_time")]


def test_research_loop_recovered_trace_clears_prior_recovery_request_in_doctrine():
    snapshot = build_doctrine_coverage(
        load_default_unit_contract_registry(),
        relevant_stages=("BLIND_RED_TEAM_B", "RESEARCH_LOOP"),
        required_stages=("BLIND_RED_TEAM_B", "RESEARCH_LOOP"),
        stage_traces=(
            StageTrace(
                "BLIND_RED_TEAM_B",
                StageStatus.RECOVERY_REQUIRED,
                "recoverable challenge",
                False,
            ),
            StageTrace(
                "RESEARCH_LOOP",
                StageStatus.RECOVERED,
                "targeted evidence resolved challenge",
                False,
            ),
        ),
    )
    entry = next(
        item for item in snapshot.entries if item.module_id == "BLIND_RED_TEAM_B"
    )
    assert "BLIND_RED_TEAM_B=recovery_required" in entry.rationale
    assert entry.status is StageStatus.RECOVERED
    assert not entry.unresolved_blocker


def _original_red_team_proposal() -> RedTeamProposal:
    return RedTeamProposal(
        issues=(CriticalIssue("RT-1", "material unresolved challenge"),),
        counter_thesis="the base thesis may fail",
    )


def test_research_recovery_cannot_omit_an_original_blocker():
    def recovery(_):
        return StageExecutionResult(
            StageStatus.PASS,
            "recovery attempted",
            {
                "recovered_red_team_proposal": RedTeamProposal(
                    issues=(),
                    counter_thesis="blocker was silently omitted",
                )
            },
        )

    result = research_loop_recovery_adapter(recovery)(
        OrchestratorContext(
            "RUN",
            ExecutionMode.LIVE_PRIMARY,
            {"red_team_proposal": _original_red_team_proposal()},
        )
    )
    assert result.status is StageStatus.RECOVERY_REQUIRED
    assert result.blocking
    assert "omitted=RT-1" in result.rationale


def test_research_recovery_requires_explicit_resolution_of_original_blocker():
    def recovery(_):
        return StageExecutionResult(
            StageStatus.PASS,
            "recovery completed",
            {
                "recovered_red_team_proposal": RedTeamProposal(
                    issues=(
                        CriticalIssue(
                            "RT-1",
                            "material challenge resolved with targeted evidence",
                            resolved=True,
                        ),
                    ),
                    counter_thesis="challenge tested and resolved",
                )
            },
        )

    result = research_loop_recovery_adapter(recovery)(
        OrchestratorContext(
            "RUN",
            ExecutionMode.LIVE_PRIMARY,
            {"red_team_proposal": _original_red_team_proposal()},
        )
    )
    assert result.status is StageStatus.RECOVERED
    assert not result.blocking
    assert result.outputs["recovered_red_team_issue_ids"] == ("RT-1",)
