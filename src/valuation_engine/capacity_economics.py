from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Mapping


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


@dataclass(frozen=True)
class CapacityPhysicalBridge:
    new_site_area_sqm: Decimal
    reference_site_area_sqm: Decimal
    reference_revenue_capacity: Decimal
    existing_product_area_share: Decimal
    effective_operating_ratio: Decimal
    uhv_nameplate_revenue_capacity: Decimal
    existing_product_nameplate_capacity: Decimal
    existing_product_effective_capacity: Decimal
    uhv_effective_capacity: Decimal
    total_effective_capacity: Decimal
    existing_product_mix: Decimal
    uhv_mix: Decimal


@dataclass(frozen=True)
class CapacityEconomicsYear:
    year: int
    base_revenue: Decimal
    existing_product_incremental_revenue: Decimal
    uhv_incremental_revenue: Decimal
    total_revenue: Decimal
    base_operating_profit: Decimal
    incremental_operating_profit: Decimal
    total_operating_profit: Decimal
    base_fcff: Decimal
    incremental_fcff: Decimal
    total_fcff: Decimal


@dataclass(frozen=True)
class CapacityEconomicsScenario:
    scenario_id: str
    existing_capacity_realization: Decimal
    uhv_capacity_realization: Decimal
    uhv_operating_margin: Decimal
    years: tuple[CapacityEconomicsYear, ...]

    @property
    def mature(self) -> CapacityEconomicsYear:
        return self.years[-1]


@dataclass(frozen=True)
class CapacityEconomicsResult:
    physical: CapacityPhysicalBridge
    scenarios: tuple[CapacityEconomicsScenario, ...]

    def scenario(self, scenario_id: str) -> CapacityEconomicsScenario:
        try:
            return next(row for row in self.scenarios if row.scenario_id == scenario_id)
        except StopIteration as exc:
            raise KeyError(scenario_id) from exc

    def as_primitive_dict(self) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, Decimal):
                return float(value)
            if isinstance(value, tuple):
                return [convert(item) for item in value]
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            return value

        return convert(asdict(self))


