from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from math import isfinite
from typing import Callable

from .actual_units import Measure
from .control_plane import StageStatus
from .decision_impact import ModuleImpactTrace
from .evaluator_registry import RuntimeValuationInput, ValuationRuntimeInputs
from .ledger import EvidenceLedger
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .per import (
    EconomicAssumptionFingerprint,
    FundamentalPERAssumptions,
    HierarchicalWarrantedPER,
    PERLevel,
    build_hierarchical_warranted_per,
    fundamental_forward_per,
    validate_dcf_per_assumption_consistency,
)
from .risk import BetaLevel, HierarchicalBetaEstimate, hierarchical_partial_pool, relever_beta
from .wacc import (
    CostOfDebtInputs,
    CostOfEquityInputs,
    TargetCapitalStructure,
    TerminalConsistency,
    WACCResult,
    compute_wacc,
    validate_terminal_consistency,
)


@dataclass(frozen=True)
class LiveBetaInputBundle:
    levels: tuple[BetaLevel, ...]
    target_debt: float
    target_equity: float
    target_tax_rate: float
    input_refs: tuple[str, ...]
    economic_twin_peer_ids: tuple[str, ...]

    def validate(self) -> None:
        if not self.input_refs:
            raise ValueError("live Beta inputs require source/input references")
        if not self.economic_twin_peer_ids:
            raise ValueError("live Beta inputs require explicit L4 Economic Twin peer IDs")
        if self.target_debt < 0 or self.target_equity <= 0 or not 0 <= self.target_tax_rate < 1:
            raise ValueError("target capital structure for Beta is invalid")
        # hierarchical_partial_pool performs the canonical L1→L4 shape and numeric validation.
        hierarchical_partial_pool(self.levels)
        l4_ids = tuple(peer.peer_id for peer in self.levels[-1].peers)
        if set(l4_ids) != set(self.economic_twin_peer_ids):
            raise ValueError("declared Economic Twins must exactly match the L4 Beta peer pool")


BetaInputLoader = Callable[[OrchestratorContext], LiveBetaInputBundle]


def live_hierarchical_beta_adapter(
    *,
    loader: BetaInputLoader,
    required: bool = True,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        if not required:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "selected valuation methods have no downstream Beta consumer",
                {"hierarchical_beta_state": "NOT_APPLICABLE"},
            )
        try:
            bundle = loader(context)
            bundle.validate()
            estimate = hierarchical_partial_pool(bundle.levels)
            levered = relever_beta(
                estimate.asset_beta,
                debt=bundle.target_debt,
                equity=bundle.target_equity,
                tax_rate=bundle.target_tax_rate,
            )
            if not isfinite(levered) or levered <= 0:
                raise ValueError("target relevered Beta must be finite and positive")
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                f"live hierarchical Beta estimation failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        trace = ModuleImpactTrace(
            module_id="HIERARCHICAL_BETA_ENGINE",
            affected_decisions=("cost_of_equity_input",),
            final_output_refs=("WACC_ENGINE", "WARRANTED_PER_ENGINE", "DCF_EVALUATOR_WHEN_APPLICABLE"),
            economic_path_ids=("risk:beta_to_cost_of_equity",),
        )
        trace.validate()
        return StageExecutionResult(
            StageStatus.PASS,
            "L1→L4 asset Beta was partially pooled and relevered to the declared target capital structure",
            {
                "live_beta_input_bundle": bundle,
                "hierarchical_beta_estimate": estimate,
                "target_levered_beta": levered,
                "beta_input_refs": bundle.input_refs,
                "beta_target_debt": bundle.target_debt,
                "beta_target_equity": bundle.target_equity,
                "beta_target_tax_rate": bundle.target_tax_rate,
                "beta_impact_trace": trace,
            },
        )

    return run


