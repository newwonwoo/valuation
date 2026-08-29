"""End-to-end proof that the SOTP double-count guard holds for a genuine
two-segment company.

The generic cold-start decomposer (``classified_segment_decomposer``) only ever
emits ONE ``core`` segment, so the multi-segment SOTP path has never been
exercised by the generic cold run. These tests build a real two-segment
valuation from the SAME functions the runtime uses — real
``NormalizedMultipleEvaluator`` instances in a real ``EvaluatorRegistry``, real
``execute_company_valuation``, real ``aggregate_sotp`` — with no mocks, and:

1. two segments on distinct economic paths aggregate to the sum of parts;
2. two segments sharing one economic path fail closed end-to-end;
3. shared *evidence* between segments is legitimate — only a shared economic
   *path* double-counts (the guard keys on path, not evidence identity);
4. the per-segment EV-to-equity bridge, the company-level parent adjustment and
   the diluted-share division are all arithmetically correct across two
   segments.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption
from valuation_engine.evaluator_registry import (
    EvaluatorRegistry,
    ModelKey,
    NormalizedMultipleEvaluator,
    ValueKind,
)
from valuation_engine.records import CalibrationStatus
from valuation_engine.scenario_binding import BoundScenario, BoundScenarioSet
from valuation_engine.sotp import (
    SegmentAggregationInput,
    aggregate_sotp,
)
from valuation_engine.valuation_execution import (
    CompanyValuationPlan,
    IntrinsicValuationScope,
    ParentAdjustmentPlan,
    SegmentValuationPlan,
    execute_company_valuation,
)


# --------------------------------------------------------------------------- helpers


def assumption(
    key: str,
    value: str,
    unit: str,
    path: str,
    *,
    evidence_ids: tuple[str, ...] = (),
) -> CompiledAssumption:
    """A compiled assumption with an explicit economic path and evidence cite."""
    return CompiledAssumption(
        key=key,
        scenario_id="Base",
        measure=Measure(Decimal(value), unit, "2026-06-30"),
        bridge_id=f"B:{key}",
        evidence_ids=evidence_ids or (f"E:{key}",),
        hypothesis_id=f"H:{key}",
        economic_path_id=path,
        transform_id="identity_observation",
        input_evidence_hash=f"hash:{key}",
    )


def single_base_scenario_set(assumptions: tuple[CompiledAssumption, ...]) -> BoundScenarioSet:
    """A one-scenario, uncalibrated bound set — the SOTP path runs per scenario."""
    scenario = BoundScenario("Base", assumptions, None)
    return BoundScenarioSet(
        target_id="KR:DART:MULTISEG",
        scenarios=(scenario,),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        scenario_set_hash="S:MULTISEG",
    )


def two_segment_registry() -> EvaluatorRegistry:
    """Two REAL evaluators with distinct assumption keys, one per segment.

    Distinct EBITDA/multiple keys are what let two segments carry genuinely
    different economic paths; the generic runtime uses fixed keys because it
    only ever emits one segment.
    """
    registry = EvaluatorRegistry()
    registry.register(
        NormalizedMultipleEvaluator(
            "commodity_price_taker",
            ebitda_key="ebitda_a",
            multiple_key="multiple_a",
        )
    )
    registry.register(
        NormalizedMultipleEvaluator(
            "process_spread",
            ebitda_key="ebitda_b",
            multiple_key="multiple_b",
        )
    )
    return registry


def two_segment_plan() -> CompanyValuationPlan:
    return CompanyValuationPlan(
        segments=(
            SegmentValuationPlan(
                asset_id="asset:seg_a",
                segment_id="seg_a",
                model_key=ModelKey("commodity_price_taker", "normalized_multiple", "1"),
                ownership_key="own_a",
                ev_to_equity_adjustment_key="ev_a",
            ),
            SegmentValuationPlan(
                asset_id="asset:seg_b",
                segment_id="seg_b",
                model_key=ModelKey("process_spread", "normalized_multiple", "1"),
                ownership_key="own_b",
                ev_to_equity_adjustment_key="ev_b",
            ),
        ),
        reporting_unit="KRW_billion",
        diluted_shares_key="diluted_shares",
        parent_adjustments=(
            ParentAdjustmentPlan(asset_id="asset:parent_cash", assumption_key="parent_cash"),
        ),
    )


def base_assumptions(
    *,
    ebitda_a_path: str = "path:a:ebitda",
    ebitda_b_path: str = "path:b:ebitda",
    ebitda_a_evidence: tuple[str, ...] = (),
    ebitda_b_evidence: tuple[str, ...] = (),
) -> tuple[CompiledAssumption, ...]:
    """The full compiled-assumption set the two-segment plan consumes.

    Segment A: EBITDA 100 x multiple 8 = 800 EV; EV->equity -200; ownership 1.0.
    Segment B: EBITDA 50  x multiple 6 = 300 EV; EV->equity -50;  ownership 0.6.
    Parent cash bridge: +100. Diluted shares: 100.
    """
    return (
        assumption("ebitda_a", "100", "KRW_billion", ebitda_a_path, evidence_ids=ebitda_a_evidence),
        assumption("multiple_a", "8", "multiple", "path:a:multiple"),
        assumption("ebitda_b", "50", "KRW_billion", ebitda_b_path, evidence_ids=ebitda_b_evidence),
        assumption("multiple_b", "6", "multiple", "path:b:multiple"),
        assumption("own_a", "1.0", "ratio", "path:a:ownership"),
        assumption("own_b", "0.6", "ratio", "path:b:ownership"),
        assumption("ev_a", "-200", "KRW_billion", "path:a:ev_bridge"),
        assumption("ev_b", "-50", "KRW_billion", "path:b:ev_bridge"),
        assumption("parent_cash", "100", "KRW_billion", "path:parent:cash"),
        assumption("diluted_shares", "100", "shares", "path:company:shares"),
    )


# --------------------------------------------------------------------------- tests


def test_two_segments_distinct_paths_sum_to_parts_end_to_end():
    """(1)+(4) Real evaluators -> real SOTP -> per-share, with the full bridge math.

    Segment A equity  = (800 - 200) * 1.0 = 600
    Segment B equity  = (300 -  50) * 0.6 = 150
    Parent cash       = +100
    Combined equity   = 850 (KRW_billion)
    Value per share   = 850 / 100 diluted shares = 8.5
    """
    result = execute_company_valuation(
        single_base_scenario_set(base_assumptions()),
        plan=two_segment_plan(),
        registry=two_segment_registry(),
    )

    assert result.scope is IntrinsicValuationScope.FULL_INTRINSIC
    assert result.full_company_intrinsic_available is True
    assert len(result.scenarios) == 1
    scenario = result.scenarios[0]

    # Sum of parts, with each segment's EV->equity bridge applied before ownership
    # and the parent cash bridge added at the company level.
    assert scenario.equity_value_amount == Decimal("850")

    # (4) Value per share == combined equity / diluted shares, exactly.
    assert scenario.diluted_shares == Decimal("100")
    assert scenario.value_per_share == Decimal("8.5")
    assert scenario.value_per_share == scenario.equity_value_amount / scenario.diluted_shares

    # The company equity aggregation the SOTP returned agrees with the per-share row.
    company_value = result.equity_aggregation.scenario_values[0]
    assert company_value.equity_value.amount == Decimal("850")
    # Two operating segments + one parent adjustment => three aggregation components.
    assert len(company_value.components) == 3
    seg_a = next(c for c in company_value.components if c.asset_id == "asset:seg_a")
    seg_b = next(c for c in company_value.components if c.asset_id == "asset:seg_b")
    parent = next(c for c in company_value.components if c.asset_id == "asset:parent_cash")
    assert seg_a.attributable_equity_value.amount == Decimal("600")
    assert seg_b.attributable_equity_value.amount == Decimal("150")
    assert parent.attributable_equity_value.amount == Decimal("100")

    # Uncalibrated set => no numeric expected value is manufactured.
    assert result.expected_value_per_share is None


def test_shared_economic_path_across_segments_fails_closed_end_to_end():
    """(2) Two segments sharing ONE economic value path must fail closed.

    Runs through the full execute_company_valuation -> aggregate_sotp path, not a
    hand-built aggregation, so the guard is proven where the runtime hits it.
    """
    collision = "path:SHARED:ebitda"
    assumptions = base_assumptions(
        ebitda_a_path=collision,
        ebitda_b_path=collision,
    )
    with pytest.raises(ValueError, match="duplicate economic value path"):
        execute_company_valuation(
            single_base_scenario_set(assumptions),
            plan=two_segment_plan(),
            registry=two_segment_registry(),
        )


def test_shared_evidence_is_legitimate_only_shared_path_double_counts():
    """(3) The guard keys on economic PATH, not on evidence identity.

    Both segments' EBITDA assumptions cite the SAME macro evidence (e.g. a shared
    steel PPI series). That is legitimate: two segments may read the same filing
    or benchmark. As long as their economic paths differ, aggregation succeeds.
    Collapse the two onto a single path and the same shared evidence now genuinely
    double-counts, and the guard raises.
    """
    shared_evidence = ("E:INDSER:STEEL_PPI:202607",)

    # Shared evidence, DISTINCT paths -> no false positive.
    ok = execute_company_valuation(
        single_base_scenario_set(
            base_assumptions(
                ebitda_a_evidence=shared_evidence,
                ebitda_b_evidence=shared_evidence,
                ebitda_a_path="path:a:ebitda",
                ebitda_b_path="path:b:ebitda",
            )
        ),
        plan=two_segment_plan(),
        registry=two_segment_registry(),
    )
    assert ok.scenarios[0].equity_value_amount == Decimal("850")

    # Same shared evidence, now SAME path -> real double count -> fail closed.
    with pytest.raises(ValueError, match="duplicate economic value path"):
        execute_company_valuation(
            single_base_scenario_set(
                base_assumptions(
                    ebitda_a_evidence=shared_evidence,
                    ebitda_b_evidence=shared_evidence,
                    ebitda_a_path="path:collision:ebitda",
                    ebitda_b_path="path:collision:ebitda",
                )
            ),
            plan=two_segment_plan(),
            registry=two_segment_registry(),
        )


def test_aggregate_sotp_directly_rejects_duplicate_path_and_sums_distinct():
    """(2) direct: aggregate_sotp itself is the guard; prove it in isolation too.

    Both branches use REAL SegmentValuation objects produced by the real
    NormalizedMultipleEvaluator, so the direct call exercises the same objects the
    end-to-end path builds.
    """
    registry = two_segment_registry()
    scenario_ok = BoundScenario("Base", base_assumptions(), None)
    val_a = registry.evaluate(
        ModelKey("commodity_price_taker", "normalized_multiple", "1"),
        scenario_ok,
        segment_id="seg_a",
    )
    val_b = registry.evaluate(
        ModelKey("process_spread", "normalized_multiple", "1"),
        scenario_ok,
        segment_id="seg_b",
    )
    assert val_a.value_kind is ValueKind.ENTERPRISE_VALUE
    assert val_a.value.amount == Decimal("800")
    assert val_b.value.amount == Decimal("300")
    # Distinct economic paths aggregate cleanly.
    company = aggregate_sotp(
        (
            SegmentAggregationInput(
                "asset:seg_a", val_a, Decimal("1"),
                Measure(Decimal("-200"), "KRW_billion", "2026-06-30"),
            ),
            SegmentAggregationInput(
                "asset:seg_b", val_b, Decimal("0.6"),
                Measure(Decimal("-50"), "KRW_billion", "2026-06-30"),
            ),
        ),
        scenario_id="Base",
        reporting_unit="KRW_billion",
    )
    assert company.equity_value.amount == Decimal("750")  # 600 + 150, no parent

    # Now force a shared path between the two real valuations and confirm the raise.
    collided = BoundScenario(
        "Base",
        base_assumptions(ebitda_a_path="path:collision", ebitda_b_path="path:collision"),
        None,
    )
    col_a = registry.evaluate(
        ModelKey("commodity_price_taker", "normalized_multiple", "1"),
        collided,
        segment_id="seg_a",
    )
    col_b = registry.evaluate(
        ModelKey("process_spread", "normalized_multiple", "1"),
        collided,
        segment_id="seg_b",
    )
    with pytest.raises(ValueError, match="duplicate economic value path"):
        aggregate_sotp(
            (
                SegmentAggregationInput(
                    "asset:seg_a", col_a, Decimal("1"),
                    Measure(Decimal("-200"), "KRW_billion", "2026-06-30"),
                ),
                SegmentAggregationInput(
                    "asset:seg_b", col_b, Decimal("0.6"),
                    Measure(Decimal("-50"), "KRW_billion", "2026-06-30"),
                ),
            ),
            scenario_id="Base",
            reporting_unit="KRW_billion",
        )


def test_reused_adjustment_path_fails_closed_in_execution():
    """(3)/(4) The execution-level adjustment-path guard is independent of SOTP.

    If two segments' EV->equity bridges (or a segment bridge and the parent
    bridge) share an economic path, execute_company_valuation refuses before the
    per-share step — a distinct double-count axis from the SOTP value-path guard.
    """
    # Make ev_b reuse ev_a's economic path while leaving the value paths distinct.
    assumptions = tuple(
        assumption("ev_b", "-50", "KRW_billion", "path:a:ev_bridge")
        if a.key == "ev_b"
        else a
        for a in base_assumptions()
    )
    with pytest.raises(ValueError, match="reuses valuation adjustment economic paths"):
        execute_company_valuation(
            single_base_scenario_set(assumptions),
            plan=two_segment_plan(),
            registry=two_segment_registry(),
        )
