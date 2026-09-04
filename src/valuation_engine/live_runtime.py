from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from .audit_adapter import generic_audit_adapter
from .broker_runtime import (
    BrokerResearchLoader,
    broker_aware_module_requirement_plan_adapter,
    broker_aware_rocket_insight_adapter,
    broker_research_audit_adapter,
)
from .capacity_commitment import (
    CapacityCommitmentLoader,
    capacity_commitment_gate_adapter,
)
from .capacity_consumption import (
    CapacityBridgeConsumptionLoader,
    capacity_bridge_consumption_gate_adapter,
)
from .capacity_runtime import (
    capacity_audit_adapter,
    capacity_consistency_gate_adapter,
    capacity_per_binding_adapter,
    capacity_scenario_binding_adapter,
    capacity_valuation_binding_adapter,
)
from .collection_plan import (
    CollectorCapability,
    CollectionTask,
    CompanyCollectionPlan,
    compile_company_collection_plan,
)
from .control_plane import ExecutionMode, StageStatus
from .dcf_evaluators import RegistryLoader
from .evidence_composition import evidence_composition_audit_adapter
from .evidence_adapter import (
    EvidenceCollectorSelection,
    SelectedEvidenceCollector,
    evidence_ledger_adapter,
    primary_evidence_collection_adapter,
)
from .evidence_collection import (
    EvidenceCollectionBatch,
    EvidenceCollectionRequest,
    EvidenceCollector,
)
from .funding_adapter import FundingScanner, live_upstream_funding_adapter
from .generic_reporting import (
    final_report_adapter,
    finalize_live_primary_run_artifacts,
    save_state_adapter,
    thesis_delta_adapter,
)
from .impact_adapter import GenericDecisionImpactConfig
from .live_primary_adapters import (
    CompanyResolutionRequest,
    CompanyResolver,
    FreshnessLoader,
    IndustryDNARouter,
    IndustrySnapshotLoader,
    ResolvedCompanyIdentity,
    SegmentDecomposer,
    live_company_resolution_adapter,
    live_industry_dna_route_adapter,
    live_industry_snapshot_adapter,
    live_segment_decomposition_adapter,
    live_source_freshness_adapter,
)
from .llm_adapters import (
    blind_red_team_adapter,
    evidence_to_assumption_bridge_adapter,
    researcher_a_adapter,
)
from .llm_staff import BridgeAnalyst, IntelligenceOfficer, RedTeamOfficer
from .method_capabilities import (
    MethodCapabilityRegistry,
    load_default_method_capability_registry,
)
from .module_plan import ModuleRequirementPlan
from .orchestrator import (
    ControlledRunResult,
    MajorGateReporter,
    OrchestratorContext,
    StageAdapter,
    StageExecutionResult,
    load_reporting_contract,
    load_stage_sequence,
    run_controlled_workflow,
)
from .partial_valuation import promote_partial_valuation_plan
from .per_adapters import PERInputsLoader, live_hierarchical_warranted_per_adapter
from .post_freeze_adapters import (
    MarketLoader,
    StreetLoader,
    market_compare_adapter,
    market_price_load_adapter,
    reverse_dcf_expectations_adapter,
    street_gap_analyzer_adapter,
    street_reference_load_adapter,
)
from .probability_adapter import (
    CalibrationSnapshotLoader,
    probability_calibration_load_adapter,
)
from .probability_forecasting import ProbabilityForecastHistoryStore
from .research_learning import ResearchLearningStore
from .risk_adapters import (
    BetaUniverseLoader,
    WACCInputsLoader,
    live_hierarchical_beta_adapter,
    live_wacc_validation_adapter,
)
from .runtime_support_adapters import (
    DCFConsistencyFingerprintLoader,
    chain_stage_adapters,
    conditional_funding_adapter,
    conditional_method_intent_adapter,
    conditional_warranted_per_adapter,
    cross_method_double_count_adapter,
    dcf_consistency_fingerprint_adapter,
    dcf_per_consistency_gate_adapter,
    probability_distribution_adapter,
    recoverable_red_team_adapter,
    recovery_aware_bridge_adapter,
    research_loop_recovery_adapter,
)
from .scanner_runtime import ScannerRunner, live_rocket_insight_dispatch_adapter
from .scenario_binding import ScenarioBindingSpec
from .shadow_adapters import load_company_state_adapter, scenario_build_adapter
from .state_learning_adapter import load_research_learning_adapter
from .unit_contracts import UnitContractRegistry, load_unit_contract_registry
from .valuation_adapter import deterministic_valuation_adapter
from .valuation_method_intent import valuation_method_intent_adapter
from .valuation_sensitivity import valuation_sensitivity_adapter
from .valuation_plan_compiler import (
    CompanyValuationPlanInputs,
    SegmentMethodChoice,
    compile_company_valuation_plan,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
PREFREEZE_COMPARISON_FIELDS = frozenset(
    {
        "consensus_target",
        "consensus_target_price",
        "current_market_price",
        "market",
        "market_comparison",
        "market_currency",
        "market_observation",
        "market_price",
        "market_reference",
        "market_reference_hash",
        "max_target_price",
        "median_target_price",
        "min_target_price",
        "rating",
        "consensus",
        "consensus_eps",
        "street",
        "street_comparison",
        "street_consensus",
        "street_consensus_eps",
        "street_reference",
        "street_reference_hash",
        "street_reports",
        "target_company_consensus",
        "target_company_consensus_eps",
        "target_company_forecast",
        "target_market_cap",
        "target_multiple",
        "target_price",
        "target_price_currency",
    }
)
ValuationPlanInputsLoader = Callable[[OrchestratorContext], CompanyValuationPlanInputs]

_BLOCKED_RESULT_INTRINSIC_KEYS = frozenset(
    {
        "company_state",
        "generic_valuation_result",
        "intrinsic_scenario_values",
        "expected_value_per_share",
        "valuation_hash",
        "valuation_scope",
        "unvalued_segments",
        "full_company_intrinsic_available",
        "intrinsic_freeze_token",
        "saved_current_state",
        "saved_report_markdown",
        "final_report",
        "street_comparison",
        "market_comparison",
    }
)


@dataclass(frozen=True)
class LiveCollectorProvider:
    capability: CollectorCapability
    collector: EvidenceCollector

    def validate(self) -> None:
        self.capability.validate()
        if not callable(self.collector):
            raise TypeError(
                f"collector provider {self.capability.collector_id} is not callable"
            )

    def bound_collector(self) -> EvidenceCollector:
        capability = self.capability
        collector = self.collector

        def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
            batch = collector(request)
            if not isinstance(batch, EvidenceCollectionBatch):
                raise TypeError(
                    f"collector {capability.collector_id} must return EvidenceCollectionBatch"
                )
            if batch.source_id != capability.source_id:
                raise ValueError(
                    f"collector {capability.collector_id} source mismatch: "
                    f"expected {capability.source_id}, got {batch.source_id}"
                )
            return batch

        return collect


@dataclass(frozen=True)
class LivePrimaryProviders:
    company_resolver: CompanyResolver
    industry_snapshot_loader: IndustrySnapshotLoader
    freshness_loader: FreshnessLoader
    segment_decomposer: SegmentDecomposer
    industry_dna_router: IndustryDNARouter
    collectors: tuple[LiveCollectorProvider, ...]
    scanner_runners: Mapping[str, ScannerRunner]
    intelligence_officer: IntelligenceOfficer
    red_team_officer: RedTeamOfficer
    bridge_analyst: BridgeAnalyst
    evaluator_registry_loader: RegistryLoader
    valuation_plan_inputs_loader: ValuationPlanInputsLoader
    broker_research_loader: BrokerResearchLoader | None = None
    capacity_commitment_loader: CapacityCommitmentLoader | None = None
    capacity_bridge_consumption_loader: CapacityBridgeConsumptionLoader | None = None
    funding_scanner: FundingScanner | None = None
    research_recovery_adapter: StageAdapter | None = None
    beta_loader: BetaUniverseLoader | None = None
    wacc_loader: WACCInputsLoader | None = None
    dcf_fingerprint_loader: DCFConsistencyFingerprintLoader | None = None
    per_loader: PERInputsLoader | None = None
    calibration_loader: CalibrationSnapshotLoader | None = None
    street_loader: StreetLoader | None = None
    market_loader: MarketLoader | None = None

    def validate(self) -> None:
        required_callables = (
            self.company_resolver,
            self.industry_snapshot_loader,
            self.freshness_loader,
            self.segment_decomposer,
            self.industry_dna_router,
            self.intelligence_officer,
            self.red_team_officer,
            self.bridge_analyst,
            self.evaluator_registry_loader,
            self.valuation_plan_inputs_loader,
        )
        if not all(callable(item) for item in required_callables):
            raise TypeError("LIVE_PRIMARY required providers must be callable")
        if not self.collectors:
            raise ValueError(
                "LIVE_PRIMARY requires at least one primary evidence collector provider"
            )
        collector_ids: list[str] = []
        for provider in self.collectors:
            provider.validate()
            collector_ids.append(provider.capability.collector_id)
        if len(collector_ids) != len(set(collector_ids)):
            raise ValueError("LIVE_PRIMARY collector provider IDs must be unique")
        if not isinstance(self.scanner_runners, Mapping):
            raise TypeError("scanner_runners must be a mapping")


@dataclass(frozen=True)
class LivePrimaryRuntimeConfig:
    run_id: str
    state_root: str | Path
    company_request: CompanyResolutionRequest
    scenario_binding_spec: ScenarioBindingSpec
    providers: LivePrimaryProviders
    method_choices: tuple[SegmentMethodChoice, ...] = ()
    additional_required_evidence: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )
    require_broker_research: bool = False
    capacity_core_scenario_id: str | None = None
    market_currency: str | None = None
    stage_registry_path: str | Path = (
        _REPO_ROOT / "config" / "control_plane_stage_registry.yaml"
    )
    archetype_registry_path: str | Path = (
        _REPO_ROOT / "config" / "archetype_module_registry.yaml"
    )
    archetype_control_requirements_path: str | Path = (
        _REPO_ROOT / "config" / "archetype_control_requirements.yaml"
    )
    industry_source_registry_path: str | Path = (
        _REPO_ROOT / "config" / "industry_source_registry.yaml"
    )
    unit_contract_registry_path: str | Path = (
        _REPO_ROOT / "config" / "unit_contract_registry.yaml"
    )
    capability_registry: MethodCapabilityRegistry | None = None
    impact_config: GenericDecisionImpactConfig | None = None
    major_gate_reporter: MajorGateReporter | None = None
    initial_data: Mapping[str, object] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.run_id:
            raise ValueError("LIVE_PRIMARY run_id is required")
        self.company_request.validate()
        self.scenario_binding_spec.validate()
        self.providers.validate()
        if not isinstance(self.additional_required_evidence, Mapping) or not all(
            isinstance(segment_id, str)
            and segment_id
            and isinstance(metrics, tuple)
            and all(isinstance(metric, str) and metric for metric in metrics)
            for segment_id, metrics in self.additional_required_evidence.items()
        ):
            raise TypeError(
                "additional_required_evidence must be a segment_id→tuple[str, ...] mapping"
            )
        if not isinstance(self.require_broker_research, bool):
            raise TypeError("require_broker_research must be bool")
        if self.require_broker_research and self.providers.broker_research_loader is None:
            raise ValueError(
                "require_broker_research=True requires broker_research_loader"
            )
        if self.providers.market_loader is not None and not self.market_currency:
            raise ValueError("LIVE_PRIMARY market_loader requires market_currency")
        if self.major_gate_reporter is not None and not callable(self.major_gate_reporter):
            raise TypeError("major_gate_reporter must be callable")
        prohibited = PREFREEZE_COMPARISON_FIELDS.intersection(self.initial_data)
        if prohibited:
            raise PermissionError(
                "LIVE_PRIMARY initial_data cannot contain pre-freeze target market/Street fields: "
                + ", ".join(sorted(prohibited))
            )