def build_capacity_economics(payload: Mapping[str, Any]) -> CapacityEconomicsResult:
    """Convert the frozen site/mix underwrite into annual revenue, OP and FCFF.

    All money values are KRW billion. Working capital is charged only on each
    year's revenue increase, so a one-time ramp investment is not capitalized in
    terminal value forever.
    """

    economics = payload["capacity_economics"]
    physical_input = economics["physical"]
    new_site_area = _decimal(physical_input["new_site_area_sqm"])
    reference_site_area = _decimal(physical_input["reference_site_area_sqm"])
    reference_capacity = _decimal(
        physical_input["reference_annual_revenue_capacity_krw_billion"]
    )
    existing_area_share = _decimal(
        physical_input["existing_product_area_share"]
    )
    operating_ratio = _decimal(physical_input["effective_operating_ratio"])
    uhv_nameplate = _decimal(
        physical_input["uhv_nameplate_revenue_capacity_krw_billion"]
    )
    existing_nameplate = (
        reference_capacity
        * new_site_area
        / reference_site_area
        * existing_area_share
    )
    existing_effective = existing_nameplate * operating_ratio
    uhv_effective = uhv_nameplate * operating_ratio
    total_effective = existing_effective + uhv_effective
    physical = CapacityPhysicalBridge(
        new_site_area_sqm=new_site_area,
        reference_site_area_sqm=reference_site_area,
        reference_revenue_capacity=reference_capacity,
        existing_product_area_share=existing_area_share,
        effective_operating_ratio=operating_ratio,
        uhv_nameplate_revenue_capacity=uhv_nameplate,
        existing_product_nameplate_capacity=existing_nameplate,
        existing_product_effective_capacity=existing_effective,
        uhv_effective_capacity=uhv_effective,
        total_effective_capacity=total_effective,
        existing_product_mix=existing_effective / total_effective,
        uhv_mix=uhv_effective / total_effective,
    )

    financial = economics["financial"]
    tax_rate = _decimal(financial["operating_tax_rate"])
    depreciation_rate = _decimal(financial["depreciation_rate_of_revenue"])
    maintenance_capex_rate = _decimal(
        financial["maintenance_capex_rate_of_revenue"]
    )
    working_capital_rate = _decimal(
        financial["incremental_working_capital_rate"]
    )
    opening_base_revenue = _decimal(
        financial["opening_base_revenue_krw_billion"]
    )

    scenarios: list[CapacityEconomicsScenario] = []
    for scenario_id, scenario_input in economics["scenarios"].items():
        base_revenues = tuple(
            _decimal(value)
            for value in scenario_input["base_revenue_krw_billion"]
        )
        base_margins = tuple(
            _decimal(value) for value in scenario_input["base_operating_margin"]
        )
        ramp = tuple(_decimal(value) for value in scenario_input["ramp_factor"])
        if not (len(base_revenues) == len(base_margins) == len(ramp) == 5):
            raise ValueError(
                f"{scenario_id} capacity economics requires five annual inputs"
            )
        if any(ramp[index] > ramp[index + 1] for index in range(4)):
            raise ValueError(f"{scenario_id} ramp factors must be non-decreasing")
        if ramp[-1] != Decimal("1"):
            raise ValueError(f"{scenario_id} final ramp factor must equal one")

        existing_realization = _decimal(
            scenario_input["existing_capacity_realization"]
        )
        uhv_realization = _decimal(scenario_input["uhv_capacity_realization"])
        uhv_margin = _decimal(scenario_input["uhv_operating_margin"])
        annual_rows: list[CapacityEconomicsYear] = []
        prior_base_revenue = opening_base_revenue
        prior_incremental_revenue = Decimal("0")

        for year, (base_revenue, base_margin, ramp_factor) in enumerate(
            zip(base_revenues, base_margins, ramp), start=1
        ):
            existing_revenue = (
                physical.existing_product_effective_capacity
                * existing_realization
                * ramp_factor
            )
            uhv_revenue = (
                physical.uhv_effective_capacity * uhv_realization * ramp_factor
            )
            incremental_revenue = existing_revenue + uhv_revenue
            total_revenue = base_revenue + incremental_revenue
            base_operating_profit = base_revenue * base_margin
            incremental_operating_profit = (
                existing_revenue * base_margin + uhv_revenue * uhv_margin
            )
            total_operating_profit = (
                base_operating_profit + incremental_operating_profit
            )

            base_revenue_increase = max(
                Decimal("0"), base_revenue - prior_base_revenue
            )
            incremental_revenue_increase = max(
                Decimal("0"), incremental_revenue - prior_incremental_revenue
            )
            base_fcff = (
                base_operating_profit * (Decimal("1") - tax_rate)
                + base_revenue * depreciation_rate
                - base_revenue * maintenance_capex_rate
                - base_revenue_increase * working_capital_rate
            )
            incremental_fcff = (
                incremental_operating_profit * (Decimal("1") - tax_rate)
                + incremental_revenue * depreciation_rate
                - incremental_revenue * maintenance_capex_rate
                - incremental_revenue_increase * working_capital_rate
            )
            annual_rows.append(
                CapacityEconomicsYear(
                    year=year,
                    base_revenue=base_revenue,
                    existing_product_incremental_revenue=existing_revenue,
                    uhv_incremental_revenue=uhv_revenue,
                    total_revenue=total_revenue,
                    base_operating_profit=base_operating_profit,
                    incremental_operating_profit=incremental_operating_profit,
                    total_operating_profit=total_operating_profit,
                    base_fcff=base_fcff,
                    incremental_fcff=incremental_fcff,
                    total_fcff=base_fcff + incremental_fcff,
                )
            )
            prior_base_revenue = base_revenue
            prior_incremental_revenue = incremental_revenue

        scenarios.append(
            CapacityEconomicsScenario(
                scenario_id=scenario_id,
                existing_capacity_realization=existing_realization,
                uhv_capacity_realization=uhv_realization,
                uhv_operating_margin=uhv_margin,
                years=tuple(annual_rows),
            )
        )

    return CapacityEconomicsResult(physical=physical, scenarios=tuple(scenarios))


def materialize_capacity_economics(payload: Mapping[str, Any]) -> None:
    result = build_capacity_economics(payload)
    scenarios = payload["scenarios"]
    for scenario in result.scenarios:
        row = scenarios[scenario.scenario_id]
        row["fcff_krw_billion"] = [
            float(year.base_fcff) for year in scenario.years
        ]
        row["uhv_reference_fcff_krw_billion"] = [
            float(year.incremental_fcff) for year in scenario.years
        ]
        row["uhv_steady_state_fcff_krw_billion"] = float(
            scenario.mature.incremental_fcff
        )