@dataclass(frozen=True)
class LiveWACCInputBundle:
    as_of: str
    risk_free_rate: float
    equity_risk_premium: float
    cash_flow_currency: str
    risk_free_currency: str
    marginal_pre_tax_cost: float
    tax_rate: float
    equity_weight: float
    debt_weight: float
    input_refs: tuple[str, ...]
    country_risk_premium: float = 0.0
    country_risk_lambda: float = 0.0
    terminal_growth: float | None = None
    terminal_roic: float | None = None

    def validate(self) -> None:
        if not self.as_of or not self.input_refs:
            raise ValueError("live WACC inputs require as-of and source/input references")
        CostOfDebtInputs(self.marginal_pre_tax_cost, self.tax_rate)
        TargetCapitalStructure(self.equity_weight, self.debt_weight)
        if (self.terminal_growth is None) != (self.terminal_roic is None):
            raise ValueError("terminal growth and terminal ROIC must be supplied together")


WACCInputLoader = Callable[[OrchestratorContext], LiveWACCInputBundle]


def live_wacc_adapter(
    *,
    loader: WACCInputLoader,
    required: bool = True,
    target_structure_tolerance: float = 1e-6,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        if not required:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "selected valuation methods have no WACC/Cost-of-Equity consumer",
                {"wacc_validation_state": "NOT_APPLICABLE"},
            )
        beta = context.data.get("target_levered_beta")
        beta_debt = context.data.get("beta_target_debt")
        beta_equity = context.data.get("beta_target_equity")
        beta_tax = context.data.get("beta_target_tax_rate")
        if not all(isinstance(value, (int, float)) for value in (beta, beta_debt, beta_equity, beta_tax)):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "target Beta and Beta target-capital-structure outputs are required before WACC",
                blocking=True,
            )
        try:
            bundle = loader(context)
            bundle.validate()
            total = float(beta_debt) + float(beta_equity)
            expected_debt_weight = float(beta_debt) / total
            expected_equity_weight = float(beta_equity) / total
            if abs(bundle.debt_weight - expected_debt_weight) > target_structure_tolerance or abs(bundle.equity_weight - expected_equity_weight) > target_structure_tolerance:
                raise ValueError("WACC target capital structure does not match the structure used to relever Beta")
            if abs(bundle.tax_rate - float(beta_tax)) > target_structure_tolerance:
                raise ValueError("WACC tax rate does not match the tax rate used to relever Beta")
            equity = CostOfEquityInputs(
                risk_free_rate=bundle.risk_free_rate,
                beta=float(beta),
                equity_risk_premium=bundle.equity_risk_premium,
                cash_flow_currency=bundle.cash_flow_currency,
                risk_free_currency=bundle.risk_free_currency,
                country_risk_premium=bundle.country_risk_premium,
                country_risk_lambda=bundle.country_risk_lambda,
                additional_risk_premium=0.0,
            )
            debt = CostOfDebtInputs(bundle.marginal_pre_tax_cost, bundle.tax_rate)
            capital = TargetCapitalStructure(bundle.equity_weight, bundle.debt_weight)
            result = compute_wacc(equity, debt, capital)
            terminal: TerminalConsistency | None = None
            if bundle.terminal_growth is not None and bundle.terminal_roic is not None:
                terminal = validate_terminal_consistency(
                    wacc=result.wacc,
                    terminal_growth=bundle.terminal_growth,
                    terminal_roic=bundle.terminal_roic,
                )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                f"live WACC validation failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        beta_refs = context.data.get("beta_input_refs", ())
        funding_credit_ids = context.data.get("funding_credit_improvement_evidence_ids", ())
        source_refs = tuple(dict.fromkeys((*tuple(beta_refs), *bundle.input_refs)))
        runtime = ValuationRuntimeInputs(
            (
                RuntimeValuationInput(
                    "cost_of_equity",
                    Measure(Decimal(str(result.cost_of_equity)), "ratio", bundle.as_of),
                    "risk:beta_to_cost_of_equity",
                    source_refs,
                ),
                RuntimeValuationInput(
                    "wacc",
                    Measure(Decimal(str(result.wacc)), "ratio", bundle.as_of),
                    "risk:wacc_to_intrinsic",
                    source_refs,
                ),
            )
        )
        trace = ModuleImpactTrace(
            module_id="WACC_ENGINE",
            evidence_ids=tuple(funding_credit_ids) if isinstance(funding_credit_ids, tuple) else (),
            affected_decisions=("discount_rate_input", "cost_of_equity_input"),
            economic_path_ids=("risk:beta_to_cost_of_equity", "risk:wacc_to_intrinsic"),
            final_output_refs=("DETERMINISTIC_VALUATION_WHEN_CONSUMED", "WARRANTED_PER_ENGINE"),
        )
        trace.validate()
        return StageExecutionResult(
            StageStatus.PASS,
            "currency-consistent Cost of Equity/WACC computed with the same target capital structure as Beta; funding evidence was not converted into an automatic WACC reduction",
            {
                "live_wacc_input_bundle": bundle,
                "wacc_result": result,
                "cost_of_equity": result.cost_of_equity,
                "wacc": result.wacc,
                "terminal_consistency": terminal,
                "valuation_runtime_inputs": runtime,
                "funding_credit_evidence_considered": tuple(funding_credit_ids) if isinstance(funding_credit_ids, tuple) else (),
                "wacc_input_refs": source_refs,
                "wacc_impact_trace": trace,
            },
        )

    return run