def _task_bound_collector(
    provider: LiveCollectorProvider,
    *,
    task: CollectionTask,
    collection_plan: CompanyCollectionPlan,
) -> EvidenceCollector:
    base = provider.bound_collector()
    by_id = {item.requirement_id: item for item in collection_plan.requirements}
    try:
        task_metrics = tuple(
            dict.fromkeys(
                by_id[requirement_id].metric
                for requirement_id in task.requirement_ids
            )
        )
    except KeyError as exc:
        raise ValueError(
            f"collection task references unknown requirement {exc.args[0]}"
        ) from exc
    if not task_metrics:
        raise ValueError(f"collection task {task.task_id} has no authorized metrics")

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        return base(EvidenceCollectionRequest(request.target_id, task_metrics))

    return collect


def _collection_selection_loader(config: LivePrimaryRuntimeConfig):
    providers = {
        item.capability.collector_id: item for item in config.providers.collectors
    }
    capabilities = tuple(item.capability for item in config.providers.collectors)

    def load(context: OrchestratorContext) -> EvidenceCollectorSelection:
        identity = context.data.get("resolved_company_identity")
        plan = context.data.get("module_requirement_plan")
        if not isinstance(identity, ResolvedCompanyIdentity):
            raise ValueError(
                "resolved company identity missing before collection planning"
            )
        if not isinstance(plan, ModuleRequirementPlan):
            raise ValueError("ModuleRequirementPlan missing before collection planning")
        collection_plan = compile_company_collection_plan(
            plan,
            company=identity,
            source_registry_path=config.industry_source_registry_path,
            collector_capabilities=capabilities,
        )
        selected = tuple(
            SelectedEvidenceCollector(
                task.collector_id,
                _task_bound_collector(
                    providers[task.collector_id],
                    task=task,
                    collection_plan=collection_plan,
                ),
            )
            for task in collection_plan.tasks
        )
        return EvidenceCollectorSelection(collection_plan, selected)

    return load


