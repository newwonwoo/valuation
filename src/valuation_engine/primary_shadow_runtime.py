from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from .audit_adapter import generic_audit_adapter
from .control_plane import ExecutionMode, StageStatus
from .evidence_adapter import evidence_ledger_adapter, primary_evidence_collection_adapter
from .evidence_collection import EvidenceCollector
from .generic_reporting import final_report_adapter, save_state_adapter, thesis_delta_adapter
from .impact_adapter import GenericDecisionImpactConfig
from .industry_dna import IndustryDNAProfile
from .llm_adapters import (
    blind_red_team_adapter,
    evidence_to_assumption_bridge_adapter,
    researcher_a_adapter,
)
from .llm_staff import BridgeAnalyst, IntelligenceOfficer, RedTeamOfficer
from .method_capabilities import load_method_capability_registry
from .module_plan_adapter import module_requirement_plan_adapter
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
from .valuation_method_intent import valuation_method_intent_adapter
from .valuation_plan_compiler import (
    CompanyValuationPlanInputs,
    SegmentMethodChoice,
    SegmentValueBinding,
    compile_company_valuation_plan,
)


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
    strict_evidence_coverage: bool = True
    optional_research_units: tuple[str, ...] = ()
    research_trigger_state: Mapping[str, bool] = field(default_factory=dict)
    research_unit_aliases: Mapping[str, str] = field(default_factory=dict)
    impact_config: GenericDecisionImpactConfig | None = None
    major_gate_reporter: MajorGateReporter | None = None
    stage_registry_path: str | Path = _REPO_ROOT / "config" / "control_plane_stage_registry.yaml"
    archetype_registry_path: str | Path = _REPO_ROOT / "config" / "archetype_module_registry.yaml"
    control_requirements_path: str | Path = _REPO_ROOT / "config" / "archetype_control_requirements.yaml"
    method_capability_registry_path: str | Path = _REPO_ROOT / "config" / "valuation_method_capability_registry.yaml"
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
        if any(not isinstance(value, str) or not value.strip() for value in text_fields):
            raise ValueError("primary-shadow runtime requires non-empty identity/hash/currency fields")
        if not self.profiles or not self.collectors or not self.selected_methods:
            raise ValueError("primary-shadow runtime requires profiles, collectors and selected methods")
        if len(self.selected_methods) != len(set(self.selected_methods)):
            raise ValueError("selected_methods contains duplicates")
        for profile in self.profiles:
            profile.validate()
        self.scenario_binding_spec.validate()
        self.valuation_plan.validate()
        capability_registry = load_method_capability_registry(
            self.method_capability_registry_path
        )
        capability_registry.validate(
            archetype_registry_path=self.archetype_registry_path,
            repo_root=_REPO_ROOT,
        )
        if not all(isinstance(item, str) and item for item in self.optional_research_units):
            raise ValueError("optional_research_units must contain non-empty strings")
        if not all(isinstance(key, str) and isinstance(value, bool) for key, value in self.research_trigger_state.items()):
            raise ValueError("research_trigger_state must be str→bool")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in self.research_unit_aliases.items()):
            raise ValueError("research_unit_aliases must be str→str")
        if self.major_gate_reporter is not None and not callable(self.major_gate_reporter):
            raise TypeError("major_gate_reporter must be callable")
        unknown_overrides = set(self.stage_overrides).difference(load_stage_sequence(self.stage_registry_path))
        if unknown_overrides:
            raise ValueError(f"stage overrides contain unknown stages: {sorted(unknown_overrides)}")


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