@dataclass(frozen=True)
class PERCoreEconomics:
    normalized_forward_eps: float
    explicit_growth_rates: tuple[float, ...]
    fcfe_conversion_rates: tuple[float, ...]
    terminal_growth: float
    terminal_roe: float
    fingerprint: EconomicAssumptionFingerprint


@dataclass(frozen=True)
class LivePERInputBundle:
    core: PERCoreEconomics
    input_refs: tuple[str, ...]
    expansion: PERCoreEconomics | None = None
    expansion_evidence_ids: tuple[str, ...] = ()
    residual_levels: tuple[PERLevel, ...] | None = None
    residual_input_refs: tuple[str, ...] = ()

    def validate(self, *, ledger: EvidenceLedger, target_ticker: str) -> None:
        if not self.input_refs:
            raise ValueError("live PER inputs require source/input references")
        if self.expansion is None and self.expansion_evidence_ids:
            raise ValueError("expansion evidence cannot be supplied without expansion economics")
        if self.expansion is not None:
            if not self.expansion_evidence_ids:
                raise ValueError("Expansion-Adjusted PER requires explicit committed/pre-invested Evidence IDs")
            for evidence_id in self.expansion_evidence_ids:
                ledger.get(evidence_id)
        if self.residual_levels is not None:
            if not self.residual_input_refs:
                raise ValueError("hierarchical PER residual pooling requires peer/source references")
            for level in self.residual_levels:
                for peer in level.peers:
                    if peer.peer_id == target_ticker:
                        raise ValueError("target company cannot enter its own PER residual peer pool")


PERInputLoader = Callable[[OrchestratorContext], LivePERInputBundle]


def _per_assumptions(economics: PERCoreEconomics, *, cost_of_equity: float) -> FundamentalPERAssumptions:
    return FundamentalPERAssumptions(
        normalized_forward_eps=economics.normalized_forward_eps,
        explicit_growth_rates=economics.explicit_growth_rates,
        fcfe_conversion_rates=economics.fcfe_conversion_rates,
        cost_of_equity=cost_of_equity,
        terminal_growth=economics.terminal_growth,
        terminal_roe=economics.terminal_roe,
    )


