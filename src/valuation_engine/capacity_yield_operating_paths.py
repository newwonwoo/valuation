"""Pure operating-path math for capacity/yield businesses.

The shared archetype covers businesses whose revenue is principally activity
or capacity multiplied by utilization and unit yield, while variable costs,
fixed operating leverage and asset reinvestment are modeled explicitly.  An
airline, shipping line, rail carrier or another transport operator supplies a
profile and metric mapping; the calculation contains no issuer/sector branch.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Mapping

from .dynamic_driver_distribution import DriverPath


ZERO = Decimal("0")
ONE = Decimal("1")
ECONOMIC_ARCHETYPE = "capacity_yield_levered"


class OperatingPathError(ValueError):
    """Raised when a capacity/yield path violates its economic contract."""


class CostBasis(str, Enum):
    CAPACITY = "CAPACITY"
    TRAFFIC = "TRAFFIC"
    FIXED = "FIXED"


def _decimal(value: Decimal, label: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{label} must be Decimal")
    if not value.is_finite():
        raise OperatingPathError(f"{label} must be finite")
    return value


@dataclass(frozen=True)
class CapacityYieldProfile:
    target_id: str
    economic_archetype: str
    reporting_currency: str
    accounting_basis: str
    active_module_ids: tuple[str, ...]
    non_capacity_segment_ids: tuple[str, ...]
    metric_mapping_version: str

    def validate(self) -> None:
        if (
            not self.target_id
            or self.economic_archetype != ECONOMIC_ARCHETYPE
            or not self.reporting_currency
            or not self.accounting_basis
            or not self.metric_mapping_version
        ):
            raise OperatingPathError("capacity/yield profile identity is incomplete")
        if not self.active_module_ids or len(set(self.active_module_ids)) != len(
            self.active_module_ids
        ):
            raise OperatingPathError("profile requires distinct active operating modules")
        if len(set(self.non_capacity_segment_ids)) != len(self.non_capacity_segment_ids):
            raise OperatingPathError("profile repeats a non-capacity segment")
        if set(self.active_module_ids).intersection(self.non_capacity_segment_ids):
            raise OperatingPathError("operating and non-capacity segments overlap")


@dataclass(frozen=True)
class CapacityYieldModuleMapping:
    module_id: str
    capacity_driver_id: str
    unit_yield_driver_id: str
    utilization_driver_id: str | None
    economic_path_id: str

    def validate(self) -> None:
        if not self.module_id or not self.capacity_driver_id or not self.unit_yield_driver_id:
            raise OperatingPathError("module mapping is incomplete")
        if not self.economic_path_id:
            raise OperatingPathError("module mapping requires an economic path ID")


@dataclass(frozen=True)
class VariableCostRule:
    rule_id: str
    module_id: str | None
    basis: CostBasis
    factor_driver_ids: tuple[str, ...]
    constant_factor: Decimal = ONE

    def validate(self, active_module_ids: tuple[str, ...]) -> None:
        if not self.rule_id or not self.factor_driver_ids:
            raise OperatingPathError("variable-cost rule identity is incomplete")
        if self.basis is CostBasis.FIXED and self.module_id is not None:
            raise OperatingPathError("fixed rule cannot name an activity module")
        if self.basis is not CostBasis.FIXED and self.module_id not in active_module_ids:
            raise OperatingPathError("activity cost rule names an inactive module")
        if len(self.factor_driver_ids) != len(set(self.factor_driver_ids)):
            raise OperatingPathError("variable-cost rule repeats a factor driver")
        _decimal(self.constant_factor, "cost constant factor")
        if self.constant_factor < ZERO:
            raise OperatingPathError("cost constant factor cannot be negative")


@dataclass(frozen=True)
class CapacityYieldMetricMapping:
    version: str
    modules: tuple[CapacityYieldModuleMapping, ...]
    variable_cost_rules: tuple[VariableCostRule, ...]
    fixed_cost_driver_ids: tuple[str, ...]
    depreciation_driver_id: str
    owned_capex_driver_id: str
    lease_additions_driver_id: str
    change_in_working_capital_driver_id: str

    def validate(self, profile: CapacityYieldProfile) -> None:
        if not self.version or self.version != profile.metric_mapping_version:
            raise OperatingPathError("profile and metric mapping versions differ")
        for item in self.modules:
            item.validate()
        module_ids = tuple(item.module_id for item in self.modules)
        if len(module_ids) != len(set(module_ids)):
            raise OperatingPathError("metric mapping repeats a module")
        if set(module_ids) != set(profile.active_module_ids):
            raise OperatingPathError(
                "metric mapping must contain exactly the profile's active modules"
            )
        if len(self.fixed_cost_driver_ids) != len(set(self.fixed_cost_driver_ids)):
            raise OperatingPathError("metric mapping repeats a fixed-cost driver")
        required_direct = (
            self.depreciation_driver_id,
            self.owned_capex_driver_id,
            self.lease_additions_driver_id,
            self.change_in_working_capital_driver_id,
        )
        if not all(required_direct):
            raise OperatingPathError("metric mapping is missing a reinvestment driver")
        for rule in self.variable_cost_rules:
            rule.validate(profile.active_module_ids)
        rule_ids = tuple(rule.rule_id for rule in self.variable_cost_rules)
        if len(rule_ids) != len(set(rule_ids)):
            raise OperatingPathError("metric mapping repeats a variable-cost rule")

    def required_driver_ids(self) -> tuple[str, ...]:
        required: list[str] = []
        for module in self.modules:
            required.extend((module.capacity_driver_id, module.unit_yield_driver_id))
            if module.utilization_driver_id is not None:
                required.append(module.utilization_driver_id)
        for rule in self.variable_cost_rules:
            required.extend(rule.factor_driver_ids)
        required.extend(self.fixed_cost_driver_ids)
        required.extend(
            (
                self.depreciation_driver_id,
                self.owned_capex_driver_id,
                self.lease_additions_driver_id,
                self.change_in_working_capital_driver_id,
            )
        )
        return tuple(dict.fromkeys(required))


@dataclass(frozen=True)
class OperatingPolicy:
    tax_rate: Decimal

    def validate(self) -> None:
        _decimal(self.tax_rate, "tax rate")
        if not ZERO <= self.tax_rate <= ONE:
            raise OperatingPathError("tax rate must lie within [0,1]")


@dataclass(frozen=True)
class ModulePeriodResult:
    module_id: str
    economic_path_id: str
    capacity: Decimal
    utilization: Decimal
    traffic: Decimal
    unit_yield: Decimal
    revenue: Decimal
    variable_cost: Decimal
    contribution_profit: Decimal


@dataclass(frozen=True)
class OperatingPeriodResult:
    period: int
    modules: tuple[ModulePeriodResult, ...]
    revenue: Decimal
    variable_cost: Decimal
    fixed_cost: Decimal
    ebitdar: Decimal
    depreciation: Decimal
    lease_adjusted_ebit: Decimal
    taxable_income_before_financing: Decimal
    cash_tax_before_financing: Decimal
    change_in_working_capital: Decimal
    owned_capex: Decimal
    lease_additions: Decimal
    economic_reinvestment: Decimal
    mandatory_capex: Decimal
    operating_cash_flow: Decimal
    unlevered_fcff: Decimal


@dataclass(frozen=True)
class CapacityYieldOperatingPath:
    target_id: str
    path_id: str
    reporting_currency: str
    periods: tuple[OperatingPeriodResult, ...]
    economic_path_ids: tuple[str, ...]


def evaluate_capacity_yield_path(
    *,
    profile: CapacityYieldProfile,
    metric_mapping: CapacityYieldMetricMapping,
    driver_path: DriverPath,
    policy: OperatingPolicy,
) -> CapacityYieldOperatingPath:
    """Evaluate one driver path without inspecting issuer or industry labels."""

    profile.validate()
    metric_mapping.validate(profile)
    policy.validate()
    drivers = driver_path.as_map()
    if set(metric_mapping.required_driver_ids()) - set(drivers):
        missing = sorted(set(metric_mapping.required_driver_ids()) - set(drivers))
        raise OperatingPathError("driver path is missing: " + ", ".join(missing))
    lengths = {len(drivers[driver_id]) for driver_id in metric_mapping.required_driver_ids()}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
        raise OperatingPathError("required driver paths must be non-empty and aligned")
    horizon = next(iter(lengths))
    for driver_id in metric_mapping.required_driver_ids():
        for value in drivers[driver_id]:
            _decimal(value, f"driver {driver_id}")

    module_mapping = {item.module_id: item for item in metric_mapping.modules}
    periods: list[OperatingPeriodResult] = []
    for period in range(horizon):
        modules: dict[str, ModulePeriodResult] = {}
        for module_id in profile.active_module_ids:
            mapping = module_mapping[module_id]
            capacity = drivers[mapping.capacity_driver_id][period]
            utilization = (
                ONE
                if mapping.utilization_driver_id is None
                else drivers[mapping.utilization_driver_id][period]
            )
            unit_yield = drivers[mapping.unit_yield_driver_id][period]
            if capacity < ZERO or not ZERO <= utilization <= ONE or unit_yield < ZERO:
                raise OperatingPathError(
                    f"module {module_id} capacity/yield must be non-negative and utilization in [0,1]"
                )
            traffic = capacity * utilization
            revenue = traffic * unit_yield
            modules[module_id] = ModulePeriodResult(
                module_id=module_id,
                economic_path_id=mapping.economic_path_id,
                capacity=capacity,
                utilization=utilization,
                traffic=traffic,
                unit_yield=unit_yield,
                revenue=revenue,
                variable_cost=ZERO,
                contribution_profit=revenue,
            )

        fixed_rule_cost = ZERO
        variable_cost_by_module = {module_id: ZERO for module_id in modules}
        for rule in metric_mapping.variable_cost_rules:
            factor = rule.constant_factor
            for driver_id in rule.factor_driver_ids:
                driver_value = drivers[driver_id][period]
                if driver_value < ZERO:
                    raise OperatingPathError(
                        f"cost rule {rule.rule_id} contains a negative factor"
                    )
                factor *= driver_value
            if factor < ZERO:
                raise OperatingPathError(f"cost rule {rule.rule_id} produced a negative factor")
            if rule.basis is CostBasis.FIXED:
                fixed_rule_cost += factor
                continue
            assert rule.module_id is not None
            module = modules[rule.module_id]
            activity = module.capacity if rule.basis is CostBasis.CAPACITY else module.traffic
            variable_cost_by_module[rule.module_id] += activity * factor

        finalized_modules: list[ModulePeriodResult] = []
        for module_id in profile.active_module_ids:
            module = modules[module_id]
            cost = variable_cost_by_module[module_id]
            finalized_modules.append(
                ModulePeriodResult(
                    module_id=module.module_id,
                    economic_path_id=module.economic_path_id,
                    capacity=module.capacity,
                    utilization=module.utilization,
                    traffic=module.traffic,
                    unit_yield=module.unit_yield,
                    revenue=module.revenue,
                    variable_cost=cost,
                    contribution_profit=module.revenue - cost,
                )
            )
        revenue = sum((item.revenue for item in finalized_modules), ZERO)
        variable_cost = sum((item.variable_cost for item in finalized_modules), ZERO)
        fixed_cost_values = tuple(
            drivers[driver_id][period] for driver_id in metric_mapping.fixed_cost_driver_ids
        )
        if any(value < ZERO for value in fixed_cost_values):
            raise OperatingPathError("fixed-cost drivers cannot be negative")
        fixed_cost = fixed_rule_cost + sum(fixed_cost_values, ZERO)
        depreciation = drivers[metric_mapping.depreciation_driver_id][period]
        owned_capex = drivers[metric_mapping.owned_capex_driver_id][period]
        lease_additions = drivers[metric_mapping.lease_additions_driver_id][period]
        change_nwc = drivers[metric_mapping.change_in_working_capital_driver_id][period]
        if any(value < ZERO for value in (fixed_cost, depreciation, owned_capex, lease_additions)):
            raise OperatingPathError("cost, depreciation and reinvestment cannot be negative")
        ebitdar = revenue - variable_cost - fixed_cost
        ebit = ebitdar - depreciation
        cash_tax = max(ebit, ZERO) * policy.tax_rate
        # Full asset additions belong in unlevered reinvestment.  Only the cash
        # purchase portion enters levered liquidity; the lease liability and
        # subsequent lease payments are handled by the financing schedule.
        economic_reinvestment = owned_capex + lease_additions
        mandatory_capex = owned_capex
        operating_cash_flow = ebitdar - cash_tax - change_nwc
        unlevered_fcff = (
            ebit - cash_tax + depreciation - economic_reinvestment - change_nwc
        )
        periods.append(
            OperatingPeriodResult(
                period=period + 1,
                modules=tuple(finalized_modules),
                revenue=revenue,
                variable_cost=variable_cost,
                fixed_cost=fixed_cost,
                ebitdar=ebitdar,
                depreciation=depreciation,
                lease_adjusted_ebit=ebit,
                taxable_income_before_financing=ebit,
                cash_tax_before_financing=cash_tax,
                change_in_working_capital=change_nwc,
                owned_capex=owned_capex,
                lease_additions=lease_additions,
                economic_reinvestment=economic_reinvestment,
                mandatory_capex=mandatory_capex,
                operating_cash_flow=operating_cash_flow,
                unlevered_fcff=unlevered_fcff,
            )
        )
    economic_paths = tuple(module_mapping[item].economic_path_id for item in profile.active_module_ids)
    if len(economic_paths) != len(set(economic_paths)):
        raise OperatingPathError("active modules must use distinct economic path IDs")
    return CapacityYieldOperatingPath(
        target_id=profile.target_id,
        path_id=driver_path.path_id,
        reporting_currency=profile.reporting_currency,
        periods=tuple(periods),
        economic_path_ids=economic_paths,
    )


def evaluate_capacity_yield_paths(
    *,
    profile: CapacityYieldProfile,
    metric_mapping: CapacityYieldMetricMapping,
    driver_paths: tuple[DriverPath, ...],
    policy: OperatingPolicy,
) -> tuple[CapacityYieldOperatingPath, ...]:
    if not driver_paths:
        raise OperatingPathError("at least one driver path is required")
    path_ids = tuple(item.path_id for item in driver_paths)
    if len(path_ids) != len(set(path_ids)):
        raise OperatingPathError("driver paths contain duplicate IDs")
    return tuple(
        evaluate_capacity_yield_path(
            profile=profile,
            metric_mapping=metric_mapping,
            driver_path=path,
            policy=policy,
        )
        for path in driver_paths
    )


__all__ = [
    "CapacityYieldMetricMapping",
    "CapacityYieldModuleMapping",
    "CapacityYieldOperatingPath",
    "CapacityYieldProfile",
    "CostBasis",
    "ECONOMIC_ARCHETYPE",
    "ModulePeriodResult",
    "OperatingPathError",
    "OperatingPeriodResult",
    "OperatingPolicy",
    "VariableCostRule",
    "evaluate_capacity_yield_path",
    "evaluate_capacity_yield_paths",
]
