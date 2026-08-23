from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256

from .evaluator_registry import (
    EvaluatorRegistry,
    ModelKey,
    NormalizedMultipleEvaluator,
    ValuationRuntimeInputs,
    ValueKind,
)
from .scenario_binding import BoundScenarioSet
from .sotp import (
    ParentAdjustment,
    SegmentAggregationInput,
    ScenarioEquityAggregation,
    aggregate_scenario_equity_values,
    aggregate_sotp,
)


@dataclass(frozen=True)
class SegmentValuationPlan:
    asset_id: str
    segment_id: str
    model_key: ModelKey
    ownership_key: str
    ev_to_equity_adjustment_key: str | None

    def __post_init__(self) -> None:
        if not all((self.asset_id, self.segment_id, self.ownership_key)):
            raise ValueError("segment valuation plan requires asset, segment and ownership assumption key")


@dataclass(frozen=True)
class ParentAdjustmentPlan:
    asset_id: str
    assumption_key: str

    def __post_init__(self) -> None:
        if not self.asset_id or not self.assumption_key:
            raise ValueError("parent adjustment plan requires asset_id and assumption_key")


@dataclass(frozen=True)
class CompanyValuationPlan:
    segments: tuple[SegmentValuationPlan, ...]
    reporting_unit: str
    diluted_shares_key: str
    parent_adjustments: tuple[ParentAdjustmentPlan, ...] = ()

    def validate(self) -> None:
        if not self.segments:
            raise ValueError("company valuation plan requires segments")
        if not self.reporting_unit or not self.diluted_shares_key:
            raise ValueError("company valuation plan requires reporting unit and diluted-shares key")
        asset_ids = [item.asset_id for item in self.segments] + [item.asset_id for item in self.parent_adjustments]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("company valuation plan has duplicate asset IDs")


@dataclass(frozen=True)
class ScenarioPerShareValue:
    scenario_id: str
    equity_value_amount: Decimal
    reporting_unit: str
    diluted_shares: Decimal
    value_per_share: Decimal
    aggregation_hash: str
    economic_path_ids: tuple[str, ...]


@dataclass(frozen=True)
class GenericValuationResult:
    scenarios: tuple[ScenarioPerShareValue, ...]
    equity_aggregation: ScenarioEquityAggregation
    expected_value_per_share: Decimal | None
    reporting_unit: str
    valuation_hash: str



def default_evaluator_registry() -> EvaluatorRegistry:
    registry = EvaluatorRegistry()
    registry.register(NormalizedMultipleEvaluator("commodity_price_taker"))
    registry.register(NormalizedMultipleEvaluator("process_spread"))
    return registry


def execute_company_valuation(
    scenario_set: BoundScenarioSet,
    *,
    plan: CompanyValuationPlan,
    registry: EvaluatorRegistry,
    runtime_inputs: ValuationRuntimeInputs | None = None,
) -> GenericValuationResult:
    plan.validate()
    runtime = runtime_inputs or ValuationRuntimeInputs()
    scenario_company_values = []
    per_share_values: list[ScenarioPerShareValue] = []

    for scenario in scenario_set.scenarios:
        aggregation_inputs: list[SegmentAggregationInput] = []
        scenario_paths: list[str] = []
        for segment_plan in plan.segments:
            valuation = registry.evaluate(
                segment_plan.model_key,
                scenario,
                segment_id=segment_plan.segment_id,
                runtime_inputs=runtime,
            )
            scenario_paths.extend(valuation.economic_path_ids)
            ownership_assumption = scenario.get(segment_plan.ownership_key)
            scenario_paths.append(ownership_assumption.economic_path_id)
            ownership = ownership_assumption.measure.convert_to("ratio").amount
            if not Decimal("0") <= ownership <= Decimal("1"):
                raise ValueError(f"ownership assumption out of range for {segment_plan.segment_id}")

            adjustment = None
            if valuation.value_kind is ValueKind.ENTERPRISE_VALUE:
                if not segment_plan.ev_to_equity_adjustment_key:
                    raise ValueError(
                        f"segment {segment_plan.segment_id} produces enterprise value but has no EV-to-equity adjustment key"
                    )
                adjustment_assumption = scenario.get(segment_plan.ev_to_equity_adjustment_key)
                scenario_paths.append(adjustment_assumption.economic_path_id)
                adjustment = adjustment_assumption.measure.convert_to(plan.reporting_unit)

            aggregation_inputs.append(
                SegmentAggregationInput(
                    asset_id=segment_plan.asset_id,
                    valuation=valuation,
                    ownership_ratio=ownership,
                    ev_to_equity_adjustment=adjustment,
                )
            )

        parent_adjustments_list = []
        for item in plan.parent_adjustments:
            assumption = scenario.get(item.assumption_key)
            scenario_paths.append(assumption.economic_path_id)
            parent_adjustments_list.append(
                ParentAdjustment(item.asset_id, assumption.measure.convert_to(plan.reporting_unit))
            )
        parent_adjustments = tuple(parent_adjustments_list)

        company_value = aggregate_sotp(
            tuple(aggregation_inputs),
            scenario_id=scenario.scenario_id,
            reporting_unit=plan.reporting_unit,
            parent_adjustments=parent_adjustments,
        )
        scenario_company_values.append(company_value)

        shares_assumption = scenario.get(plan.diluted_shares_key)
        scenario_paths.append(shares_assumption.economic_path_id)
        diluted_shares = shares_assumption.measure.convert_to("shares").amount
        if diluted_shares <= 0:
            raise ValueError(f"diluted shares must be positive for {scenario.scenario_id}")
        equity_amount = company_value.equity_value.convert_to(plan.reporting_unit).amount
        per_share_values.append(
            ScenarioPerShareValue(
                scenario_id=scenario.scenario_id,
                equity_value_amount=equity_amount,
                reporting_unit=plan.reporting_unit,
                diluted_shares=diluted_shares,
                value_per_share=equity_amount / diluted_shares,
                aggregation_hash=company_value.aggregation_hash,
                economic_path_ids=tuple(dict.fromkeys(scenario_paths)),
            )
        )

    equity_aggregation = aggregate_scenario_equity_values(scenario_set, tuple(scenario_company_values))
    expected_per_share: Decimal | None = None
    if scenario_set.numeric_weighting_allowed:
        by_id = {item.scenario_id: item for item in per_share_values}
        expected_per_share = sum(
            (by_id[scenario.scenario_id].value_per_share * scenario.probability for scenario in scenario_set.scenarios),
            Decimal("0"),
        )

    serialized = "\n".join(
        [scenario_set.scenario_set_hash, plan.reporting_unit]
        + [
            f"{item.scenario_id}|{item.equity_value_amount}|{item.diluted_shares}|{item.value_per_share}|{item.aggregation_hash}|{','.join(item.economic_path_ids)}"
            for item in per_share_values
        ]
        + [
            "RUNTIME|" + item.key + "|" + str(item.measure.amount) + "|" + item.measure.unit + "|" + item.economic_path_id + "|" + ",".join(item.source_refs)
            for item in runtime.inputs
        ]
        + [f"expected={expected_per_share if expected_per_share is not None else 'NA'}"]
    )
    return GenericValuationResult(
        scenarios=tuple(per_share_values),
        equity_aggregation=equity_aggregation,
        expected_value_per_share=expected_per_share,
        reporting_unit=plan.reporting_unit,
        valuation_hash=sha256(serialized.encode("utf-8")).hexdigest(),
    )