def live_warranted_per_adapter(
    *,
    loader: PERInputLoader,
    required: bool = True,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        if not required:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "Warranted PER is not allowed/selected for this route",
                {"hierarchical_warranted_per_state": "NOT_APPLICABLE"},
            )
        cost_of_equity = context.data.get("cost_of_equity")
        ledger = context.data.get("evidence_ledger")
        ticker = context.data.get("ticker")
        if not isinstance(cost_of_equity, (float, int)) or not isinstance(ledger, EvidenceLedger) or not isinstance(ticker, str):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "Cost of Equity, EvidenceLedger and ticker are required before Warranted PER",
                blocking=True,
            )
        try:
            bundle = loader(context)
            bundle.validate(ledger=ledger, target_ticker=ticker)
            core = _per_assumptions(bundle.core, cost_of_equity=float(cost_of_equity))
            expansion = _per_assumptions(bundle.expansion, cost_of_equity=float(cost_of_equity)) if bundle.expansion is not None else None
            result = build_hierarchical_warranted_per(
                core,
                expansion=expansion,
                expansion_is_committed_or_preinvested=expansion is not None,
                residual_levels=bundle.residual_levels,
            )
            core_detail = fundamental_forward_per(core)
            expansion_detail = fundamental_forward_per(expansion) if expansion is not None else None
            market_realization_price = None
            if result.market_realization_per is not None:
                applicable_eps = expansion.normalized_forward_eps if expansion is not None else core.normalized_forward_eps
                market_realization_price = result.market_realization_per * applicable_eps
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                f"live Hierarchical Warranted PER failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        trace = ModuleImpactTrace(
            module_id="WARRANTED_PER_ENGINE",
            evidence_ids=bundle.expansion_evidence_ids,
            affected_decisions=("cross_method_intrinsic_reference",),
            economic_path_ids=("risk:beta_to_cost_of_equity", "valuation:warranted_per"),
            final_output_refs=("DCF_PER_ASSUMPTION_CONSISTENCY_GATE", "AUDIT_GATE", "FINAL_REPORT"),
        )
        trace.validate()
        return StageExecutionResult(
            StageStatus.PASS,
            "Core/Expansion Warranted PER computed from normalized EPS and the WACC-stage Cost of Equity; peer residual premium remains a separate Market-Realization layer",
            {
                "live_per_input_bundle": bundle,
                "hierarchical_warranted_per": result,
                "core_fundamental_per": result.core_fundamental_per,
                "core_per_implied_price": core_detail.implied_price,
                "expansion_adjusted_per": result.expansion_adjusted_fundamental_per,
                "expansion_per_implied_price": expansion_detail.implied_price if expansion_detail is not None else None,
                "market_realization_per": result.market_realization_per,
                "market_realization_implied_price": market_realization_price,
                "per_economic_fingerprint": bundle.core.fingerprint,
                "per_input_refs": tuple(dict.fromkeys((*bundle.input_refs, *bundle.residual_input_refs))),
                "per_impact_trace": trace,
            },
        )

    return run


DCFFingerprintLoader = Callable[[OrchestratorContext], EconomicAssumptionFingerprint]


def live_dcf_per_consistency_adapter(
    *,
    dcf_fingerprint_loader: DCFFingerprintLoader,
    required: bool = True,
    value_gap_warning_threshold: float = 0.25,
) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        if not required:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "both DCF-like and Warranted PER outputs are not present",
                {"dcf_per_consistency_state": "NOT_APPLICABLE"},
            )
        per_fingerprint = context.data.get("per_economic_fingerprint")
        if not isinstance(per_fingerprint, EconomicAssumptionFingerprint):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "PER economic fingerprint missing before DCF-PER consistency gate",
                blocking=True,
            )
        try:
            dcf_fingerprint = dcf_fingerprint_loader(context)
            if not isinstance(dcf_fingerprint, EconomicAssumptionFingerprint):
                raise ValueError("DCF fingerprint loader returned the wrong type")
            validate_dcf_per_assumption_consistency(dcf_fingerprint, per_fingerprint)
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"DCF-PER assumption consistency failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        gap = None
        warning = False
        valuation = context.data.get("generic_valuation_result")
        per_price = context.data.get("core_per_implied_price")
        expected = getattr(valuation, "expected_value_per_share", None)
        if expected is not None and isinstance(per_price, (int, float)) and float(expected) > 0:
            gap = per_price / float(expected) - 1.0
            warning = abs(gap) >= value_gap_warning_threshold
        return StageExecutionResult(
            StageStatus.WARNING if warning else StageStatus.PASS,
            "DCF and Core PER economic fingerprints are consistent"
            + ("; value divergence remains large and must be reconciled rather than averaged" if warning else ""),
            {
                "dcf_per_consistency_state": "PASS",
                "dcf_economic_fingerprint": dcf_fingerprint,
                "dcf_per_value_gap_pct": gap,
                "dcf_per_value_divergence_review": warning,
            },
        )

    return run