def _unavailable_stage(label: str) -> StageAdapter:
    def run(_: OrchestratorContext) -> StageExecutionResult:
        return StageExecutionResult(
            StageStatus.NOT_IMPLEMENTED,
            f"LIVE_PRIMARY {label} provider is not configured",
            blocking=True,
        )

    return run


def _freshness_precheck_adapter(loader: FreshnessLoader) -> StageAdapter:
    """Namespace the pre-collection freshness hash away from the Evidence snapshot hash."""
    inner = live_source_freshness_adapter(loader=loader)

    def run(context: OrchestratorContext) -> StageExecutionResult:
        result = inner(context)
        outputs = dict(result.outputs)
        freshness_hash = outputs.pop("source_snapshot_hash", None)
        if freshness_hash is not None:
            outputs["source_freshness_snapshot_hash"] = freshness_hash
        return StageExecutionResult(
            result.status,
            result.rationale,
            outputs,
            result.blocking,
        )

    return run


def _valuation_plan_loader(
    config: LivePrimaryRuntimeConfig,
    capability_registry: MethodCapabilityRegistry,
):
    def load(context: OrchestratorContext, evaluator_registry):
        module_plan = context.data.get("module_requirement_plan")
        scenario_set = context.data.get("bound_scenario_set")
        intent = context.data.get("valuation_method_intent")
        if not isinstance(module_plan, ModuleRequirementPlan):
            raise KeyError("module_requirement_plan")
        if scenario_set is None:
            raise KeyError("bound_scenario_set")
        if intent is None or not getattr(intent, "ready", False):
            raise KeyError("valuation_method_intent")
        inputs = config.providers.valuation_plan_inputs_loader(context)
        if not isinstance(inputs, CompanyValuationPlanInputs):
            raise TypeError(
                "valuation_plan_inputs_loader must return CompanyValuationPlanInputs"
            )
        compilation = compile_company_valuation_plan(
            module_plan,
            scenario_set,
            evaluator_registry=evaluator_registry,
            capability_registry=capability_registry,
            inputs=inputs,
            method_choices=intent.method_choices(),
        )
        return promote_partial_valuation_plan(
            compilation,
            inputs=inputs,
            scenario_set=scenario_set,
        )

    return load


