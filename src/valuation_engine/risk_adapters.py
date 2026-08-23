from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from typing import Callable

from .control_plane import StageStatus
from .ledger import EvidenceLedger
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .risk import (
    BETA_LEVEL_ORDER,
    BetaLevel,
    BetaLevelName,
    HierarchicalBetaEstimate,
    PeerBetaInput,
    hierarchical_partial_pool,
    relever_beta,
)
from .wacc import (
    CostOfDebtInputs,
    CostOfEquityInputs,
    CustomerAdvanceCreditEvidence,
    TargetCapitalStructure,
    TerminalConsistency,
    WACCResult,
    compute_wacc,
    validate_terminal_consistency,
)


_FORBIDDEN_PRE_FREEZE_KEYS = {
    "current_market_price",
    "market_price",
    "market_observation",
    "target_market_cap",
    "target_price",
    "consensus_target",
    "target_multiple",
    "street_reference",
}


class TargetCapitalStructureMethod(str, Enum):
    PEER_NORMALIZED_MARKET_VALUE = "peer_normalized_market_value"
    MANAGEMENT_TARGET = "management_target"
    REGULATORY_TARGET = "regulatory_target"
    LONG_RUN_POLICY = "long_run_policy"
    COMPILED_SCENARIO = "compiled_scenario"


class AdditionalRiskBasis(str, Enum):
    NONE = "none"
    EVIDENCED_LIQUIDITY = "evidenced_liquidity"
    EVIDENCED_REFINANCING = "evidenced_refinancing"
    EVIDENCED_OTHER = "evidenced_other"


@dataclass(frozen=True)
class LivePeerBetaObservation:
    peer_id: str
    levered_beta: float
    debt: float
    equity: float
    tax_rate: float
    benchmark_id: str
    return_frequency: str
    estimation_window_months: int
    as_of: str
    source_ref: str
    beta_standard_error: float | None = None
    estimation_method: str = "regression_or_adjusted"

    def validate(self) -> None:
        if not all(
            (
                self.peer_id,
                self.benchmark_id,
                self.return_frequency,
                self.as_of,
                self.source_ref,
                self.estimation_method,
            )
        ):
            raise ValueError("live peer beta observation has missing identity/method/source fields")
        _parse_date(self.as_of, "peer beta as_of")
        if self.estimation_window_months <= 0:
            raise ValueError("estimation_window_months must be positive")
        PeerBetaInput(
            peer_id=self.peer_id,
            levered_beta=self.levered_beta,
            debt=self.debt,
            equity=self.equity,
            tax_rate=self.tax_rate,
            beta_standard_error=self.beta_standard_error,
            estimation_method=self.estimation_method,
        )

    def to_engine_input(self) -> PeerBetaInput:
        self.validate()
        return PeerBetaInput(
            peer_id=self.peer_id,
            levered_beta=self.levered_beta,
            debt=self.debt,
            equity=self.equity,
            tax_rate=self.tax_rate,
            beta_standard_error=self.beta_standard_error,
            estimation_method=self.estimation_method,
        )


@dataclass(frozen=True)
class LiveBetaLevelObservation:
    level: BetaLevelName
    peers: tuple[LivePeerBetaObservation, ...]
    selection_rationale: str
    selection_evidence_ids: tuple[str, ...]
    risk_driver_features: tuple[str, ...]

    def validate(self) -> None:
        if not self.peers or not self.selection_rationale or not self.selection_evidence_ids:
            raise ValueError(f"{self.level.value} requires peers, rationale and Evidence IDs")
        peer_ids = tuple(peer.peer_id for peer in self.peers)
        if len(peer_ids) != len(set(peer_ids)):
            raise ValueError(f"duplicate peer inside {self.level.value}")
        for peer in self.peers:
            peer.validate()
        if self.level is BetaLevelName.L4_ECONOMIC_TWINS and not self.risk_driver_features:
            raise ValueError("L4 Economic Twins require explicit systematic-risk features")

    def to_engine_level(self) -> BetaLevel:
        self.validate()
        return BetaLevel(self.level, tuple(peer.to_engine_input() for peer in self.peers))


@dataclass(frozen=True)
class LiveCapitalStructureObservation:
    equity_weight: float
    debt_weight: float
    tax_rate: float
    method: TargetCapitalStructureMethod
    as_of: str
    source_refs: tuple[str, ...]
    rationale: str

    def validate(self) -> None:
        if not self.as_of or not self.source_refs or not self.rationale:
            raise ValueError("target capital structure requires as_of, source_refs and rationale")
        _parse_date(self.as_of, "target capital structure as_of")
        if not isfinite(self.tax_rate) or not 0 <= self.tax_rate < 1:
            raise ValueError("target capital structure tax_rate must be in [0, 1)")
        if self.equity_weight <= 0:
            raise ValueError("target equity weight must be positive for relevering")
        TargetCapitalStructure(self.equity_weight, self.debt_weight)

    def to_engine_structure(self) -> TargetCapitalStructure:
        self.validate()
        return TargetCapitalStructure(self.equity_weight, self.debt_weight)


