from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from .audit_adapter import generic_audit_adapter
from .collection_plan import (
    CollectorCapability,
    CollectionTask,
    CompanyCollectionPlan,
    compile_company_collection_plan,
)
from .control_plane import ExecutionMode, StageStatus
from .dcf_evaluators import RegistryLoader
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
from .generic_reporting import final_report_adapter, save_state_adapter, thesis_delta_adapter
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
from .module_plan_adapter import module_requirement_plan_adapter
from .orchestrator import (
    ControlledRunResult,
    OrchestratorContext,
    StageAdapter,
    StageExecutionResult,
    load_stage_sequence,
    run_controlled_workflow,
)
from .per_adapters import PERInputsLoader, live_hierarchical_warranted_per_adapter
from .post_freeze_adapters import (
    MarketLoader,
    StreetLoader,
    market_compare_adapter,
    market_price_load_adapter,
    street_gap_analyzer_adapter,
    street_reference_load_adapter,
)
from .probability_adapter import (
    CalibrationSnapshotLoader,
    probability_calibration_load_adapter,
)
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
from .unit_contracts import load_unit_contract_registry
from .valuation_adapter import deterministic_valuation_adapter
from .valuation_method_intent import valuation_method_intent_adapter
from .valuation_plan_compiler import (
    CompanyValuationPlanInputs,
    SegmentMethodChoice,
    compile_company_valuation_plan,
)


ValuationPlanInputsLoader = Callable[[OrchestratorContext], CompanyValuationPlanInputs]


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
    market_currency: str | None = None
    stage_registry_path: str | Path = "config/control_plane_stage_registry.yaml"
    archetype_registry_path: str | Path = "config/archetype_module_registry.yaml"
    archetype_control_requirements_path: str | Path = (
        "config/archetype_control_requirements.yaml"
    )
    industry_source_registry_path: str | Path = "config/industry_source_registry.yaml"
    unit_contract_registry_path: str | Path = "config/unit_contract_registry.yaml"
    capability_registry: MethodCapabilityRegistry | None = None
    impact_config: GenericDecisionImpactConfig | None = None
    initial_data: Mapping[str, object] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.run_id:
            raise ValueError("LIVE_PRIMARY run_id is required")
        self.company_request.validate()
        self.scenario_binding_spec.validate()
        self.providers.validate()
        if self.providers.market_loader is not None and not self.market_currency:
            raise ValueError("LIVE_PRIMARY market_loader requires market_currency")
        prohibited = {
            "current_market_price",
            "market_price",
            "market_observation",
            "target_price",
            "target_multiple",
            "street_reference",
            "street_reports",
        }.intersection(self.initial_data)
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
            target_is_listed=bool(identity.ticker),
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
        return compile_company_valuation_plan(
            module_plan,
            scenario_set,
            evaluator_registry=evaluator_registry,
            capability_registry=capability_registry,
            inputs=inputs,
            method_choices=intent.method_choices(),
        )

    return load


def build_live_primary_adapters(
    config: LivePrimaryRuntimeConfig,
) -> dict[str, StageAdapter]:
    config.validate()
    providers = config.providers
    capability_registry = (
        config.capability_registry or load_default_method_capability_registry()
    )
    state_root = Path(config.state_root)
    learning_store = ResearchLearningStore(state_root)
    unit_contract_registry = load_unit_contract_registry(
        config.unit_contract_registry_path
    )

    state_load = chain_stage_adapters(
        load_company_state_adapter(state_root=state_root),
        load_research_learning_adapter(store=learning_store),
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

    scenario_chain: list[StageAdapter] = []
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
    scenario_chain.extend(
        (
            scenario_build_adapter(),
            valuation_method_intent_adapter(
                capability_registry=capability_registry,
                method_choices=config.method_choices,
            ),
        )
    )

    valuation = chain_stage_adapters(
        deterministic_valuation_adapter(
            registry_loader=providers.evaluator_registry_loader,
            plan_loader=_valuation_plan_loader(config, capability_registry),
        ),
        dcf_consistency_fingerprint_adapter(providers.dcf_fingerprint_loader),
    )

    per = conditional_warranted_per_adapter(
        live_hierarchical_warranted_per_adapter(loader=providers.per_loader)
        if providers.per_loader is not None
        else None
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
    bridge = recovery_aware_bridge_adapter(
        evidence_to_assumption_bridge_adapter(analyst=providers.bridge_analyst)
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
        "SOURCE_FRESHNESS_PRECHECK": live_source_freshness_adapter(
            loader=providers.freshness_loader
        ),
        "SEGMENT_DECOMPOSITION": live_segment_decomposition_adapter(
            decomposer=providers.segment_decomposer
        ),
        "INDUSTRY_DNA_ROUTE": live_industry_dna_route_adapter(
            router=providers.industry_dna_router
        ),
        "MODULE_REQUIREMENT_PLAN": module_requirement_plan_adapter(
            registry_path=config.archetype_registry_path,
            control_requirements_path=config.archetype_control_requirements_path,
        ),
        "PRIMARY_EVIDENCE_COLLECTION": primary_evidence_collection_adapter(
            selection_loader=_collection_selection_loader(config)
        ),
        "EVIDENCE_LEDGER": evidence_ledger_adapter(),
        "ROCKET_INSIGHT_SCAN": live_rocket_insight_dispatch_adapter(
            runners=providers.scanner_runners
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
        "HIERARCHICAL_BETA_ESTIMATION": beta,
        "WACC_VALIDATION": wacc,
        "DETERMINISTIC_VALUATION": valuation,
        "HIERARCHICAL_WARRANTED_PER": per,
        "DCF_PER_ASSUMPTION_CONSISTENCY_GATE": (
            dcf_per_consistency_gate_adapter()
        ),
        "CROSS_METHOD_DOUBLE_COUNT_AUDIT": cross_method_double_count_adapter(),
        "PROBABILITY_DISTRIBUTION_ANALYSIS": probability_distribution_adapter(),
        "AUDIT_GATE": generic_audit_adapter(
            impact_config=config.impact_config,
            unit_contract_registry=unit_contract_registry,
        ),
        "STREET_REFERENCE_LOAD": street_load,
        "STREET_GAP_ANALYZER": street_gap_analyzer_adapter(),
        "MARKET_PRICE_LOAD": market_load,
        "MARKET_COMPARE": market_compare_adapter(),
        "THESIS_DELTA": thesis_delta_adapter(),
        "SAVE_STATE": save_state_adapter(
            state_root=state_root,
            learning_store=learning_store,
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
    initial = dict(config.initial_data)
    initial["scenario_binding_spec"] = config.scenario_binding_spec
    initial.setdefault("prior_hypotheses", ())
    initial.setdefault("optional_research_units", ())
    initial.setdefault("research_trigger_state", {})
    initial.setdefault("research_unit_aliases", {})
    return run_controlled_workflow(
        run_id=config.run_id,
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_sequence=sequence,
        adapters=build_live_primary_adapters(config),
        required_stages=sequence,
        initial_data=initial,
    )
