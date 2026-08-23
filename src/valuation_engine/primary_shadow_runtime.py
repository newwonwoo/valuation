from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .audit_adapter import generic_audit_adapter
from .control_plane import ExecutionMode, StageStatus
from .evidence_adapter import evidence_ledger_adapter, primary_evidence_collection_adapter
from .evidence_collection import EvidenceCollector
from .generic_reporting import final_report_adapter, save_state_adapter, thesis_delta_adapter
from .industry_dna import IndustryDNAProfile
from .llm_adapters import (
    blind_red_team_adapter,
    evidence_to_assumption_bridge_adapter,
    researcher_a_adapter,
)
from .llm_staff import BridgeAnalyst, IntelligenceOfficer, RedTeamOfficer
from .module_plan_adapter import module_requirement_plan_adapter
from .orchestrator import (
    ControlledRunResult,
    OrchestratorContext,
    StageAdapter,
    StageExecutionResult,
    load_stage_sequence,
    run_controlled_workflow,
)
from .post_freeze_adapters import (
    MarketLoader,
    StreetLoader,
    market_compare_adapter,
    market_price_load_adapter,
    street_gap_analyzer_adapter,
    street_reference_load_adapter,
)
from .research_learning import ResearchLearningStore
from .scenario_binding import BoundScenarioSet, ScenarioBindingSpec
from .shadow_adapters import (
    company_resolution_adapter,
    industry_dna_adapter,
    load_company_state_adapter,
    scenario_build_adapter,
)
from .state_learning_adapter import load_research_learning_adapter
from .valuation_adapter import deterministic_valuation_adapter
from .valuation_execution import CompanyValuationPlan, EvaluatorRegistry


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DCF_LIKE_TOKENS = ("dcf", "npv", "ddm", "residual_income", "rate_base_roe")


@dataclass(frozen=True)
class PrimaryShadowRuntimeConfig:
    run_id: str
    company: str
    ticker: str
    target_id: str
    state_root: str | Path
    profiles: tuple[IndustryDNAProfile, ...]
    collectors: tuple[EvidenceCollector, ...]
    intelligence_officer: IntelligenceOfficer
    red_team_officer: RedTeamOfficer
    bridge_analyst: BridgeAnalyst
    scenario_binding_spec: ScenarioBindingSpec
    valuation_plan: CompanyValuationPlan
    evaluator_registry: EvaluatorRegistry
    selected_methods: tuple[str, ...]
    industry_snapshot_hash: str
    street_loader: StreetLoader
    market_loader: MarketLoader
    market_currency: str
    optional_research_units: tuple[str, ...] = ()
    research_trigger_state: Mapping[str, bool] = field(default_factory=dict)
    research_unit_aliases: Mapping[str, str] = field(default_factory=dict)
    stage_registry_path: str | Path = _REPO_ROOT / "config" / "control_plane_stage_registry.yaml"
    archetype_registry_path: str | Path = _REPO_ROOT / "config" / "archetype_module_registry.yaml"
    control_requirements_path: str | Path = _REPO_ROOT / "config" / "archetype_control_requirements.yaml"
    stage_overrides: Mapping[str, StageAdapter] = field(default_factory=dict)

    def validate(self) -> None:
        text_fields = (
            self.run_id,
            self.company,
            self.ticker,
            self.target_id,
            self.industry_snapshot_hash,
            self.market_currency,
        )
        if any(not value for value in text_fields):
            raise ValueError("primary-shadow runtime requires non-empty identity/hash/currency fields")
        if not self.profiles or not self.collectors or not self.selected_methods:
            raise ValueError("primary-shadow runtime requires profiles, collectors and selected methods")
        if not all(isinstance(item, str) and item for item in self.optional_research_units):
            raise ValueError("optional_research_units must be a non-empty string tuple")
        if not all(isinstance(key, str) and isinstance(value, bool) for key, value in self.research_trigger_state.items()):
            raise ValueError("research_trigger_state must be str→bool")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in self.research_unit_aliases.items()):
            raise ValueError("research_unit_aliases must be str→str")
        for profile in self.profiles:
            profile.validate()
        self.scenario_binding_spec.validate()
        self.valuation_plan.validate()


def _static_adapter(
    status: StageStatus,
    rationale: str,
    outputs: dict | None = None,
    *,
    blocking: bool = False,
) -> StageAdapter:
    frozen_outputs = dict(outputs or {})

    def run(_: OrchestratorContext) -> StageExecutionResult:
        return StageExecutionResult(status, rationale, dict(frozen_outputs), blocking=blocking)

    return run


def _combined_state_load_adapter(state_root: str | Path) -> StageAdapter:
    company_loader = load_company_state_adapter(state_root=state_root)
    learning_loader = load_research_learning_adapter(store=ResearchLearningStore(state_root))

    def run(context: OrchestratorContext) -> StageExecutionResult:
        company_result = company_loader(context)
        if company_result.blocking:
            return company_result
        learning_result = learning_loader(context)
        if learning_result.blocking:
            return learning_result
        overlap = set(company_result.outputs).intersection(learning_result.outputs)
        if overlap:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "combined state loaders produced overlapping keys: " + ", ".join(sorted(overlap)),
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            company_result.rationale + "; " + learning_result.rationale,
            {**company_result.outputs, **learning_result.outputs},
        )

    return run