@dataclass(frozen=True)
class LiveBetaUniverse:
    levels: tuple[LiveBetaLevelObservation, ...]
    target_capital_structure: LiveCapitalStructureObservation
    universe_rationale: str
    source_refs: tuple[str, ...]

    def validate(self) -> None:
        if tuple(item.level for item in self.levels) != BETA_LEVEL_ORDER:
            raise ValueError("live Beta universe must be exactly L1→L2→L3→L4")
        if not self.universe_rationale or not self.source_refs:
            raise ValueError("live Beta universe requires rationale and source references")
        self.target_capital_structure.validate()
        seen_peer_ids: set[str] = set()
        conventions: set[tuple[str, str, int]] = set()
        for level in self.levels:
            level.validate()
            for peer in level.peers:
                if peer.peer_id in seen_peer_ids:
                    raise ValueError(
                        f"peer {peer.peer_id} appears in more than one hierarchy level and would be double-counted"
                    )
                seen_peer_ids.add(peer.peer_id)
                conventions.add(
                    (peer.benchmark_id, peer.return_frequency, peer.estimation_window_months)
                )
        if len(conventions) != 1:
            raise ValueError(
                "live Beta peers must use one normalized benchmark/frequency/estimation window"
            )


@dataclass(frozen=True)
class LiveBetaStageResult:
    estimate: HierarchicalBetaEstimate
    target_asset_beta: float
    target_levered_beta: float
    target_capital_structure: LiveCapitalStructureObservation
    peer_ids: tuple[str, ...]
    source_refs: tuple[str, ...]
    selection_evidence_ids: tuple[str, ...]
    snapshot_hash: str


BetaUniverseLoader = Callable[[OrchestratorContext], LiveBetaUniverse]


@dataclass(frozen=True)
class RateObservation:
    value: float
    currency: str
    as_of: str
    source_ref: str
    methodology: str

    def validate(self, *, allow_negative: bool = False) -> None:
        if not all((self.currency, self.as_of, self.source_ref, self.methodology)):
            raise ValueError("rate observation requires currency, as_of, source_ref and methodology")
        _parse_date(self.as_of, "rate observation as_of")
        if not isfinite(self.value):
            raise ValueError("rate observation value must be finite")
        if not allow_negative and self.value < 0:
            raise ValueError("rate observation must be non-negative")


@dataclass(frozen=True)
class LiveWACCInputs:
    cash_flow_currency: str
    risk_free_rate: RateObservation
    equity_risk_premium: RateObservation
    marginal_pre_tax_cost_of_debt: RateObservation
    target_capital_structure: LiveCapitalStructureObservation
    country_risk_premium: RateObservation | None = None
    country_risk_lambda: float = 0.0
    country_risk_source_ref: str = ""
    additional_risk_premium: float = 0.0
    additional_risk_basis: AdditionalRiskBasis = AdditionalRiskBasis.NONE
    additional_risk_evidence_ids: tuple[str, ...] = ()
    funding_credit_evidence_ids: tuple[str, ...] = ()
    customer_advance_credit_evidence: CustomerAdvanceCreditEvidence | None = None
    terminal_growth: float | None = None
    terminal_roic: float | None = None

    def validate(self) -> None:
        if not self.cash_flow_currency:
            raise ValueError("cash_flow_currency is required")
        self.risk_free_rate.validate(allow_negative=True)
        self.equity_risk_premium.validate()
        self.marginal_pre_tax_cost_of_debt.validate()
        self.target_capital_structure.validate()
        currencies = {
            self.risk_free_rate.currency,
            self.equity_risk_premium.currency,
            self.marginal_pre_tax_cost_of_debt.currency,
        }
        if self.country_risk_premium is not None:
            self.country_risk_premium.validate()
            currencies.add(self.country_risk_premium.currency)
        if currencies != {self.cash_flow_currency}:
            raise ValueError("all WACC rates must match the cash-flow currency")
        if not isfinite(self.country_risk_lambda) or not 0 <= self.country_risk_lambda <= 2:
            raise ValueError("country_risk_lambda must be in [0, 2]")
        if self.country_risk_lambda > 0 and (
            self.country_risk_premium is None or not self.country_risk_source_ref
        ):
            raise ValueError("non-zero country risk requires premium and exposure source")
        if not isfinite(self.additional_risk_premium) or self.additional_risk_premium < 0:
            raise ValueError("additional_risk_premium must be finite and non-negative")
        if self.additional_risk_premium > 0:
            if self.additional_risk_basis is AdditionalRiskBasis.NONE:
                raise ValueError("additional risk premium requires an evidenced risk basis")
            if not self.additional_risk_evidence_ids:
                raise ValueError("additional risk premium requires Evidence IDs")
        elif self.additional_risk_basis is not AdditionalRiskBasis.NONE:
            raise ValueError("additional risk basis must be NONE when premium is zero")
        if (self.terminal_growth is None) != (self.terminal_roic is None):
            raise ValueError("terminal_growth and terminal_roic must be supplied together")


