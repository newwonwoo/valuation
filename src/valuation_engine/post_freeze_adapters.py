from __future__ import annotations

from hashlib import sha256
from typing import Callable

from .control_plane import StageStatus, authorize_post_freeze
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .post_freeze import compare_generic_to_market, compare_generic_to_street
from .records import MarketObservation
from .street import StreetGapDriver, StreetResearchReport
from .valuation_execution import GenericValuationResult


StreetLoader = Callable[[], tuple[StreetResearchReport, ...]]
MarketLoader = Callable[[], MarketObservation]


def _require_post_freeze(context: OrchestratorContext) -> None:
    if context.freeze_token is None:
        raise PermissionError("IntrinsicFreezeToken is required")
    authorize_post_freeze(context.freeze_token, run_id=context.run_id)


def street_reference_load_adapter(*, loader: StreetLoader) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            _require_post_freeze(context)
            reports = loader()
            if not isinstance(reports, tuple) or not reports:
                raise ValueError("Street loader must return a non-empty tuple")
            if not all(isinstance(item, StreetResearchReport) for item in reports):
                raise ValueError("Street loader returned an invalid report object")
            payload = "\n".join(
                sorted(
                    f"{item.broker}|{item.analyst}|{item.published_date}|{item.target_price}|{item.target_price_currency}|{item.valuation_method}|{item.source_ref}"
                    for item in reports
                )
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"Street reference load failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "target-company Street references loaded after a valid same-run Freeze Token",
            {
                "street_reports": reports,
                "street_reference_hash": sha256(payload.encode("utf-8")).hexdigest(),
            },
        )

    return run


def street_gap_analyzer_adapter(*, drivers: tuple[StreetGapDriver, ...] = ()) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            _require_post_freeze(context)
            valuation = context.data.get("generic_valuation_result")
            reports = context.data.get("street_reports")
            if not isinstance(valuation, GenericValuationResult):
                raise ValueError("GenericValuationResult is missing")
            if not isinstance(reports, tuple) or not reports:
                raise ValueError("Street reports are missing")
            bundle = compare_generic_to_street(valuation, reports, drivers=drivers)
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"Street gap analysis failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        rationale = (
            "Street gap calculated from calibrated Expected Value and scenario envelope"
            if bundle.envelope.expected_gap is not None
            else "Street gap preserved as scenario envelope because probability weighting is not calibrated"
        )
        return StageExecutionResult(
            StageStatus.PASS,
            rationale,
            {"street_comparison": bundle},
        )

    return run


def market_price_load_adapter(*, loader: MarketLoader, currency: str) -> StageAdapter:
    if not currency:
        raise ValueError("market currency is required")

    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            _require_post_freeze(context)
            observation = loader()
            if not isinstance(observation, MarketObservation):
                raise ValueError("market loader returned an invalid observation")
            payload = f"{observation.price}|{observation.as_of}|{observation.source_ref}|{currency}"
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"Market price load failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "target-company market price loaded only after intrinsic freeze",
            {
                "market_observation": observation,
                "market_currency": currency,
                "market_reference_hash": sha256(payload.encode("utf-8")).hexdigest(),
            },
        )

    return run


def market_compare_adapter() -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            _require_post_freeze(context)
            valuation = context.data.get("generic_valuation_result")
            observation = context.data.get("market_observation")
            currency = context.data.get("market_currency")
            if not isinstance(valuation, GenericValuationResult):
                raise ValueError("GenericValuationResult is missing")
            if not isinstance(observation, MarketObservation):
                raise ValueError("MarketObservation is missing")
            if not isinstance(currency, str) or not currency:
                raise ValueError("market currency is missing")
            bundle = compare_generic_to_market(valuation, observation, currency=currency)
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"Market comparison failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        rationale = (
            "current price compared with calibrated Expected Value and each scenario"
            if bundle.envelope.expected_gap is not None
            else "current price compared with each intrinsic scenario; no Expected Value fabricated"
        )
        return StageExecutionResult(StageStatus.PASS, rationale, {"market_comparison": bundle})

    return run
