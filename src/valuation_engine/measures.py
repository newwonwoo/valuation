from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Iterable


class Dimension(str, Enum):
    MONEY = "money"
    MASS = "mass"
    POWER = "power"
    ENERGY = "energy"
    COUNT = "count"
    SHARES = "shares"
    TIME = "time"
    RATIO = "ratio"
    MULTIPLE = "multiple"
    PRICE_PER_UNIT = "price_per_unit"
    OTHER = "other"


@dataclass(frozen=True)
class PeriodRef:
    start: date
    end: date
    label: str = ""

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("period end cannot precede start")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class UnitDef:
    code: str
    dimension: Dimension
    base_code: str
    factor_to_base: Decimal

    def __post_init__(self) -> None:
        if not self.code or not self.base_code:
            raise ValueError("unit code and base_code are required")
        if not self.factor_to_base.is_finite() or self.factor_to_base <= 0:
            raise ValueError("factor_to_base must be finite and positive")


@dataclass(frozen=True)
class Measure:
    amount: Decimal
    unit: str
    as_of: date
    period: PeriodRef | None = None
    scope: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError("Measure.amount must be Decimal")
        if not self.amount.is_finite():
            raise ValueError("Measure.amount must be finite")
        if not self.unit:
            raise ValueError("Measure.unit is required")

    @classmethod
    def from_value(
        cls,
        value: str | int | float | Decimal,
        *,
        unit: str,
        as_of: date,
        period: PeriodRef | None = None,
        scope: str = "",
    ) -> "Measure":
        try:
            amount = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"invalid Decimal value: {value}") from exc
        return cls(amount, unit, as_of, period, scope)


@dataclass(frozen=True)
class ConversionTrace:
    source_unit: str
    target_unit: str
    factor: Decimal
    source_amount: Decimal
    output_amount: Decimal


class UnitRegistry:
    def __init__(self, definitions: Iterable[UnitDef] = ()) -> None:
        self._definitions: dict[str, UnitDef] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: UnitDef) -> None:
        if definition.code in self._definitions:
            raise ValueError(f"duplicate unit definition: {definition.code}")
        self._definitions[definition.code] = definition

    def get(self, code: str) -> UnitDef:
        try:
            return self._definitions[code]
        except KeyError as exc:
            raise ValueError(f"unknown unit: {code}") from exc

    def convert(self, measure: Measure, target_unit: str) -> tuple[Measure, ConversionTrace]:
        source = self.get(measure.unit)
        target = self.get(target_unit)
        if source.dimension is not target.dimension or source.base_code != target.base_code:
            raise ValueError(
                f"incompatible unit conversion: {source.code} ({source.dimension.value}) "
                f"→ {target.code} ({target.dimension.value})"
            )
        factor = source.factor_to_base / target.factor_to_base
        amount = measure.amount * factor
        converted = Measure(amount, target_unit, measure.as_of, measure.period, measure.scope)
        return converted, ConversionTrace(
            source.code,
            target.code,
            factor,
            measure.amount,
            amount,
        )

    def codes(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))


def default_unit_registry() -> UnitRegistry:
    D = Decimal
    definitions = (
        UnitDef("kg", Dimension.MASS, "kg", D("1")),
        UnitDef("metric_ton", Dimension.MASS, "kg", D("1000")),
        UnitDef("kMT", Dimension.MASS, "kg", D("1000000")),
        UnitDef("W", Dimension.POWER, "W", D("1")),
        UnitDef("kW", Dimension.POWER, "W", D("1000")),
        UnitDef("MW", Dimension.POWER, "W", D("1000000")),
        UnitDef("GW", Dimension.POWER, "W", D("1000000000")),
        UnitDef("Wh", Dimension.ENERGY, "Wh", D("1")),
        UnitDef("kWh", Dimension.ENERGY, "Wh", D("1000")),
        UnitDef("MWh", Dimension.ENERGY, "Wh", D("1000000")),
        UnitDef("GWh", Dimension.ENERGY, "Wh", D("1000000000")),
        UnitDef("KRW", Dimension.MONEY, "KRW", D("1")),
        UnitDef("KRW_thousand", Dimension.MONEY, "KRW", D("1000")),
        UnitDef("KRW_million", Dimension.MONEY, "KRW", D("1000000")),
        UnitDef("KRW_billion", Dimension.MONEY, "KRW", D("1000000000")),
        UnitDef("USD", Dimension.MONEY, "USD", D("1")),
        UnitDef("USD_thousand", Dimension.MONEY, "USD", D("1000")),
        UnitDef("USD_million", Dimension.MONEY, "USD", D("1000000")),
        UnitDef("USD_billion", Dimension.MONEY, "USD", D("1000000000")),
        UnitDef("count", Dimension.COUNT, "count", D("1")),
        UnitDef("shares", Dimension.SHARES, "shares", D("1")),
        UnitDef("days", Dimension.TIME, "days", D("1")),
        UnitDef("years", Dimension.TIME, "years", D("1")),
        UnitDef("ratio", Dimension.RATIO, "ratio", D("1")),
        UnitDef("multiple", Dimension.MULTIPLE, "multiple", D("1")),
    )
    return UnitRegistry(definitions)