def _chain_stage_adapters(label: str, *adapters: StageAdapter) -> StageAdapter:
    """Compose same-stage adapters while preserving append-only context semantics."""
    if not adapters:
        raise ValueError("chained stage requires at least one adapter")

    def run(context: OrchestratorContext) -> StageExecutionResult:
        working = OrchestratorContext(
            context.run_id,
            context.execution_mode,
            dict(context.data),
            list(context.stage_traces),
            context.freeze_token,
        )
        merged: dict[str, object] = {}
        rationales: list[str] = []
        overall = StageStatus.PASS
        unresolved = {
            StageStatus.BLOCKED,
            StageStatus.NOT_IMPLEMENTED,
            StageStatus.RECOVERY_REQUIRED,
            StageStatus.AWAITING_USER_DECISION,
        }
        for adapter in adapters:
            result = adapter(working)
            overlap = set(result.outputs).intersection(working.data)
            if overlap:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    f"{label} attempted duplicate output keys: {sorted(overlap)}",
                    merged,
                    blocking=True,
                )
            working.data.update(result.outputs)
            merged.update(result.outputs)
            rationales.append(result.rationale)
            if result.status in unresolved and result.blocking:
                return StageExecutionResult(
                    result.status,
                    f"{label}: " + " | ".join(rationales),
                    merged,
                    blocking=True,
                )
            if result.status is StageStatus.WARNING:
                overall = StageStatus.WARNING
            elif result.status is StageStatus.RECOVERED and overall is StageStatus.PASS:
                overall = StageStatus.RECOVERED
        return StageExecutionResult(overall, f"{label}: " + " | ".join(rationales), merged)

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
        if not isinstance(scanners, tuple) or not all(isinstance(item, str) for item in scanners):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "Module Requirement Plan did not produce a typed scanner loadout",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.WARNING,
            "mandatory scanner loadout was inspected in shadow mode; Researcher A remains responsible for typed hypotheses",
            {
                "rocket_insight_scanner_loadout": scanners,
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


MethodStageApplicability = Callable[
    [OrchestratorContext, tuple[SegmentMethodChoice, ...]],
    bool,
]


def _intent_method_stage_adapter(
    *,
    stage_name: str,
    applicability: MethodStageApplicability,
    not_applicable_reason: str,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        choices = context.data.get("planned_method_choices")
        if not isinstance(choices, tuple) or not all(
            isinstance(item, SegmentMethodChoice) for item in choices
        ):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                f"{stage_name} requires resolved pre-risk method intent",
                blocking=True,
            )
        try:
            applicable = applicability(context, choices)
        except (KeyError, TypeError, ValueError) as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"{stage_name} method-intent applicability failed: {exc}",
                blocking=True,
            )
        if not isinstance(applicable, bool):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"{stage_name} applicability must be boolean",
                blocking=True,
            )
        if not applicable:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                not_applicable_reason,
                {f"{stage_name.lower()}_state": "NOT_APPLICABLE"},
            )
        selected_methods = tuple(
            f"{item.archetype}/{item.method}"
            + (f"/{item.version}" if item.version is not None else "")
            for item in choices
        )
        return StageExecutionResult(
            StageStatus.NOT_IMPLEMENTED,
            f"{stage_name} is required by resolved method intent but no "
            "runtime adapter was supplied: " + ", ".join(selected_methods),
            {f"{stage_name.lower()}_state": "NOT_IMPLEMENTED"},
            blocking=True,
        )

    return run


def _intent_bool(
    context: OrchestratorContext,
    key: str,
) -> bool:
    value = context.data[key]
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be boolean")
    return value


def _warranted_per_applicable(
    context: OrchestratorContext,
    _: tuple[SegmentMethodChoice, ...],
) -> bool:
    segments = context.data["warranted_per_segments"]
    if not isinstance(segments, tuple) or not all(
        isinstance(item, str) and item for item in segments
    ):
        raise TypeError("warranted_per_segments must be a string tuple")
    return bool(segments)


def _dcf_per_consistency_applicable(
    context: OrchestratorContext,
    choices: tuple[SegmentMethodChoice, ...],
) -> bool:
    return _warranted_per_applicable(context, choices) and any(
        token in choice.method
        for choice in choices
        for token in _DCF_LIKE_TOKENS
    )


def _configured_method_choices(
    plan: CompanyValuationPlan,
) -> tuple[SegmentMethodChoice, ...]:
    return tuple(
        SegmentMethodChoice(
            segment_id=item.segment_id,
            archetype=item.model_key.archetype,
            method=item.model_key.method,
            version=item.model_key.version,
        )
        for item in plan.segments
    )


def _valuation_plan_inputs(
    plan: CompanyValuationPlan,
) -> CompanyValuationPlanInputs:
    return CompanyValuationPlanInputs(
        reporting_unit=plan.reporting_unit,
        diluted_shares_key=plan.diluted_shares_key,
        segment_bindings=tuple(
            SegmentValueBinding(
                segment_id=item.segment_id,
                asset_id=item.asset_id,
                ownership_key=item.ownership_key,
                ev_to_equity_adjustment_key=(
                    item.ev_to_equity_adjustment_key
                ),
            )
            for item in plan.segments
        ),
        parent_adjustments=plan.parent_adjustments,
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
                {
                    "probability_distribution_state": "WITHHELD_UNCALIBRATED",
                    "probability_calibration_dataset_hash": None,
                    "probability_calibration_snapshot_hash": None,
                },
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "calibrated scenario probabilities are authorized for deterministic expected-value aggregation",
            {
                "probability_distribution_state": "CALIBRATED",
                "probability_calibration_dataset_hash": scenario_set.calibration_dataset_hash,
                "probability_calibration_snapshot_hash": scenario_set.calibration_snapshot_hash,
            },
        )

    return run


