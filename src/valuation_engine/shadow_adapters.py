from __future__ import annotations

from pathlib import Path

from .assumption_compiler import AssumptionSpec, compile_assumptions
from .control_plane import ExecutionMode, StageStatus
from .industry_dna import IndustryDNAProfile, compose_modules
from .ledger import EvidenceLedger
from .module_plan import build_module_requirement_plan
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .probability_adapter import (
    EXTERNAL_PROBABILITY_SNAPSHOT_CONTRACTS,
    EXTERNAL_PROBABILITY_SNAPSHOT_KEYS,
)
from .probability_calibration import CalibrationCertificate
from .records import BridgeRecord, HypothesisRecord
from .scenario_binding import (
    ScenarioBindingSpec,
    bind_external_calibrated_probabilities,
    bind_scenarios,
)
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
            "prior company state loaded"
            if state is not None
            else "no prior company state; first-run empty state is valid",
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


def module_requirement_plan_adapter(
    *,
    registry_path: str | Path,
    control_requirements_path: str | Path,
) -> StageAdapter:
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
        plan = build_module_requirement_plan(
            profiles,
            registry_path=registry_path,
            control_requirements_path=control_requirements_path,
        )
        expected_modules = tuple(
            dict.fromkeys(
                (
                    *plan.common_core_modules,
                    *(
                        archetype
                        for segment in plan.segments
                        for archetype in segment.archetypes
                    ),
                )
            )
        )
        return StageExecutionResult(
            StageStatus.PASS,
            "Industry DNA compiled into evidence, KPI, scanner, kill-condition, scenario and valuation-method contracts",
            {
                "module_requirement_plan": plan,
                "required_evidence": plan.required_evidence,
                "required_kpis": plan.required_kpis,
                "mandatory_scanners": plan.mandatory_scanners,
                "kill_conditions": plan.kill_conditions,
                "scenario_variables": plan.scenario_variables,
                "expected_module_ids": expected_modules,
            },
        )

    return run


