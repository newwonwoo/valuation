from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256

from .actual_units import Measure
from .evaluator_registry import (
    SegmentValuation,
    SegmentValuationDiagnostics,
    ValueKind,
)
from .scenario_binding import BoundScenarioSet


@dataclass(frozen=True)
class SegmentAggregationInput:
    asset_id: str
    valuation: SegmentValuation
    ownership_ratio: Decimal
    ev_to_equity_adjustment: Measure | None = None

    def validate(self) -> None:
        if not self.asset_id:
            raise ValueError("aggregation input requires asset_id")
        if not Decimal("0") <= self.ownership_ratio <= Decimal("1"):
            raise ValueError("ownership_ratio must be within [0, 1]")
        if self.valuation.value_kind is ValueKind.ENTERPRISE_VALUE and self.ev_to_equity_adjustment is None:
            raise ValueError("enterprise-value contribution requires explicit EV-to-equity adjustment, including explicit zero")
        if self.valuation.value_kind is ValueKind.EQUITY_VALUE and self.ev_to_equity_adjustment is not None:
            if self.ev_to_equity_adjustment.amount != 0:
                raise ValueError("equity-value contribution cannot take a non-zero EV-to-equity adjustment")


@dataclass(frozen=True)
class ParentAdjustment:
    asset_id: str
    amount: Measure
    economic_path_id: str

    def __post_init__(self) -> None:
        if not self.asset_id or not self.economic_path_id:
            raise ValueError(
                "parent adjustment requires asset_id and economic_path_id"
            )


@dataclass(frozen=True)
class AggregationComponent:
    asset_id: str
    contribution_id: str
    attributable_equity_value: Measure
    economic_path_ids: tuple[str, ...]
    ownership_ratio: Decimal | None = None
    diagnostics: SegmentValuationDiagnostics | None = None


@dataclass(frozen=True)
class CompanyScenarioEquityValue:
    scenario_id: str
    equity_value: Measure
    components: tuple[AggregationComponent, ...]
    aggregation_hash: str


@dataclass(frozen=True)
class ScenarioEquityAggregation:
    scenario_values: tuple[CompanyScenarioEquityValue, ...]
    expected_equity_value: Measure | None
    numeric_weighting_allowed: bool


def aggregate_sotp(
    inputs: tuple[SegmentAggregationInput, ...],
    *,
    scenario_id: str,
    reporting_unit: str,
    parent_adjustments: tuple[ParentAdjustment, ...] = (),
) -> CompanyScenarioEquityValue:
    if not inputs:
        raise ValueError("SOTP requires at least one segment contribution")
    if not scenario_id or not reporting_unit:
        raise ValueError("SOTP requires scenario_id and reporting_unit")

    asset_ids: set[str] = set()
    contribution_ids: set[str] = set()
    economic_paths: set[str] = set()
    components: list[AggregationComponent] = []
    total = Decimal("0")
    as_of = ""

    for item in inputs:
        item.validate()
        valuation = item.valuation
        if valuation.scenario_id != scenario_id:
            raise ValueError("all SOTP contributions must match scenario_id")
        if item.asset_id in asset_ids:
            raise ValueError(f"duplicate SOTP asset_id: {item.asset_id}")
        if valuation.contribution_id in contribution_ids:
            raise ValueError(f"duplicate valuation contribution_id: {valuation.contribution_id}")
        duplicate_paths = economic_paths.intersection(valuation.economic_path_ids)
        if duplicate_paths:
            raise ValueError(f"duplicate economic value path in SOTP: {sorted(duplicate_paths)}")
        asset_ids.add(item.asset_id)
        contribution_ids.add(valuation.contribution_id)
        economic_paths.update(valuation.economic_path_ids)

        base_value = valuation.value.convert_to(reporting_unit)
        if valuation.value_kind is ValueKind.ENTERPRISE_VALUE:
            adjustment = item.ev_to_equity_adjustment.convert_to(reporting_unit)
            full_equity = base_value.amount + adjustment.amount
            as_of = max(as_of, base_value.as_of, adjustment.as_of)
        else:
            full_equity = base_value.amount
            as_of = max(as_of, base_value.as_of)
        attributable = full_equity * item.ownership_ratio
        component_measure = Measure(attributable, reporting_unit, as_of)
        components.append(
            AggregationComponent(
                asset_id=item.asset_id,
                contribution_id=valuation.contribution_id,
                attributable_equity_value=component_measure,
                economic_path_ids=valuation.economic_path_ids,
                ownership_ratio=item.ownership_ratio,
                diagnostics=valuation.diagnostics,
            )
        )
        total += attributable

    for adjustment in parent_adjustments:
        if adjustment.asset_id in asset_ids:
            raise ValueError(f"parent adjustment duplicates SOTP asset_id: {adjustment.asset_id}")
        if adjustment.economic_path_id in economic_paths:
            raise ValueError(
                "duplicate economic value path in SOTP: "
                f"{[adjustment.economic_path_id]}"
            )
        asset_ids.add(adjustment.asset_id)
        economic_paths.add(adjustment.economic_path_id)
        amount = adjustment.amount.convert_to(reporting_unit)
        as_of = max(as_of, amount.as_of)
        total += amount.amount
        components.append(
            AggregationComponent(
                asset_id=adjustment.asset_id,
                contribution_id=f"parent:{adjustment.asset_id}",
                attributable_equity_value=amount,
                economic_path_ids=(adjustment.economic_path_id,),
            )
        )

    digest_payload = "\n".join(
        [scenario_id, reporting_unit]
        + [
            f"{item.asset_id}|{item.contribution_id}|{item.attributable_equity_value.amount}|{','.join(item.economic_path_ids)}"
            for item in components
        ]
    )
    return CompanyScenarioEquityValue(
        scenario_id=scenario_id,
        equity_value=Measure(total, reporting_unit, as_of),
        components=tuple(components),
        aggregation_hash=sha256(digest_payload.encode("utf-8")).hexdigest(),
    )


def aggregate_scenario_equity_values(
    scenario_set: BoundScenarioSet,
    values: tuple[CompanyScenarioEquityValue, ...],
) -> ScenarioEquityAggregation:
    by_id = {item.scenario_id: item for item in values}
    if len(by_id) != len(values):
        raise ValueError("duplicate company scenario valuation")
    expected_ids = tuple(item.scenario_id for item in scenario_set.scenarios)
    if set(by_id) != set(expected_ids):
        raise ValueError("company scenario values must exactly cover the bound scenario set")

    ordered = tuple(by_id[scenario_id] for scenario_id in expected_ids)
    reporting_unit = ordered[0].equity_value.unit
    for item in ordered[1:]:
        item.equity_value.convert_to(reporting_unit)

    if not scenario_set.numeric_weighting_allowed:
        return ScenarioEquityAggregation(ordered, None, False)

    expected = Decimal("0")
    as_of = ""
    for scenario in scenario_set.scenarios:
        if scenario.probability is None:
            raise ValueError("numeric weighting allowed but a scenario probability is missing")
        value = by_id[scenario.scenario_id].equity_value.convert_to(reporting_unit)
        expected += value.amount * scenario.probability
        as_of = max(as_of, value.as_of)
    return ScenarioEquityAggregation(
        ordered,
        Measure(expected, reporting_unit, as_of),
        True,
    )
