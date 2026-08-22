from __future__ import annotations

from pathlib import Path

from .control_plane import StageStatus
from .industry_dna import IndustryDNAProfile, compose_modules
from .module_plan import build_module_requirement_plan
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .state import StateStore


def company_resolution_adapter(*, company: str, ticker: str) -> StageAdapter:
    if not company.strip() or not ticker.strip():
        raise ValueError("company and ticker are required")

    def run(_: OrchestratorContext) -> StageExecutionResult:
        return StageExecutionResult(
            StageStatus.PASS,
            "company identity supplied by the caller and resolved for shadow execution",
            {"company": company.strip(), "ticker": ticker.strip()},
        )

    return run


def load_company_state_adapter(*, state_root: str | Path) -> StageAdapter:
    store = StateStore(state_root)

    def run(context: OrchestratorContext) -> StageExecutionResult:
        ticker = context.data.get("ticker")
        if not isinstance(ticker, str) or not ticker:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "ticker is missing; company resolution must complete first",
                blocking=True,
            )
        state = store.load_current(ticker)
        return StageExecutionResult(
            StageStatus.PASS,
            "prior company state loaded" if state is not None else "no prior company state; first-run empty state is valid",
            {"company_state": state or {}},
        )

    return run


def industry_dna_adapter(*, profiles: tuple[IndustryDNAProfile, ...]) -> StageAdapter:
    if not profiles:
        raise ValueError("Industry DNA adapter requires at least one profile")
    for profile in profiles:
        profile.validate()

    def run(_: OrchestratorContext) -> StageExecutionResult:
        compositions = tuple(compose_modules(profile) for profile in profiles)
        return StageExecutionResult(
            StageStatus.PASS,
            "evidence-backed Industry DNA profiles validated and module compositions derived",
            {
                "industry_dna_profiles": profiles,
                "module_compositions": compositions,
            },
        )

    return run


def module_requirement_plan_adapter(*, registry_path: str | Path) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        profiles = context.data.get("industry_dna_profiles")
        if not isinstance(profiles, tuple) or not profiles or not all(
            isinstance(item, IndustryDNAProfile) for item in profiles
        ):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "Industry DNA profiles are missing or invalid",
                blocking=True,
            )
        plan = build_module_requirement_plan(profiles, registry_path=registry_path)
        expected_modules = tuple(
            dict.fromkeys(
                (*plan.common_core_modules, *(archetype for segment in plan.segments for archetype in segment.archetypes))
            )
        )
        return StageExecutionResult(
            StageStatus.PASS,
            "Industry DNA compiled into required evidence, scenario, risk and valuation-method contracts",
            {
                "module_requirement_plan": plan,
                "required_evidence": plan.required_evidence,
                "scenario_variables": plan.scenario_variables,
                "expected_module_ids": expected_modules,
            },
        )

    return run