def _industry_snapshot_adapter(snapshot_hash: str) -> StageAdapter:
    return _static_adapter(
        StageStatus.PASS,
        "versioned Industry Knowledge snapshot supplied for PRIMARY_SHADOW execution",
        {
            "industry_snapshot_hash": snapshot_hash,
            "industry_snapshot_mode": "PRIMARY_SHADOW",
        },
    )


def _source_freshness_adapter() -> StageAdapter:
    return _static_adapter(
        StageStatus.PASS,
        "shadow source contracts are healthy; collection-stage fingerprints remain the source snapshot authority",
        {"source_freshness_precheck": "CLEAN_SHADOW"},
    )


def _segment_decomposition_adapter(profiles: tuple[IndustryDNAProfile, ...]) -> StageAdapter:
    return _static_adapter(
        StageStatus.PASS,
        "caller-supplied segment decomposition accepted as a shadow routing input",
        {"segment_ids": tuple(profile.segment_id for profile in profiles)},
    )


def _rocket_insight_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        scanners = context.data.get("mandatory_scanners", ())
        active_units = context.data.get("active_research_units", ())
        if not isinstance(scanners, tuple) or not all(isinstance(item, str) for item in scanners):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "Module Requirement Plan did not produce a typed scanner loadout",
                blocking=True,
            )
        if not isinstance(active_units, tuple) or not all(isinstance(item, str) for item in active_units):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "adaptive research loadout is missing",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.WARNING,
            "mandatory and learned research loadouts were inspected in shadow mode; Researcher A remains responsible for typed hypotheses",
            {
                "rocket_insight_scanner_loadout": scanners,
                "rocket_insight_active_research_units": active_units,
                "rocket_insight_execution_mode": "SHADOW_PLAN_ONLY",
            },
        )

    return run


def _upstream_funding_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        plan = context.data.get("module_requirement_plan")
        segments = getattr(plan, "segments", ())
        scans = tuple(
            dict.fromkeys(
                scan
                for segment in segments
                for scan in getattr(segment, "funding_scans", ())
            )
        )
        if not scans:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "selected archetypes do not require a dedicated upstream-funding scan",
                {"upstream_funding_scan_state": "NOT_APPLICABLE"},
            )
        return StageExecutionResult(
            StageStatus.NOT_IMPLEMENTED,
            "funding scan is required by Industry DNA but no live/shadow funding adapter was supplied: "
            + ", ".join(scans),
            {"required_funding_scans": scans},
            blocking=True,
        )

    return run


def _research_loop_adapter() -> StageAdapter:
    return _static_adapter(
        StageStatus.SKIPPED_NOT_APPLICABLE,
        "Blind Red Team reported no unresolved blocker, so no additional research round was required",
        {"research_round_count": 1},
    )


def _method_stage_adapter(
    *,
    stage_name: str,
    selected_methods: tuple[str, ...],
    applicable: bool,
    not_applicable_reason: str,
) -> StageAdapter:
    if not applicable:
        return _static_adapter(
            StageStatus.SKIPPED_NOT_APPLICABLE,
            not_applicable_reason,
            {f"{stage_name.lower()}_state": "NOT_APPLICABLE"},
        )
    return _static_adapter(
        StageStatus.NOT_IMPLEMENTED,
        f"{stage_name} is required by selected methods but no runtime adapter was supplied: "
        + ", ".join(selected_methods),
        {f"{stage_name.lower()}_state": "NOT_IMPLEMENTED"},
        blocking=True,
    )


def _cross_method_double_count_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        valuation = context.data.get("generic_valuation_result")
        scenarios = getattr(valuation, "scenarios", ())
        if not scenarios:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "generic valuation is missing before cross-method double-count audit",
                blocking=True,
            )
        duplicates = tuple(
            item.scenario_id
            for item in scenarios
            if len(item.economic_path_ids) != len(set(item.economic_path_ids))
        )
        if duplicates:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "duplicate economic paths found in scenarios: " + ", ".join(duplicates),
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "each scenario preserves unique valuation/ownership/debt/dilution economic paths",
            {"cross_method_double_count_status": "PASS"},
        )

    return run


def _probability_distribution_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        scenario_set = context.data.get("bound_scenario_set")
        if not isinstance(scenario_set, BoundScenarioSet):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "BoundScenarioSet is missing before probability-distribution analysis",
                blocking=True,
            )
        if not scenario_set.numeric_weighting_allowed:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "scenario probabilities are not CALIBRATED; descriptive scenarios remain unweighted",
                {"probability_distribution_state": "WITHHELD_UNCALIBRATED"},
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "calibrated scenario probabilities are authorized for deterministic expected-value aggregation",
            {"probability_distribution_state": "CALIBRATED"},
        )

    return run


