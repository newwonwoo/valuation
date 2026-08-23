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
class RuntimeValuationInput:
    key: str
    measure: Measure
    economic_path_id: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.key or not self.economic_path_id or not self.source_refs:
            raise ValueError("runtime valuation input requires key, economic path and source refs")


@dataclass(frozen=True)
class ValuationRuntimeInputs:
    inputs: tuple[RuntimeValuationInput, ...] = ()

    def __post_init__(self) -> None:
        keys = tuple(item.key for item in self.inputs)
        if len(keys) != len(set(keys)):
            raise ValueError("runtime valuation input keys must be unique")

    def get(self, key: str) -> RuntimeValuationInput:
        for item in self.inputs:
            if item.key == key:
                return item
        raise KeyError(key)

    def has(self, key: str) -> bool:
        return any(item.key == key for item in self.inputs)


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

    def __post_init__(self) -> None:
        if not all((self.contribution_id, self.segment_id, self.scenario_id, self.evaluator_id, self.evaluator_version)):
            raise ValueError("segment valuation requires identity, segment, scenario and evaluator")
        if not self.economic_path_ids:
            raise ValueError("segment valuation requires economic path trace")


class DeterministicEvaluator(Protocol):
    key: ModelKey
    evaluator_id: str
    required_assumption_keys: tuple[str, ...]
    required_runtime_input_keys: tuple[str, ...]

    def evaluate(
        self,
        scenario: BoundScenario,
        *,
        segment_id: str,
        runtime_inputs: ValuationRuntimeInputs,
    ) -> SegmentValuation: ...


class EvaluatorRegistry:
    def __init__(self) -> None:
        self._evaluators: dict[ModelKey, DeterministicEvaluator] = {}

    def register(self, evaluator: DeterministicEvaluator) -> None:
        if evaluator.key in self._evaluators:
            raise ValueError(f"duplicate evaluator registration: {evaluator.key}")
        if not evaluator.required_assumption_keys:
            raise ValueError("evaluator must declare required assumptions")
        runtime_keys = tuple(getattr(evaluator, "required_runtime_input_keys", ()))
        if len(runtime_keys) != len(set(runtime_keys)):
            raise ValueError("evaluator runtime-input requirements must be unique")
        self._evaluators[evaluator.key] = evaluator

    def get(self, key: ModelKey) -> DeterministicEvaluator:
        try:
            return self._evaluators[key]
        except KeyError as exc:
            raise KeyError(f"no exact evaluator registered for {key}") from exc

    def evaluate(
        self,
        key: ModelKey,
        scenario: BoundScenario,
        *,
        segment_id: str,
        runtime_inputs: ValuationRuntimeInputs | None = None,
    ) -> SegmentValuation:
        evaluator = self.get(key)
        missing = tuple(item for item in evaluator.required_assumption_keys if not _has_assumption(scenario, item))
        if missing:
            raise ValueError(
                f"evaluator {evaluator.evaluator_id} missing assumptions for {scenario.scenario_id}: {', '.join(missing)}"
            )
        runtime = runtime_inputs or ValuationRuntimeInputs()
        required_runtime = tuple(getattr(evaluator, "required_runtime_input_keys", ()))
        missing_runtime = tuple(item for item in required_runtime if not runtime.has(item))
        if missing_runtime:
            raise ValueError(
                f"evaluator {evaluator.evaluator_id} missing runtime inputs for {scenario.scenario_id}: {', '.join(missing_runtime)}"
            )
        valuation = evaluator.evaluate(
            scenario,
            segment_id=segment_id,
            runtime_inputs=runtime,
        )
        # A declared runtime input is only considered consumed if the evaluator carries its
        # economic path into the valuation trace. This prevents Beta/WACC engines from becoming
        # decorative computations that never reach the conclusion.
        missing_paths = tuple(
            runtime.get(item).economic_path_id
            for item in required_runtime
            if runtime.get(item).economic_path_id not in valuation.economic_path_ids
        )
        if missing_paths:
            raise ValueError(
                f"evaluator {evaluator.evaluator_id} declared runtime inputs but omitted their economic paths: {', '.join(missing_paths)}"
            )
        return valuation

    def keys(self) -> tuple[ModelKey, ...]:
        return tuple(sorted(self._evaluators, key=lambda item: (item.archetype, item.method, item.version)))

    def required_runtime_inputs_for(self, key: ModelKey) -> tuple[str, ...]:
        evaluator = self.get(key)
        return tuple(getattr(evaluator, "required_runtime_input_keys", ()))


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

    @property
    def required_runtime_input_keys(self) -> tuple[str, ...]:
        return ()

    def evaluate(
        self,
        scenario: BoundScenario,
        *,
        segment_id: str,
        runtime_inputs: ValuationRuntimeInputs,
    ) -> SegmentValuation:
        del runtime_inputs
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
