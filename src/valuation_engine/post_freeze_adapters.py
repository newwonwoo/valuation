from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from typing import Callable

from .control_plane import StageStatus, authorize_post_freeze
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .post_freeze import compare_generic_to_market, compare_generic_to_street
from .records import MarketObservation
from .reverse_dcf import ReverseDCFPolicy, build_reverse_dcf_result
from .street import StreetGapDriver, StreetResearchReport
from .valuation_execution import GenericValuationResult, IntrinsicValuationScope


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
            if not isinstance(reports, tuple):
                raise ValueError("Street loader must return a tuple")
            if not reports:
                # A declared-empty authorized export: no coverage exists for
                # this target. Withhold the Street reference honestly rather
                # than blocking the run on a report nobody has written.
                return StageExecutionResult(
                    StageStatus.SKIPPED_NOT_APPLICABLE,
                    "authorized Street export declares no sell-side coverage; "
                    "Street reference withheld",
                    {"street_reports": ()},
                )
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
            if not isinstance(reports, tuple):
                raise ValueError("Street reports are missing")
            if not reports:
                return StageExecutionResult(
                    StageStatus.SKIPPED_NOT_APPLICABLE,
                    "no sell-side coverage was declared for this target; "
                    "there is no Street target to gap against",
                )
            if valuation.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC:
                reason = (
                    "PARTIAL_INTRINSIC is a valued-segment subtotal; whole-company Street target-price gap is withheld"
                )
                return StageExecutionResult(
                    StageStatus.SKIPPED_NOT_APPLICABLE,
                    reason,
                    {
                        "street_comparison_withheld_reason": reason,
                        "street_comparison_scope": valuation.scope.value,
                    },
                )
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
            if valuation.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC:
                reason = (
                    "PARTIAL_INTRINSIC is a valued-segment subtotal; whole-company current-price gap is withheld"
                )
                return StageExecutionResult(
                    StageStatus.SKIPPED_NOT_APPLICABLE,
                    reason,
                    {
                        "market_comparison_withheld_reason": reason,
                        "market_comparison_scope": valuation.scope.value,
                    },
                )
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


def reverse_dcf_expectations_adapter(
    *,
    policy: ReverseDCFPolicy | None = None,
) -> StageAdapter:
    """Derive market-implied expectations from the already-frozen intrinsic model.

    Doctrine keeps this strictly post-freeze and strictly read-only: the adapter is
    never blocking, because a frozen intrinsic result is final and a market
    observation may not retroactively invalidate it. Requirements the market is
    carrying are reported as non-blocking findings instead.
    """

    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            _require_post_freeze(context)
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"Reverse DCF requires a valid same-run Freeze Token: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        valuation = context.data.get("generic_valuation_result")
        observation = context.data.get("market_observation")
        currency = context.data.get("market_currency")
        if context.data.get("market_comparison") is None:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "전사 시장 비교가 성립하지 않아 시장 함의 기대치를 산출하지 않습니다",
                {
                    "reverse_dcf_withheld_reason": (
                        "market comparison is unavailable for this valuation scope"
                    )
                },
            )
        if not isinstance(valuation, GenericValuationResult):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "GenericValuationResult is missing before reverse DCF",
                blocking=True,
            )
        if not isinstance(observation, MarketObservation) or not isinstance(currency, str):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "MarketObservation and market currency are required before reverse DCF",
                blocking=True,
            )

        try:
            result = build_reverse_dcf_result(
                valuation=valuation,
                market_price=Decimal(str(observation.price)),
                market_as_of=observation.as_of,
                market_currency=currency,
                policy=policy,
            )
        except Exception as exc:
            # A reverse-DCF failure must never retract a frozen intrinsic result.
            return StageExecutionResult(
                StageStatus.WARNING,
                f"시장 함의 기대치 산출에 실패했습니다: {type(exc).__name__}: {exc}",
                {"reverse_dcf_withheld_reason": type(exc).__name__},
            )

        outputs = {
            "reverse_dcf_context": result,
            "reverse_dcf_result_hash": result.result_hash,
            "reverse_dcf_findings": result.findings,
        }
        if result.passed:
            return StageExecutionResult(
                StageStatus.PASS,
                "동결 모델을 고정한 채 시장 함의 영구성장률·현금흐름 배율·시나리오 위치를 역산했습니다",
                outputs,
            )
        if not any(item.reconstructed for item in result.scenarios):
            return StageExecutionResult(
                StageStatus.WARNING,
                "역산 가능한 단일 DCF 시나리오가 없어 시장 함의 영구성장률·현금흐름 배율을 산출하지 않았습니다",
                outputs,
            )
        return StageExecutionResult(
            StageStatus.WARNING,
            "시장 함의 기대치가 동결 가정과 다른 요구조건을 담고 있습니다: "
            + ", ".join(item.check for item in result.warnings),
            outputs,
        )

    return run
