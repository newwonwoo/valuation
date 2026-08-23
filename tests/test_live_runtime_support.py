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
from valuation_engine.doctrine_runtime import build_doctrine_coverage, load_default_unit_contract_registry
from valuation_engine.evidence_collection import EvidenceCollectionBatch, EvidenceCollectionRequest
from valuation_engine.live_primary_adapters import ResolvedCompanyIdentity
from valuation_engine.live_runtime import LiveCollectorProvider, _task_bound_collector
from valuation_engine.orchestrator import StageTrace


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
    bound = _task_bound_collector(provider, task=plan.tasks[0], collection_plan=plan)
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
    affected = tuple(
        item
        for item in snapshot.entries
        if "BLIND_RED_TEAM_B=RECOVERY_REQUIRED" in item.rationale
    )
    assert affected
    assert all(item.status is StageStatus.RECOVERED for item in affected)
    assert all(not item.unresolved_blocker for item in affected)
