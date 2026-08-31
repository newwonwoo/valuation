from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol

from .actual_units import Measure
from .scenario_binding import BoundScenario


class ValueKind(str, Enum):
    ENTERPRISE_VALUE = "enterprise_value"
    EQUITY_VALUE = "equity_value"


@dataclass(frozen=True)
class ModelKey:
    archetype: str
    method: str
    version: str

    def __post_init__(self) -> None:
        if not self.archetype or not self.method or not self.version:
            raise ValueError("model key requires archetype, method and version")


@dataclass(frozen=True)
class SegmentValuationDiagnostics:
    """Exact discounting decomposition published by a DCF-family evaluator.

    Post-freeze reverse-DCF must never re-infer an evaluator's internal kernel from
    compiled assumption keys. The evaluator that performed the discounting is the only
    authority on how its own value was built, so it publishes the decomposition here.
    """

    execution_family: str
    value_unit: str
    discount_rate: Decimal
    forecast_years: int
    fcff_path: tuple[Decimal, ...]
    present_value_explicit: Decimal
    present_value_terminal: Decimal
    terminal_growth: Decimal
    terminal_roic: Decimal

    def validate(self) -> None:
        if not self.execution_family or not self.value_unit:
            raise ValueError("valuation diagnostics require execution family and unit")
        if self.forecast_years < 1 or len(self.fcff_path) != self.forecast_years:
            raise ValueError("valuation diagnostics FCFF path must match forecast_years")
        for name, value in (
            ("discount_rate", self.discount_rate),
            ("present_value_explicit", self.present_value_explicit),
            ("present_value_terminal", self.present_value_terminal),
            ("terminal_growth", self.terminal_growth),
            ("terminal_roic", self.terminal_roic),
        ):
            if not value.is_finite():
                raise ValueError(f"valuation diagnostics {name} must be finite")
        if self.discount_rate <= self.terminal_growth:
            raise ValueError("valuation diagnostics require discount rate above terminal growth")
        if self.terminal_roic <= 0:
            raise ValueError("valuation diagnostics require positive terminal ROIC")

    @property
    def enterprise_value(self) -> Decimal:
        return self.present_value_explicit + self.present_value_terminal

    @property
    def terminal_fcff(self) -> Decimal:
        return self.fcff_path[-1]


@dataclass(frozen=True)
class SegmentValuation:
    contribution_id: str
    segment_id: str
    scenario_id: str
    value_kind: ValueKind
    value: Measure
    economic_path_ids: tuple[str, ...]
    evaluator_id: str
    evaluator_version: str
    diagnostics: SegmentValuationDiagnostics | None = None

    def __post_init__(self) -> None:
        if not all((self.contribution_id, self.segment_id, self.scenario_id, self.evaluator_id, self.evaluator_version)):
            raise ValueError("segment valuation requires identity, segment, scenario and evaluator")
        if not self.economic_path_ids:
            raise ValueError("segment valuation requires economic path trace")


class DeterministicEvaluator(Protocol):
    key: ModelKey
    evaluator_id: str
    required_assumption_keys: tuple[str, ...]

    def evaluate(self, scenario: BoundScenario, *, segment_id: str) -> SegmentValuation: ...


