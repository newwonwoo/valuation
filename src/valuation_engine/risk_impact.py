from __future__ import annotations

from dataclasses import dataclass

from .decision_impact import ModuleImpactTrace
from .risk_adapters import LiveBetaStageResult, LiveWACCStageResult
from .valuation_execution import GenericValuationResult


_DCF_LIKE_TOKENS = ("dcf", "npv", "ddm", "residual_income", "rate_base_roe")


@dataclass(frozen=True)
class RiskConsumptionAudit:
    required: bool
    passed: bool
    missing_scenarios: tuple[str, ...]
    expected_beta_prefix: str | None
    expected_wacc_prefix: str | None


def selected_methods_require_discount_rate(selected_methods: tuple[str, ...]) -> bool:
    return any(token in method.lower() for method in selected_methods for token in _DCF_LIKE_TOKENS)


def audit_risk_consumption(
    *,
    valuation: GenericValuationResult,
    selected_methods: tuple[str, ...],
    beta_result: LiveBetaStageResult | None,
    wacc_result: LiveWACCStageResult | None,
) -> RiskConsumptionAudit:
    required = selected_methods_require_discount_rate(selected_methods)
    if not required:
        return RiskConsumptionAudit(False, True, (), None, None)
    if beta_result is None or wacc_result is None:
        return RiskConsumptionAudit(
            True,
            False,
            tuple(item.scenario_id for item in valuation.scenarios),
            None if beta_result is None else f"beta:{beta_result.snapshot_hash}:",
            None if wacc_result is None else f"wacc:{wacc_result.snapshot_hash}:",
        )
    if wacc_result.beta_result.snapshot_hash != beta_result.snapshot_hash:
        return RiskConsumptionAudit(
            True,
            False,
            tuple(item.scenario_id for item in valuation.scenarios),
            f"beta:{beta_result.snapshot_hash}:",
            f"wacc:{wacc_result.snapshot_hash}:",
        )
    beta_prefix = f"beta:{beta_result.snapshot_hash}:"
    wacc_prefix = f"wacc:{wacc_result.snapshot_hash}:"
    missing: list[str] = []
    for scenario in valuation.scenarios:
        has_beta = any(path.startswith(beta_prefix) for path in scenario.economic_path_ids)
        has_wacc = any(path.startswith(wacc_prefix) for path in scenario.economic_path_ids)
        if not has_beta or not has_wacc:
            missing.append(scenario.scenario_id)
    return RiskConsumptionAudit(True, not missing, tuple(missing), beta_prefix, wacc_prefix)


def build_risk_impact_traces(
    *,
    beta_result: LiveBetaStageResult | None,
    wacc_result: LiveWACCStageResult | None,
    valuation: GenericValuationResult,
    selected_methods: tuple[str, ...],
) -> tuple[ModuleImpactTrace, ...]:
    audit = audit_risk_consumption(
        valuation=valuation,
        selected_methods=selected_methods,
        beta_result=beta_result,
        wacc_result=wacc_result,
    )
    if not audit.required or not audit.passed or beta_result is None or wacc_result is None:
        return ()
    beta_path = f"beta:{beta_result.snapshot_hash}"
    wacc_path = f"wacc:{wacc_result.snapshot_hash}"
    beta_trace = ModuleImpactTrace(
        module_id="HIERARCHICAL_BETA_ENGINE",
        evidence_ids=beta_result.selection_evidence_ids,
        affected_decisions=("target_levered_beta", "cost_of_equity_input"),
        economic_path_ids=(beta_path, wacc_path),
        final_output_refs=("WACC_ENGINE", "DETERMINISTIC_VALUATION", "WARRANTED_PER_ENGINE"),
    )
    wacc_trace = ModuleImpactTrace(
        module_id="WACC_ENGINE",
        evidence_ids=wacc_result.funding_credit_evidence_ids,
        affected_decisions=("cost_of_equity", "discount_rate"),
        economic_path_ids=(wacc_path,),
        final_output_refs=("DETERMINISTIC_VALUATION", "WARRANTED_PER_ENGINE"),
    )
    beta_trace.validate()
    wacc_trace.validate()
    return (beta_trace, wacc_trace)