def build_live_primary_adapters(
    config: LivePrimaryRuntimeConfig,
    *,
    unit_contract_registry: UnitContractRegistry | None = None,
) -> dict[str, StageAdapter]:
    config.validate()
    providers = config.providers
    capability_registry = (
        config.capability_registry or load_default_method_capability_registry()
    )
    state_root = Path(config.state_root)
    learning_store = ResearchLearningStore(state_root)
    probability_history_store = ProbabilityForecastHistoryStore(state_root)
    effective_unit_contract_registry = (
        unit_contract_registry
        if unit_contract_registry is not None
        else load_unit_contract_registry(config.unit_contract_registry_path)
    )
    effective_unit_contract_registry.validate()

    state_load = chain_stage_adapters(
        load_company_state_adapter(state_root=state_root),
        load_research_learning_adapter(store=learning_store),
    )
    capacity_commitment = capacity_commitment_gate_adapter(
        loader=providers.capacity_commitment_loader
    )

    funding = conditional_funding_adapter(
        live_upstream_funding_adapter(scanner=providers.funding_scanner)
        if providers.funding_scanner is not None
        else None
    )
    beta = conditional_method_intent_adapter(
        live_hierarchical_beta_adapter(loader=providers.beta_loader)
        if providers.beta_loader is not None
        else None,
        requirement="requires_beta",
        label="Hierarchical Beta",
    )
    wacc = conditional_method_intent_adapter(
        live_wacc_validation_adapter(loader=providers.wacc_loader)
        if providers.wacc_loader is not None
        else None,
        requirement="requires_wacc",
        label="WACC",
    )

    scenario_chain: list[StageAdapter] = [
        capacity_bridge_consumption_gate_adapter(
            loader=providers.capacity_bridge_consumption_loader
        )
    ]
    if providers.calibration_loader is not None:
        cohort = config.scenario_binding_spec.calibration_cohort_key
        if not cohort:
            raise ValueError(
                "calibration_loader requires scenario_binding_spec.calibration_cohort_key"
            )
        scenario_chain.append(
            probability_calibration_load_adapter(
                loader=providers.calibration_loader,
                expected_cohort_key=cohort,
            )
        )
    scenario_chain.append(scenario_build_adapter())
    scenario_chain.append(
        capacity_scenario_binding_adapter(
            core_scenario_id=config.capacity_core_scenario_id
        )
    )
    method_intent = valuation_method_intent_adapter(
        capability_registry=capability_registry,
        method_choices=config.method_choices,
    )

    valuation = chain_stage_adapters(
        deterministic_valuation_adapter(
            registry_loader=providers.evaluator_registry_loader,
            plan_loader=_valuation_plan_loader(config, capability_registry),
        ),
        dcf_consistency_fingerprint_adapter(providers.dcf_fingerprint_loader),
        capacity_valuation_binding_adapter(),
    )

    per = chain_stage_adapters(
        conditional_warranted_per_adapter(
            live_hierarchical_warranted_per_adapter(loader=providers.per_loader)
            if providers.per_loader is not None
            else None
        ),
        capacity_per_binding_adapter(),
    )

    street_load = (
        street_reference_load_adapter(loader=providers.street_loader)
        if providers.street_loader is not None
        else _unavailable_stage("Street reference")
    )
    market_load = (
        market_price_load_adapter(
            loader=providers.market_loader,
            currency=str(config.market_currency),
        )
        if providers.market_loader is not None
        else _unavailable_stage("market price")
    )

    red_team = recoverable_red_team_adapter(
        blind_red_team_adapter(officer=providers.red_team_officer)
    )
    bridge = chain_stage_adapters(
        capacity_commitment,
        recovery_aware_bridge_adapter(
            evidence_to_assumption_bridge_adapter(analyst=providers.bridge_analyst)
        ),
    )

    return {
        "COMPANY_RESOLUTION": live_company_resolution_adapter(
            resolver=providers.company_resolver,
            request=config.company_request,
        ),
        "LOAD_COMPANY_STATE": state_load,
        "LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT": live_industry_snapshot_adapter(
            loader=providers.industry_snapshot_loader
        ),
        "SOURCE_FRESHNESS_PRECHECK": _freshness_precheck_adapter(
            providers.freshness_loader
        ),
        "SEGMENT_DECOMPOSITION": live_segment_decomposition_adapter(
            decomposer=providers.segment_decomposer
        ),
        "INDUSTRY_DNA_ROUTE": live_industry_dna_route_adapter(
            router=providers.industry_dna_router
        ),
        "MODULE_REQUIREMENT_PLAN": broker_aware_module_requirement_plan_adapter(
            registry_path=config.archetype_registry_path,
            control_requirements_path=config.archetype_control_requirements_path,
            loader=getattr(providers, "broker_research_loader", None),
            require_broker_research=bool(
                getattr(config, "require_broker_research", False)
            ),
            additional_required_evidence=config.additional_required_evidence,
        ),
        "PRIMARY_EVIDENCE_COLLECTION": primary_evidence_collection_adapter(
            selection_loader=_collection_selection_loader(config)
        ),
        "EVIDENCE_LEDGER": evidence_ledger_adapter(),
        "ROCKET_INSIGHT_SCAN": broker_aware_rocket_insight_adapter(
            live_rocket_insight_dispatch_adapter(
                runners=providers.scanner_runners
            ),
            required=bool(getattr(config, "require_broker_research", False)),
        ),
        "UPSTREAM_FUNDING_SCAN": funding,
        "RESEARCHER_A": researcher_a_adapter(
            officer=providers.intelligence_officer
        ),
        "BLIND_RED_TEAM_B": red_team,
        "RESEARCH_LOOP": research_loop_recovery_adapter(
            providers.research_recovery_adapter
        ),
        "EVIDENCE_TO_ASSUMPTION_BRIDGE": bridge,
        "SCENARIO_BUILD": chain_stage_adapters(*scenario_chain),
        "VALUATION_METHOD_INTENT": method_intent,
        "HIERARCHICAL_BETA_ESTIMATION": beta,
        "WACC_VALIDATION": wacc,
        "DETERMINISTIC_VALUATION": valuation,
        "HIERARCHICAL_WARRANTED_PER": per,
        "DCF_PER_ASSUMPTION_CONSISTENCY_GATE": chain_stage_adapters(
            dcf_per_consistency_gate_adapter(),
            capacity_consistency_gate_adapter(),
        ),
        "CROSS_METHOD_DOUBLE_COUNT_AUDIT": cross_method_double_count_adapter(),
        "PROBABILITY_DISTRIBUTION_ANALYSIS": probability_distribution_adapter(),
        "AUDIT_GATE": chain_stage_adapters(
            broker_research_audit_adapter(
                required=bool(getattr(config, "require_broker_research", False))
            ),
            capacity_audit_adapter(),
            # Assumption-plausibility guardrails. Both are non-blocking disclosures
            # whose findings are bound into audit_hash by generic_audit_adapter.
            evidence_composition_audit_adapter(),
            valuation_sensitivity_adapter(),
            generic_audit_adapter(
                impact_config=config.impact_config,
                unit_contract_registry=effective_unit_contract_registry,
            ),
        ),
        "STREET_REFERENCE_LOAD": street_load,
        "STREET_GAP_ANALYZER": street_gap_analyzer_adapter(),
        "MARKET_PRICE_LOAD": market_load,
        # Reverse DCF is a post-freeze market-comparison tool (references/methods/
        # reverse-dcf.md) and shares the MARKET_COMPARE unit contract, which already
        # declares reverse_dcf_context as an owned output.
        "MARKET_COMPARE": chain_stage_adapters(
            market_compare_adapter(),
            reverse_dcf_expectations_adapter(),
        ),
        "THESIS_DELTA": thesis_delta_adapter(),
        "SAVE_STATE": save_state_adapter(
            state_root=state_root,
            learning_store=learning_store,
            probability_history_store=probability_history_store,
        ),
        "FINAL_REPORT": final_report_adapter(),
    }