class EvaluatorRegistry:
    def __init__(self) -> None:
        self._evaluators: dict[ModelKey, DeterministicEvaluator] = {}
        self._segment_evaluators: dict[tuple[str, ModelKey], DeterministicEvaluator] = {}

    def register(
        self,
        evaluator: DeterministicEvaluator,
        *,
        segment_id: str | None = None,
    ) -> None:
        if not evaluator.required_assumption_keys:
            raise ValueError("evaluator must declare required assumptions")
        if segment_id is None:
            if evaluator.key in self._evaluators:
                raise ValueError(f"duplicate evaluator registration: {evaluator.key}")
            self._evaluators[evaluator.key] = evaluator
            return
        if not segment_id:
            raise ValueError("segment-scoped evaluator registration requires segment_id")
        scoped_key = (segment_id, evaluator.key)
        if scoped_key in self._segment_evaluators:
            raise ValueError(
                f"duplicate evaluator registration for segment {segment_id}: {evaluator.key}"
            )
        self._segment_evaluators[scoped_key] = evaluator

    def get(
        self,
        key: ModelKey,
        *,
        segment_id: str | None = None,
    ) -> DeterministicEvaluator:
        if segment_id is not None:
            scoped = self._segment_evaluators.get((segment_id, key))
            if scoped is not None:
                return scoped
        try:
            return self._evaluators[key]
        except KeyError as exc:
            scope = f" for segment {segment_id}" if segment_id is not None else ""
            raise KeyError(f"no exact evaluator registered for {key}{scope}") from exc

    def evaluate(self, key: ModelKey, scenario: BoundScenario, *, segment_id: str) -> SegmentValuation:
        evaluator = self.get(key, segment_id=segment_id)
        missing = tuple(key for key in evaluator.required_assumption_keys if not _has_assumption(scenario, key))
        if missing:
            raise ValueError(
                f"evaluator {evaluator.evaluator_id} missing assumptions for {scenario.scenario_id}: {', '.join(missing)}"
            )
        return evaluator.evaluate(scenario, segment_id=segment_id)

    def keys(self) -> tuple[ModelKey, ...]:
        keys = set(self._evaluators)
        keys.update(key for _, key in self._segment_evaluators)
        return tuple(sorted(keys, key=lambda item: (item.archetype, item.method, item.version)))

    def keys_for_segment(self, segment_id: str) -> tuple[ModelKey, ...]:
        if not segment_id:
            raise ValueError("segment evaluator lookup requires segment_id")
        keys = set(self._evaluators)
        keys.update(
            key
            for scoped_segment_id, key in self._segment_evaluators
            if scoped_segment_id == segment_id
        )
        return tuple(
            sorted(
                keys,
                key=lambda item: (item.archetype, item.method, item.version),
            )
        )

    def has_scoped_registrations(self) -> bool:
        return bool(self._segment_evaluators)

    def registration_items(
        self,
    ) -> tuple[tuple[str | None, ModelKey, DeterministicEvaluator], ...]:
        rows = [
            (None, key, evaluator)
            for key, evaluator in self._evaluators.items()
        ]
        rows.extend(
            (segment_id, key, evaluator)
            for (segment_id, key), evaluator in self._segment_evaluators.items()
        )
        return tuple(
            sorted(
                rows,
                key=lambda item: (
                    item[0] or "",
                    item[1].archetype,
                    item[1].method,
                    item[1].version,
                ),
            )
        )


def _has_assumption(scenario: BoundScenario, key: str) -> bool:
    try:
        scenario.get(key)
        return True
    except KeyError:
        return False


@dataclass(frozen=True)
class NormalizedMultipleEvaluator:
    archetype: str
    ebitda_key: str = "normalized_ebitda"
    multiple_key: str = "normalized_multiple"
    version: str = "1"

    @property
    def key(self) -> ModelKey:
        return ModelKey(self.archetype, "normalized_multiple", self.version)

    @property
    def evaluator_id(self) -> str:
        return f"{self.archetype}.normalized_multiple"

    @property
    def required_assumption_keys(self) -> tuple[str, ...]:
        return (self.ebitda_key, self.multiple_key)

    def evaluate(self, scenario: BoundScenario, *, segment_id: str) -> SegmentValuation:
        ebitda = scenario.get(self.ebitda_key)
        multiple = scenario.get(self.multiple_key)
        if ebitda.measure.dimension.value != "money":
            raise ValueError("normalized EBITDA must be a money measure")
        normalized_multiple = multiple.measure.convert_to("multiple")
        if normalized_multiple.amount < Decimal("0"):
            raise ValueError("normalized multiple cannot be negative")
        value = Measure(
            ebitda.measure.amount * normalized_multiple.amount,
            ebitda.measure.unit,
            max(ebitda.measure.as_of, normalized_multiple.as_of),
        )
        return SegmentValuation(
            contribution_id=f"{segment_id}:{scenario.scenario_id}:{self.evaluator_id}:v{self.version}",
            segment_id=segment_id,
            scenario_id=scenario.scenario_id,
            value_kind=ValueKind.ENTERPRISE_VALUE,
            value=value,
            economic_path_ids=tuple(dict.fromkeys((ebitda.economic_path_id, multiple.economic_path_id))),
            evaluator_id=self.evaluator_id,
            evaluator_version=self.version,
        )
