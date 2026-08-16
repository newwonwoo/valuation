from __future__ import annotations


_CONVERSIONS = {
    ("kMT", "kg"): 1_000_000.0,
    ("GW", "W"): 1_000_000_000.0,
    ("KRW_trillion", "KRW"): 1_000_000_000_000.0,
}


def convert(value: float, from_unit: str, to_unit: str) -> float:
    if from_unit == to_unit:
        return value
    try:
        factor = _CONVERSIONS[(from_unit, to_unit)]
    except KeyError as exc:
        raise ValueError(f"incompatible or unsupported units: {from_unit} -> {to_unit}") from exc
    return value * factor


def usd_to_krw(value_usd: float, fx_krw_per_usd: float) -> float:
    if fx_krw_per_usd <= 0:
        raise ValueError("FX must be positive")
    return value_usd * fx_krw_per_usd