def build_primary_shadow_adapters(config: PrimaryShadowRuntimeConfig) -> dict[str, StageAdapter]:
    config.validate()
    capability_registry = load_method_capability_registry(
        config.method_capability_registry_path
    )
    configured_choices = _configured_method_choices(config.valuation_plan)
    plan_inputs = _valuation_plan_inputs(config.valuation_plan)
    learning_store = ResearchLearningStore(config.state_root)

    def valuation_plan_loader(context, evaluator_registry):
        return compile_company_valuation_plan(
            context.data["module_requirement_plan"],
            context.data["bound_scenario_set"],
            evaluator_registry=evaluator_registry,
            capability_registry=capability_registry,
            inputs=plan_inputs,
            method_choices=context.data["planned_method_choices"],
        )

    adapters: dict[str, StageAdapter] = {
        "COMPANY_RESOLUTION": company_resolution_adapter(company=config.company, ticker=config.ticker),
        "LOAD_COMPANY_STATE": _chain_stage_adapters(
            "company state and prior research-learning load",
            load_company_state_adapter(state_root=config.state_root),
            load_research_learning_adapter(store=learning_store),
        ),
        "LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT": _industry_snapshot_adapter(config.industry_snapshot_hash),
        "SOURCE_FRESHNESS_PRECHECK": _source_freshness_adapter(),
        "SEGMENT_DECOMPOSITION": _segment_decomposition_adapter(config.profiles),
        "INDUSTRY_DNA_ROUTE": industry_dna_adapter(profiles=config.profiles),
        "MODULE_REQUIREMENT_PLAN": module_requirement_plan_adapter(
            registry_path=config.archetype_registry_path,
            control_requirements_path=config.control_requirements_path,
        ),
        "PRIMARY_EVIDENCE_COLLECTION": primary_evidence_collection_adapter(
            collectors=config.collectors,
            strict_required_coverage=config.strict_evidence_coverage,
        ),
        "EVIDENCE_LEDGER": evidence_ledger_adapter(),
        "ROCKET_INSIGHT_SCAN": _rocket_insight_adapter(),
        "UPSTREAM_FUNDING_SCAN": _upstream_funding_adapter(),
        "RESEARCHER_A": researcher_a_adapter(officer=config.intelligence_officer),
        "BLIND_RED_TEAM_B": blind_red_team_adapter(officer=config.red_team_officer),
        "RESEARCH_LOOP": _research_loop_adapter(),
        "EVIDENCE_TO_ASSUMPTION_BRIDGE": evidence_to_assumption_bridge_adapter(analyst=config.bridge_analyst),
        "SCENARIO_BUILD": scenario_build_adapter(),
        "VALUATION_METHOD_INTENT": valuation_method_intent_adapter(
            capability_registry=capability_registry,
            method_choices=configured_choices,
        ),
        "HIERARCHICAL_BETA_ESTIMATION": _intent_method_stage_adapter(
            stage_name="HIERARCHICAL_BETA_ESTIMATION",
            applicability=lambda context, choices: _intent_bool(
                context,
                "risk_chain_requires_beta",
            ),
            not_applicable_reason="selected exact normalized-multiple evaluator does not consume Beta",
        ),
        "WACC_VALIDATION": _intent_method_stage_adapter(
            stage_name="WACC_VALIDATION",
            applicability=lambda context, choices: _intent_bool(
                context,
                "risk_chain_requires_wacc",
            ),
            not_applicable_reason="selected exact normalized-multiple evaluator does not consume WACC",
        ),
        "DETERMINISTIC_VALUATION": deterministic_valuation_adapter(
            registry=config.evaluator_registry,
            plan_loader=valuation_plan_loader,
        ),
        "HIERARCHICAL_WARRANTED_PER": _intent_method_stage_adapter(
            stage_name="HIERARCHICAL_WARRANTED_PER",
            applicability=_warranted_per_applicable,
            not_applicable_reason="Warranted PER is not one of the selected valuation methods",
        ),
        "DCF_PER_ASSUMPTION_CONSISTENCY_GATE": _intent_method_stage_adapter(
            stage_name="DCF_PER_ASSUMPTION_CONSISTENCY_GATE",
            applicability=_dcf_per_consistency_applicable,
            not_applicable_reason="both DCF-like and Warranted PER outputs are not present in this run",
        ),
        "CROSS_METHOD_DOUBLE_COUNT_AUDIT": _cross_method_double_count_adapter(),
        "PROBABILITY_DISTRIBUTION_ANALYSIS": _probability_distribution_adapter(),
        "AUDIT_GATE": generic_audit_adapter(impact_config=config.impact_config),
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
            learning_store=learning_store,
        ),
        "FINAL_REPORT": final_report_adapter(),
    }
    adapters.update(dict(config.stage_overrides))
    return adapters


def run_primary_shadow(config: PrimaryShadowRuntimeConfig) -> ControlledRunResult:
    config.validate()
    sequence = load_stage_sequence(config.stage_registry_path)
    reporting_contract = load_reporting_contract(config.stage_registry_path)
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
            "optional_research_units": config.optional_research_units,
            "research_trigger_state": dict(config.research_trigger_state),
            "research_unit_aliases": dict(config.research_unit_aliases),
        },
        reporting_contract=reporting_contract,
        major_gate_reporter=config.major_gate_reporter,
    )
