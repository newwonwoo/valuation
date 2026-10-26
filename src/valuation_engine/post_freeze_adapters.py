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
BrokerReferenceLoader = Callable[[], object]
BrokerOwnMetricsLoader = Callable[[str, str, str], tuple]
BrokerReviewOfficer = Callable[[object], object]
BrokerReviewContextLoader = Callable[[], object]


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
                # Empty input establishes only that this authorized export
                # supplies no eligible numeric targets, not that coverage is absent.
                return StageExecutionResult(
                    StageStatus.SKIPPED_NOT_APPLICABLE,
                    "no eligible numeric target-price references in the supplied authorized export; "
                    "Street reference withheld",
                    {"street_reports": ()},
                )
            if not all(isinstance(item, StreetResearchReport) for item in reports):
                raise ValueError("Street loader returned an invalid report object")
            # Estimates, base year and argument/basis notes affect interpretation.
            # Hash complete substantive reports, not merely their target prices.
            from .broker_comparison import broker_content_hash
            payload = broker_content_hash(reports)
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
                "street_reference_hash": payload,
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
                    "no eligible numeric target-price references in the supplied authorized export; "
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


def broker_reference_load_adapter(*, loader: BrokerReferenceLoader) -> StageAdapter:
    """Load richer references at the existing freeze boundary, never as Evidence."""
    def run(context: OrchestratorContext) -> StageExecutionResult:
        from .broker_comparison import BrokerReferenceBundle, broker_content_hash
        try:
            _require_post_freeze(context)
            bundle = loader()
            if not isinstance(bundle, BrokerReferenceBundle):
                raise TypeError("broker reference loader must return BrokerReferenceBundle")
            target_id = context.data.get("target_id")
            if target_id and bundle.target_id != target_id:
                raise ValueError("broker reference target differs from the frozen issuer")
            digest = broker_content_hash(bundle)
        except Exception as exc:
            return StageExecutionResult(StageStatus.BLOCKED,
                f"Broker reference load failed: {type(exc).__name__}: {exc}", blocking=True)
        return StageExecutionResult(StageStatus.PASS,
            "post-freeze broker reports and aggregate observations loaded with distinct provenance",
            {"broker_reference_bundle": bundle, "broker_reference_hash": digest,
             "street_reports": ()})
    return run


def broker_comparison_adapter(*, own_metrics_loader: BrokerOwnMetricsLoader | None = None) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        from .broker_comparison import BrokerReferenceBundle, build_broker_comparison
        try:
            _require_post_freeze(context)
            bundle = context.data.get("broker_reference_bundle")
            if not isinstance(bundle, BrokerReferenceBundle):
                raise TypeError("BrokerReferenceBundle is missing")
            valuation = context.data.get("distributional_primary_result") or context.data.get("generic_valuation_result")
            token = context.freeze_token
            metrics = own_metrics_loader(token.token_hash, token.valuation_hash, token.assumption_set_hash) if own_metrics_loader else ()
            result = build_broker_comparison(bundle, frozen_metrics=metrics,
                valuation=valuation, freeze_token_hash=token.token_hash,
                intrinsic_as_of=str(context.data.get("data_cutoff") or bundle.comparison_as_of))
        except Exception as exc:
            return StageExecutionResult(StageStatus.BLOCKED,
                f"Broker comparison failed: {type(exc).__name__}: {exc}", blocking=True)
        return StageExecutionResult(StageStatus.PASS,
            "broker forecast/basis comparisons completed; numeric target availability is recorded separately",
            {"broker_comparison": result, "broker_frozen_operating_metrics": metrics})
    return run


def broker_review_adapter(*, officer: BrokerReviewOfficer | None,
                          research_context_loader: BrokerReviewContextLoader | None = None) -> StageAdapter:
    """After MARKET_COMPARE, ask for reasoned proposals, never valuation authority."""
    def run(context: OrchestratorContext) -> StageExecutionResult:
        from .broker_comparison import canonical_broker_payload, broker_content_hash
        from .broker_judgment import (build_broker_judgment_request,
            validate_broker_judgment_response, build_broker_revision_work_order)
        from .runtime_authority import llm_proposal_scope
        try:
            _require_post_freeze(context)
            bundle = context.data.get("broker_reference_bundle")
            comparison = context.data.get("broker_comparison")
            if bundle is None or comparison is None:
                raise ValueError("post-freeze broker comparison is missing")
            combined = {"broker_comparison": canonical_broker_payload(comparison),
                "market_comparison": canonical_broker_payload(context.data.get("market_comparison")),
                "reverse_dcf_context": canonical_broker_payload(context.data.get("reverse_dcf_context")),
                "frozen_model_context": {
                    "investor_report_profile": canonical_broker_payload(context.data.get("investor_report_profile")),
                    "hypotheses": canonical_broker_payload(context.data.get("hypotheses", ())),
                    "red_team_proposal": canonical_broker_payload(context.data.get("red_team_proposal")),
                    "compiled_assumptions": canonical_broker_payload(context.data.get("compiled_assumption_set")),
                }}
            ledger = context.data.get("evidence_ledger")
            if ledger is not None and callable(getattr(ledger, "active", None)):
                combined["frozen_model_context"]["evidence"] = canonical_broker_payload(tuple(ledger.active()))
            if research_context_loader is not None:
                combined["followup_research_context"] = canonical_broker_payload(research_context_loader())
            combined["research_mode"] = context.data.get("research_mode", "BLIND_BASELINE")
            request = build_broker_judgment_request(target_id=bundle.target_id,
                intrinsic_as_of=comparison.intrinsic_as_of,
                comparison_as_of=bundle.comparison_as_of, run_id=context.run_id,
                freeze_token_hash=context.freeze_token.token_hash,
                comparison_payload=combined,
                references_payload=canonical_broker_payload(bundle))
            if officer is None:
                return StageExecutionResult(StageStatus.RECOVERY_REQUIRED,
                    "broker review requires a source-bound host proposal",
                    {"broker_review_request": request.to_dict()}, blocking=True)
            before = broker_content_hash(combined)
            with llm_proposal_scope():
                proposal = officer(request)
            if broker_content_hash(combined) != before:
                raise PermissionError("broker reviewer mutated its frozen comparison view")
            receipt = validate_broker_judgment_response(proposal, request)
            work_order = build_broker_revision_work_order(receipt)
        except Exception as exc:
            from .staff_transport import StaffWorkRequired
            if isinstance(exc, StaffWorkRequired):
                return StageExecutionResult(StageStatus.RECOVERY_REQUIRED,
                    str(exc), {"broker_review_request": request.to_dict()}, blocking=True)
            return StageExecutionResult(StageStatus.BLOCKED,
                f"Broker review failed: {type(exc).__name__}: {exc}", blocking=True)
        return StageExecutionResult(StageStatus.PASS,
            "source-bound broker/market discrepancy review validated; new-run research proposals do not mutate frozen value",
            {"broker_review_request": request.to_dict(), "broker_review_receipt": receipt,
             "broker_revision_work_order": work_order})
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
