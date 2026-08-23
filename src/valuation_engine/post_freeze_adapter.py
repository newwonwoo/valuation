from __future__ import annotations

from typing import Callable

from .control_plane import authorize_post_freeze
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .post_freeze import compare_generic_to_market, compare_generic_to_street
from .records import MarketObservation
from .street import StreetGapDriver, StreetResearchReport
from .control_plane import StageStatus
from .valuation_execution import GenericValuationResult

StreetLoader = Callable[[OrchestratorContext], tuple[StreetResearchReport, ...]]
MarketLoader = Callable[[OrchestratorContext], MarketObservation]


def street_reference_load_adapter(*, loader: StreetLoader) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        if context.freeze_token is None:
            return StageExecutionResult(StageStatus.BLOCKED, "Street access requires IntrinsicFreezeToken", blocking=True)
        authorize_post_freeze(context.freeze_token, run_id=context.run_id)
        reports = tuple(loader(context))
        if not reports:
            return StageExecutionResult(StageStatus.WARNING, "no Street reports available after freeze", {"street_reports": ()})
        return StageExecutionResult(StageStatus.PASS, "Street references loaded after intrinsic freeze", {"street_reports": reports})
    return run


def street_gap_analyzer_adapter(*, drivers: tuple[StreetGapDriver, ...] = ()) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        if context.freeze_token is None:
            return StageExecutionResult(StageStatus.BLOCKED, "Street analysis requires IntrinsicFreezeToken", blocking=True)
        authorize_post_freeze(context.freeze_token, run_id=context.run_id)
        valuation = context.data.get("generic_valuation_result")
        reports = context.data.get("street_reports", ())
        if not isinstance(valuation, GenericValuationResult):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "generic valuation result missing", blocking=True)
        if not reports:
            return StageExecutionResult(StageStatus.SKIPPED_NOT_APPLICABLE, "no Street reports available", {"street_gap_analysis": None})
        bundle = compare_generic_to_street(valuation, tuple(reports), drivers=drivers)
        return StageExecutionResult(StageStatus.PASS, "Street gap analyzed against frozen intrinsic values", {"street_gap_analysis": bundle})
    return run


def market_price_load_adapter(*, loader: MarketLoader) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        if context.freeze_token is None:
            return StageExecutionResult(StageStatus.BLOCKED, "market price access requires IntrinsicFreezeToken", blocking=True)
        authorize_post_freeze(context.freeze_token, run_id=context.run_id)
        observation = loader(context)
        if not isinstance(observation, MarketObservation):
            return StageExecutionResult(StageStatus.BLOCKED, "market loader must return MarketObservation", blocking=True)
        return StageExecutionResult(StageStatus.PASS, "current market price loaded after intrinsic freeze", {"market_observation": observation})
    return run


def market_compare_adapter(*, currency: str) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        if context.freeze_token is None:
            return StageExecutionResult(StageStatus.BLOCKED, "market comparison requires IntrinsicFreezeToken", blocking=True)
        authorize_post_freeze(context.freeze_token, run_id=context.run_id)
        valuation = context.data.get("generic_valuation_result")
        observation = context.data.get("market_observation")
        if not isinstance(valuation, GenericValuationResult) or not isinstance(observation, MarketObservation):
            return StageExecutionResult(StageStatus.RECOVERY_REQUIRED, "valuation or market observation missing", blocking=True)
        bundle = compare_generic_to_market(valuation, observation, currency=currency)
        return StageExecutionResult(StageStatus.PASS, "market comparison completed after freeze", {"market_comparison": bundle})
    return run