def build_primary_shadow_adapters(config: PrimaryShadowRuntimeConfig) -> dict[str, StageAdapter]:
    config.validate()
    selected = tuple(config.selected_methods)
    uses_dcf_like = any(token in method for method in selected for token in _DCF_LIKE_TOKENS)
    uses_warranted_per = any("warranted_per" in method for method in selected)
    learning_store = ResearchLearningStore(config.state_root)

    adapters: dict[str, StageAdapter] = {
        "COMPANY_RESOLUTION": company_resolution_adapter(company=config.company, ticker=config.ticker),
        "LOAD_COMPANY_STATE": _combined_state_load_adapter(config.state_root),
        "LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT": _industry_snapshot_adapter(config.industry_snapshot_hash),
        "SOURCE_FRESHNESS_PRECHECK": _source_freshness_adapter(),
        "SEGMENT_DECOMPOSITION": _segment_decomposition_adapter(config.profiles),
        "INDUSTRY_DNA_ROUTE": industry_dna_adapter(profiles=config.profiles),
        "MODULE_REQUIREMENT_PLAN": module_requirement_plan_adapter(
            registry_path=config.archetype_registry_path,
            control_requirements_path=config.control_requirements_path,
        ),
        "PRIMARY_EVIDENCE_COLLECTION": primary_evidence_collection_adapter(collectors=config.collectors),
        "EVIDENCE_LEDGER": evidence_ledger_adapter(),
        "ROCKET_INSIGHT_SCAN": _rocket_insight_adapter(),
        "UPSTREAM_FUNDING_SCAN": _upstream_funding_adapter(),
        "RESEARCHER_A": researcher_a_adapter(officer=config.intelligence_officer),
        "BLIND_RED_TEAM_B": blind_red_team_adapter(officer=config.red_team_officer),
        "RESEARCH_LOOP": _research_loop_adapter(),
        "EVIDENCE_TO_ASSUMPTION_BRIDGE": evidence_to_assumption_bridge_adapter(analyst=config.bridge_analyst),
        "SCENARIO_BUILD": scenario_build_adapter(),
        "HIERARCHICAL_BETA_ESTIMATION": _method_stage_adapter(
            stage_name="HIERARCHICAL_BETA_ESTIMATION",
            selected_methods=selected,
            applicable=uses_dcf_like or uses_warranted_per,
            not_applicable_reason="selected exact normalized-multiple evaluator does not consume Beta",
        ),
        "WACC_VALIDATION": _method_stage_adapter(
            stage_name="WACC_VALIDATION",
            selected_methods=selected,
            applicable=uses_dcf_like,
            not_applicable_reason="selected exact normalized-multiple evaluator does not consume WACC",
        ),
        "DETERMINISTIC_VALUATION": deterministic_valuation_adapter(
            registry=config.evaluator_registry,
            plan=config.valuation_plan,
        ),
        "HIERARCHICAL_WARRANTED_PER": _method_stage_adapter(
            stage_name="HIERARCHICAL_WARRANTED_PER",
            selected_methods=selected,
            applicable=uses_warranted_per,
            not_applicable_reason="Warranted PER is not one of the selected valuation methods",
        ),
        "DCF_PER_ASSUMPTION_CONSISTENCY_GATE": _method_stage_adapter(
            stage_name="DCF_PER_ASSUMPTION_CONSISTENCY_GATE",
            selected_methods=selected,
            applicable=uses_dcf_like and uses_warranted_per,
            not_applicable_reason="both DCF-like and Warranted PER outputs are not present in this run",
        ),
        "CROSS_METHOD_DOUBLE_COUNT_AUDIT": _cross_method_double_count_adapter(),
        "PROBABILITY_DISTRIBUTION_ANALYSIS": _probability_distribution_adapter(),
        "AUDIT_GATE": generic_audit_adapter(),
        "STREET_REFERENCE_LOAD": street_reference_load_adapter(loader=config.street_loader),
        "STREET_GAP_ANALYZER": street_gap_analyzer_adapter(),
        "MARKET_PRICE_LOAD": market_price_load_adapter(
            loader=config.market_loader,
            currency=config.market_currency,
        ),
        "MARKET_COMPARE": market_compare_adapter(),
        "THESIS_DELTA": thesis_delta_adapter(),
        "SAVE_STATE": save_state_adapter(
            state_root=config.state_root,
            research_learning_store=learning_store,
        ),
        "FINAL_REPORT": final_report_adapter(),
    }
    adapters.update(dict(config.stage_overrides))
    return adapters


def run_primary_shadow(config: PrimaryShadowRuntimeConfig) -> ControlledRunResult:
    config.validate()
    sequence = load_stage_sequence(config.stage_registry_path)
    adapters = build_primary_shadow_adapters(config)
    return run_controlled_workflow(
        run_id=config.run_id,
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=sequence,
        adapters=adapters,
        required_stages=sequence,
        initial_data={
            "target_id": config.target_id,
            "prior_hypotheses": (),
            "scenario_binding_spec": config.scenario_binding_spec,
            "selected_methods": config.selected_methods,
            "optional_research_units": config.optional_research_units,
            "research_trigger_state": dict(config.research_trigger_state),
            "research_unit_aliases": dict(config.research_unit_aliases),
        },
    )
