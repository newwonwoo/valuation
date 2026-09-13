"""Pre-audit research experiments; missing bounds are never numeric defaults."""
from dataclasses import dataclass
from decimal import Decimal
from itertools import product
from typing import Callable, Mapping


@dataclass(frozen=True)
class DriverRange:
    driver_id: str
    low: Decimal | None
    base: Decimal | None
    high: Decimal | None
    mandatory: bool = False
    rationale: str = ""

    def __post_init__(self):
        if not self.driver_id:
            raise ValueError("driver identity required")
        values = (self.low, self.base, self.high)
        if any(v is not None and (not isinstance(v, Decimal) or not v.is_finite()) for v in values):
            raise ValueError("bounds must be finite Decimal or None")
        if all(v is not None for v in values) and not self.low <= self.base <= self.high:
            raise ValueError("bounds must satisfy low <= base <= high")


@dataclass(frozen=True)
class ResearchSensitivity:
    driver_ids: tuple[str, ...]
    status: str
    mandatory: bool
    low_value: Decimal | None = None
    base_value: Decimal | None = None
    high_value: Decimal | None = None
    absolute_swing: Decimal | None = None
    pct_swing: Decimal | None = None
    rationale: str = ""
    corner_values: tuple[Decimal, ...] = ()


def assess_research_sensitivity(
    drivers: tuple[DriverRange, ...],
    evaluator: Callable[[Mapping[str, Decimal]], Decimal],
    *,
    pairs: tuple[tuple[str, str], ...] = (),
) -> tuple[ResearchSensitivity, ...]:
    by_id = {d.driver_id: d for d in drivers}
    if len(by_id) != len(drivers):
        raise ValueError("duplicate driver identity")
    seen = set()
    groups = [(d.driver_id,) for d in drivers]
    for pair in pairs:
        if len(pair) != 2 or len(set(pair)) != 2 or not set(pair) <= set(by_id):
            raise ValueError("pair must contain two distinct known drivers")
        canonical = tuple(sorted(pair))
        if canonical in seen:
            raise ValueError("duplicate pair")
        seen.add(canonical)
        groups.append(canonical)

    baseline = {d.driver_id: d.base for d in drivers if d.base is not None}

    def evaluate(overrides):
        value = evaluator({**baseline, **overrides})
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("evaluator must return a finite Decimal")
        return value

    rows = []
    for ids in groups:
        group = [by_id[key] for key in ids]
        mandatory = any(d.mandatory for d in group)
        rationale = "; ".join(d.rationale for d in group if d.rationale)
        if any(None in (d.low, d.base, d.high) for d in group):
            rows.append(ResearchSensitivity(ids, "NOT_YET_MEASURED", mandatory, rationale=rationale))
            continue
        try:
            low = evaluate({d.driver_id: d.low for d in group})
            base = evaluate({d.driver_id: d.base for d in group})
            high = evaluate({d.driver_id: d.high for d in group})
            corners = ()
            if len(group) == 2:
                corners = tuple(evaluate(dict(zip(ids, values))) for values in product(*[(d.low, d.high) for d in group]))
        except Exception as exc:
            # An internal research experiment cannot certify a value when its
            # evaluator fails. Preserve the fault for repair and continue peers.
            detail = f"{type(exc).__name__}: {exc}"
            rows.append(ResearchSensitivity(ids, "NOT_MEASURABLE", mandatory,
                rationale="; ".join(part for part in (rationale, detail) if part)))
            continue
        observed = (low, base, high, *corners)
        swing = max(observed) - min(observed)
        rows.append(ResearchSensitivity(ids, "MEASURED", mandatory, low, base, high, swing,
                                        swing / abs(base) if base != 0 else None, rationale, corners))
    return tuple(rows)


def rank_research_priorities(rows: tuple[ResearchSensitivity, ...]) -> tuple[ResearchSensitivity, ...]:
    return tuple(sorted(rows, key=lambda r: (
        not r.mandatory, r.absolute_swing is not None,
        -r.absolute_swing if r.absolute_swing is not None else Decimal(0), r.driver_ids,
    )))