def run_prism(config: LivePrimaryRuntimeConfig) -> ControlledRunResult:
    """Run the canonical PRISM Control Plane in LIVE_PRIMARY mode.

    LIVE_PRIMARY never falls back to PRIMARY_SHADOW or the legacy OCI workflow. Missing
    route-specific providers remain explicit stage-level capability gaps.
    """
    config.validate()
    sequence = load_stage_sequence(config.stage_registry_path)
    reporting_contract = load_reporting_contract(config.stage_registry_path)
    initial = dict(config.initial_data)
    initial["scenario_binding_spec"] = config.scenario_binding_spec
    initial.setdefault("prior_hypotheses", ())
    initial.setdefault("optional_research_units", ())
    initial.setdefault("research_trigger_state", {})
    initial.setdefault("research_unit_aliases", {})
    unit_contract_registry = load_unit_contract_registry(
        config.unit_contract_registry_path
    )
    result = run_controlled_workflow(
        run_id=config.run_id,
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_sequence=sequence,
        adapters=build_live_primary_adapters(
            config,
            unit_contract_registry=unit_contract_registry,
        ),
        required_stages=sequence,
        initial_data=initial,
        unit_contract_registry=unit_contract_registry,
        reporting_contract=reporting_contract,
        major_gate_reporter=getattr(config, "major_gate_reporter", None),
    )
    if not result.blocked_reasons:
        return finalize_live_primary_run_artifacts(
            result,
            state_root=config.state_root,
            stage_registry_path=config.stage_registry_path,
        )
    return ControlledRunResult(
        run_id=result.run_id,
        execution_mode=result.execution_mode,
        stage_traces=result.stage_traces,
        data={
            key: value
            for key, value in result.data.items()
            if key not in _BLOCKED_RESULT_INTRINSIC_KEYS
        },
        blocked_reasons=result.blocked_reasons,
        freeze_token=None,
        major_gate_summaries=result.major_gate_summaries,
        reporting_warnings=result.reporting_warnings,
    )