@dataclass(frozen=True)
class LiveWACCStageResult:
    beta_result: LiveBetaStageResult
    wacc_result: WACCResult
    terminal_consistency: TerminalConsistency | None
    source_refs: tuple[str, ...]
    funding_credit_evidence_ids: tuple[str, ...]
    customer_advance_credit_supports_reduction_candidate: bool
    snapshot_hash: str


WACCInputsLoader = Callable[[OrchestratorContext], LiveWACCInputs]


def _parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be ISO date") from exc


def _reject_target_market_leakage(context: OrchestratorContext) -> None:
    leaked = tuple(sorted(key for key in _FORBIDDEN_PRE_FREEZE_KEYS if key in context.data))
    if leaked:
        raise PermissionError(
            "pre-freeze Beta/WACC context contains target Street/market fields: "
            + ", ".join(leaked)
        )


def _active_evidence_ids(context: OrchestratorContext) -> set[str]:
    ledger = context.data.get("evidence_ledger")
    if not isinstance(ledger, EvidenceLedger):
        raise ValueError("EvidenceLedger is required before live Beta/WACC stages")
    return {item.id for item in ledger.active()}


def _validate_evidence_ids(
    *,
    label: str,
    evidence_ids: tuple[str, ...],
    active_ids: set[str],
) -> None:
    unknown = tuple(sorted(set(evidence_ids) - active_ids))
    if unknown:
        raise ValueError(f"{label} references inactive/unknown Evidence IDs: {', '.join(unknown)}")


def _capital_structure_equal(
    left: LiveCapitalStructureObservation,
    right: LiveCapitalStructureObservation,
) -> bool:
    return (
        abs(left.equity_weight - right.equity_weight) <= 1e-12
        and abs(left.debt_weight - right.debt_weight) <= 1e-12
        and abs(left.tax_rate - right.tax_rate) <= 1e-12
        and left.method is right.method
    )


def _stable_hash(payload: dict) -> str:
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def live_hierarchical_beta_adapter(*, loader: BetaUniverseLoader) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            _reject_target_market_leakage(context)
            active_ids = _active_evidence_ids(context)
            universe = loader(context)
            if not isinstance(universe, LiveBetaUniverse):
                raise TypeError("Beta loader must return LiveBetaUniverse")
            universe.validate()
            selection_ids = tuple(
                dict.fromkeys(
                    evidence_id
                    for level in universe.levels
                    for evidence_id in level.selection_evidence_ids
                )
            )
            _validate_evidence_ids(
                label="Beta peer selection",
                evidence_ids=selection_ids,
                active_ids=active_ids,
            )
            engine_levels = tuple(level.to_engine_level() for level in universe.levels)
            estimate = hierarchical_partial_pool(engine_levels)
            structure = universe.target_capital_structure
            target_levered = relever_beta(
                estimate.asset_beta,
                debt=structure.debt_weight,
                equity=structure.equity_weight,
                tax_rate=structure.tax_rate,
            )
            peer_ids = tuple(sorted(peer.peer_id for level in universe.levels for peer in level.peers))
            source_refs = tuple(
                sorted(
                    set(universe.source_refs)
                    | set(structure.source_refs)
                    | {peer.source_ref for level in universe.levels for peer in level.peers}
                )
            )
            payload = {
                "asset_beta": estimate.asset_beta,
                "posterior_variance": estimate.posterior_variance,
                "target_levered_beta": target_levered,
                "peer_ids": peer_ids,
                "source_refs": source_refs,
                "selection_evidence_ids": sorted(selection_ids),
                "target_capital_structure": {
                    "equity_weight": structure.equity_weight,
                    "debt_weight": structure.debt_weight,
                    "tax_rate": structure.tax_rate,
                    "method": structure.method.value,
                    "as_of": structure.as_of,
                    "source_refs": sorted(structure.source_refs),
                },
            }
            result = LiveBetaStageResult(
                estimate=estimate,
                target_asset_beta=estimate.asset_beta,
                target_levered_beta=target_levered,
                target_capital_structure=structure,
                peer_ids=peer_ids,
                source_refs=source_refs,
                selection_evidence_ids=selection_ids,
                snapshot_hash=_stable_hash(payload),
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"live hierarchical Beta failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "live L1→L4 Economic-Twin Beta estimated and relevered with one target structure",
            {
                "live_beta_result": result,
                "target_asset_beta": result.target_asset_beta,
                "target_levered_beta": result.target_levered_beta,
                "beta_snapshot_hash": result.snapshot_hash,
                "beta_peer_ids": result.peer_ids,
                "beta_source_refs": result.source_refs,
            },
        )

    return run


