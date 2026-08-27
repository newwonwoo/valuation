from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum


class Dimension(str, Enum):
    MONEY = "money"
    MASS = "mass"
    POWER = "power"
    AREA = "area"
    COUNT = "count"
    SHARES = "shares"
    TIME = "time"
    RATIO = "ratio"
    MULTIPLE = "multiple"
    DIMENSIONLESS = "dimensionless"


@dataclass(frozen=True)
class UnitDef:
    code: str
    dimension: Dimension
    base_code: str
    factor_to_base: Decimal


_UNIT_DEFS = {
    "KRW": UnitDef("KRW", Dimension.MONEY, "KRW", Decimal("1")),
    "KRW_million": UnitDef("KRW_million", Dimension.MONEY, "KRW", Decimal("1000000")),
    "KRW_billion": UnitDef("KRW_billion", Dimension.MONEY, "KRW", Decimal("1000000000")),
    "USD": UnitDef("USD", Dimension.MONEY, "USD", Decimal("1")),
    "USD_million": UnitDef("USD_million", Dimension.MONEY, "USD", Decimal("1000000")),
    "kg": UnitDef("kg", Dimension.MASS, "kg", Decimal("1")),
    "kMT": UnitDef("kMT", Dimension.MASS, "kg", Decimal("1000000")),
    "W": UnitDef("W", Dimension.POWER, "W", Decimal("1")),
    "MW": UnitDef("MW", Dimension.POWER, "W", Decimal("1000000")),
    "GW": UnitDef("GW", Dimension.POWER, "W", Decimal("1000000000")),
    "sqm": UnitDef("sqm", Dimension.AREA, "sqm", Decimal("1")),
    "pyeong": UnitDef(
        "pyeong",
        Dimension.AREA,
        "sqm",
        Decimal("3.305785123966942148760330579"),
    ),
    "count": UnitDef("count", Dimension.COUNT, "count", Decimal("1")),
    "shares": UnitDef("shares", Dimension.SHARES, "shares", Decimal("1")),
    "days": UnitDef("days", Dimension.TIME, "days", Decimal("1")),
    "years": UnitDef("years", Dimension.TIME, "days", Decimal("365.25")),
    "ratio": UnitDef("ratio", Dimension.RATIO, "ratio", Decimal("1")),
    "%": UnitDef("%", Dimension.RATIO, "ratio", Decimal("0.01")),
    "multiple": UnitDef("multiple", Dimension.MULTIPLE, "multiple", Decimal("1")),
    "dimensionless": UnitDef("dimensionless", Dimension.DIMENSIONLESS, "dimensionless", Decimal("1")),
}


def unit_def(code: str) -> UnitDef:
    try:
        return _UNIT_DEFS[code]
    except KeyError as exc:
        raise ValueError(f"unsupported unit: {code}") from exc


def to_decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric measure")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"non-numeric measure: {value!r}") from exc
    if not result.is_finite():
        raise ValueError("measure must be finite")
    return result


@dataclass(frozen=True)
class Measure:
    amount: Decimal
    unit: str
    as_of: str

    def __post_init__(self) -> None:
        if not self.as_of:
            raise ValueError("measure requires as_of")
        if not self.amount.is_finite():
            raise ValueError("measure amount must be finite")
        unit_def(self.unit)

    @property
    def dimension(self) -> Dimension:
        return unit_def(self.unit).dimension

    def to_base(self) -> "Measure":
        definition = unit_def(self.unit)
        return Measure(self.amount * definition.factor_to_base, definition.base_code, self.as_of)

    def convert_to(self, target_unit: str) -> "Measure":
        source = unit_def(self.unit)
        target = unit_def(target_unit)
        if source.dimension is not target.dimension:
            raise ValueError(f"unit dimension mismatch: {self.unit} -> {target_unit}")
        if source.base_code != target.base_code:
            raise ValueError(
                f"currency/base conversion requires an explicit FX transform: {self.unit} -> {target_unit}"
            )
        base_amount = self.amount * source.factor_to_base
        return Measure(base_amount / target.factor_to_base, target_unit, self.as_of)


def measure_from_raw(value: object, unit: str, as_of: str) -> Measure:
    return Measure(to_decimal(value), unit, as_of)