def scenario_build_adapter() -> StageAdapter:
    """Compile Bridge proposals and bind scenarios in canonical SCENARIO_BUILD.

    Probability arithmetic remains deterministic. LIVE_PRIMARY may bind either a
    calibrated probability assumption path or a frozen external probability
    snapshot — continuous financial-path or binary-event — but never both. The
    LLM remains proposal-only in either route.
    """

    def run(context: OrchestratorContext) -> StageExecutionResult:
        target_id = context.data.get("target_id")
        ledger = context.data.get("evidence_ledger")
        hypotheses = context.data.get("hypotheses")
        bridges = context.data.get("bridges")
        specs = context.data.get("assumption_specs")
        bridge_input_map = context.data.get("bridge_input_map", {})
        binding_spec = context.data.get("scenario_binding_spec")
        calibration_certificate = context.data.get(
            "probability_calibration_certificate"
        )
        external_snapshot = next(
            (
                context.data[key]
                for _, key in EXTERNAL_PROBABILITY_SNAPSHOT_KEYS
                if context.data.get(key) is not None
            ),
            None,
        )

        if not isinstance(target_id, str) or not target_id:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "target_id missing for compilation",
                blocking=True,
            )
        if not isinstance(ledger, EvidenceLedger):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "EvidenceLedger missing for compilation",
                blocking=True,
            )
        if not isinstance(hypotheses, tuple) or not all(
            isinstance(item, HypothesisRecord) for item in hypotheses
        ):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "Hypothesis records missing for compilation",
                blocking=True,
            )
        if not isinstance(bridges, tuple) or not all(
            isinstance(item, BridgeRecord) for item in bridges
        ):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "Bridge proposals missing for compilation",
                blocking=True,
            )
        if not isinstance(specs, tuple) or not specs or not all(
            isinstance(item, AssumptionSpec) for item in specs
        ):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "Assumption specs missing for compilation",
                blocking=True,
            )
        if not isinstance(bridge_input_map, dict):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "bridge_input_map must be a dict",
                blocking=True,
            )
        if not isinstance(binding_spec, ScenarioBindingSpec):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "ScenarioBindingSpec missing",
                blocking=True,
            )
        if calibration_certificate is not None and not isinstance(
            calibration_certificate, CalibrationCertificate
        ):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "probability_calibration_certificate must be a typed CalibrationCertificate",
                blocking=True,
            )
        if external_snapshot is not None and not isinstance(
            external_snapshot, EXTERNAL_PROBABILITY_SNAPSHOT_CONTRACTS
        ):
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "external probability calibration snapshot has invalid type",
                blocking=True,
            )

        compilation = compile_assumptions(
            target_id=target_id,
            ledger=ledger,
            hypotheses=hypotheses,
            bridges=bridges,
            specs=specs,
            bridge_input_map=bridge_input_map,
        )
        if not compilation.passed:
            codes = tuple(item.code for item in compilation.findings)
            recoverable = {
                "MISSING_BRIDGE",
                "MISSING_TRANSFORM_INPUT",
                "UNKNOWN_HYPOTHESIS",
                "EMPTY_COMPILED_SET",
            }
            status = (
                StageStatus.RECOVERY_REQUIRED
                if codes and set(codes).issubset(recoverable)
                else StageStatus.BLOCKED
            )
            return StageExecutionResult(
                status,
                "assumption compilation failed: " + ", ".join(codes),
                {"compilation_findings": compilation.findings},
                blocking=True,
            )

        require_certificate = (
            context.execution_mode is ExecutionMode.LIVE_PRIMARY
            and binding_spec.probability_key is not None
        )
        bound = bind_scenarios(
            compilation.assumption_set,
            binding_spec,
            calibration_certificate=calibration_certificate,
            require_calibration_certificate=require_certificate,
        )
        if not bound.passed:
            codes = tuple(item.code for item in bound.findings)
            recoverable = {
                "MISSING_REQUIRED_ASSUMPTION",
                "MISSING_SCENARIO_PROBABILITY",
            }
            status = (
                StageStatus.RECOVERY_REQUIRED
                if codes and set(codes).issubset(recoverable)
                else StageStatus.BLOCKED
            )
            return StageExecutionResult(
                status,
                "scenario binding failed: " + ", ".join(codes),
                {"scenario_binding_findings": bound.findings},
                blocking=True,
            )

        binding_findings = list(bound.findings)
        if binding_spec.external_probability_source is not None:
            if external_snapshot is None or calibration_certificate is None:
                return StageExecutionResult(
                    StageStatus.RECOVERY_REQUIRED,
                    "external probability binding requires a frozen snapshot and certificate",
                    blocking=True,
                )
            try:
                external_snapshot.validate()
            except Exception as exc:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    f"external probability snapshot validation failed: {type(exc).__name__}: {exc}",
                    blocking=True,
                )
            rebound = bind_external_calibrated_probabilities(
                compilation.assumption_set,
                bound.scenario_set,
                binding_spec,
                probabilities=external_snapshot.probabilities,
                calibration_certificate=calibration_certificate,
                probability_source=external_snapshot.probability_source,
            )
            if not rebound.passed:
                return StageExecutionResult(
                    StageStatus.BLOCKED,
                    "external probability binding failed: "
                    + ", ".join(item.code for item in rebound.findings),
                    {"scenario_binding_findings": rebound.findings},
                    blocking=True,
                )
            bound = rebound
            binding_findings.extend(rebound.findings)

        scenario_set = bound.scenario_set
        outputs = {
            "compiled_assumption_set": compilation.assumption_set,
            "assumption_set_hash": compilation.assumption_set.assumption_set_hash,
            "bound_scenario_set": scenario_set,
            "scenario_set_hash": scenario_set.scenario_set_hash,
            "probability_calibration_status": scenario_set.calibration_status,
            "probability_weighting_allowed": scenario_set.numeric_weighting_allowed,
            "scenario_binding_findings": tuple(binding_findings),
        }
        if scenario_set.calibration_snapshot_hash is not None:
            outputs["probability_calibration_snapshot_hash"] = (
                scenario_set.calibration_snapshot_hash
            )
        if scenario_set.calibration_dataset_hash is not None:
            outputs["probability_calibration_dataset_hash"] = (
                scenario_set.calibration_dataset_hash
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "Bridge proposals deterministically compiled and bound into generic scenarios",
            outputs,
        )

    return run