def live_wacc_validation_adapter(*, loader: WACCInputsLoader) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            _reject_target_market_leakage(context)
            active_ids = _active_evidence_ids(context)
            beta_result = context.data.get("live_beta_result")
            if not isinstance(beta_result, LiveBetaStageResult):
                raise ValueError("LiveBetaStageResult is required before WACC validation")
            inputs = loader(context)
            if not isinstance(inputs, LiveWACCInputs):
                raise TypeError("WACC loader must return LiveWACCInputs")
            inputs.validate()
            if not _capital_structure_equal(
                beta_result.target_capital_structure,
                inputs.target_capital_structure,
            ):
                raise ValueError(
                    "Beta relevering and WACC must use the same target capital structure, tax rate and method"
                )
            _validate_evidence_ids(
                label="additional risk premium",
                evidence_ids=inputs.additional_risk_evidence_ids,
                active_ids=active_ids,
            )
            _validate_evidence_ids(
                label="funding credit candidate",
                evidence_ids=inputs.funding_credit_evidence_ids,
                active_ids=active_ids,
            )
            country_premium = (
                inputs.country_risk_premium.value
                if inputs.country_risk_premium is not None
                else 0.0
            )
            equity = CostOfEquityInputs(
                risk_free_rate=inputs.risk_free_rate.value,
                beta=beta_result.target_levered_beta,
                equity_risk_premium=inputs.equity_risk_premium.value,
                cash_flow_currency=inputs.cash_flow_currency,
                risk_free_currency=inputs.risk_free_rate.currency,
                country_risk_premium=country_premium,
                country_risk_lambda=inputs.country_risk_lambda,
                additional_risk_premium=inputs.additional_risk_premium,
            )
            debt = CostOfDebtInputs(
                marginal_pre_tax_cost=inputs.marginal_pre_tax_cost_of_debt.value,
                tax_rate=inputs.target_capital_structure.tax_rate,
            )
            wacc_result = compute_wacc(
                equity,
                debt,
                inputs.target_capital_structure.to_engine_structure(),
            )
            terminal: TerminalConsistency | None = None
            if inputs.terminal_growth is not None and inputs.terminal_roic is not None:
                terminal = validate_terminal_consistency(
                    wacc=wacc_result.wacc,
                    terminal_growth=inputs.terminal_growth,
                    terminal_roic=inputs.terminal_roic,
                )
            source_refs = tuple(
                sorted(
                    {
                        inputs.risk_free_rate.source_ref,
                        inputs.equity_risk_premium.source_ref,
                        inputs.marginal_pre_tax_cost_of_debt.source_ref,
                        *inputs.target_capital_structure.source_refs,
                        *(
                            (inputs.country_risk_premium.source_ref,)
                            if inputs.country_risk_premium is not None
                            else ()
                        ),
                        *((inputs.country_risk_source_ref,) if inputs.country_risk_source_ref else ()),
                    }
                )
            )
            credit_support = bool(
                inputs.customer_advance_credit_evidence
                and inputs.customer_advance_credit_evidence.supports_wacc_reduction
            )
            payload = {
                "beta_snapshot_hash": beta_result.snapshot_hash,
                "cost_of_equity": wacc_result.cost_of_equity,
                "after_tax_cost_of_debt": wacc_result.after_tax_cost_of_debt,
                "wacc": wacc_result.wacc,
                "source_refs": source_refs,
                "funding_credit_evidence_ids": sorted(inputs.funding_credit_evidence_ids),
                "customer_advance_credit_supports_reduction_candidate": credit_support,
                "terminal": asdict(terminal) if terminal is not None else None,
            }
            result = LiveWACCStageResult(
                beta_result=beta_result,
                wacc_result=wacc_result,
                terminal_consistency=terminal,
                source_refs=source_refs,
                funding_credit_evidence_ids=inputs.funding_credit_evidence_ids,
                customer_advance_credit_supports_reduction_candidate=credit_support,
                snapshot_hash=_stable_hash(payload),
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"live WACC validation failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "currency-consistent WACC computed from live Beta and independent marginal financing inputs",
            {
                "live_wacc_result": result,
                "cost_of_equity": result.wacc_result.cost_of_equity,
                "after_tax_cost_of_debt": result.wacc_result.after_tax_cost_of_debt,
                "wacc": result.wacc_result.wacc,
                "wacc_snapshot_hash": result.snapshot_hash,
                "wacc_source_refs": result.source_refs,
                "customer_advance_credit_supports_reduction_candidate": (
                    result.customer_advance_credit_supports_reduction_candidate
                ),
            },
        )

    return run
