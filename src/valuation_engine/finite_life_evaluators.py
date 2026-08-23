from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import Callable

from .actual_units import Measure
from .evaluator_registry import (
    EvaluatorRegistry,
    ModelKey,
    NormalizedMultipleEvaluator,
    SegmentValuation,
    ValueKind,
)
from .orchestrator import OrchestratorContext
from .risk_adapters import LiveWACCStageResult
from .scenario_binding import BoundScenario


_FORBIDDEN_PRE_FREEZE_KEYS = {
    "current_market_price",
    "market_price",
    "market_observation",
    "target_market_cap",
    "target_price",
    "consensus_target",
    "target_multiple",
    "street_reference",
}


@dataclass(frozen=True)
class FiniteLifeNPVRegistration:
    archetype: str
    method: str
    version: str
    final_year: int
    assumption_prefix: str = ""

    def validate(self) -> None:
        if not all((self.archetype, self.method, self.version)):
            raise ValueError("finite-life NPV registration requires archetype, method and version")
        if self.final_year < 1 or self.final_year > 60:
            raise ValueError("finite-life NPV final_year must be in [1, 60]")
        if any(character.isspace() for character in self.assumption_prefix):
            raise ValueError("assumption_prefix cannot contain whitespace")


@dataclass(frozen=True)
class FiniteLifeNPVEvaluator:
    archetype: str
    method: str
    version: str
    final_year: int
    discount_rate: Decimal
    discount_rate_path_id: str
    beta_path_id: str
    assumption_prefix: str = ""

    def __post_init__(self) -> None:
        if not all(
            (
                self.archetype,
                self.method,
                self.version,
                self.discount_rate_path_id,
                self.beta_path_id,
            )
        ):
            raise ValueError("finite-life NPV evaluator requires identity and Beta/WACC paths")
        if self.final_year < 1 or self.final_year > 60:
            raise ValueError("finite-life NPV final_year must be in [1, 60]")
        if not self.discount_rate.is_finite() or self.discount_rate <= 0:
            raise ValueError("discount_rate must be finite and positive")

    @property
    def key(self) -> ModelKey:
        return ModelKey(self.archetype, self.method, self.version)

    @property
    def evaluator_id(self) -> str:
        return f"{self.archetype}.{self.method}"

    def _key(self, year: int) -> str:
        return f"{self.assumption_prefix}cashflow_year_{year}"

    @property
    def required_assumption_keys(self) -> tuple[str, ...]:
        return tuple(self._key(year) for year in range(0, self.final_year + 1))

    def evaluate(self, scenario: BoundScenario, *, segment_id: str) -> SegmentValuation:
        assumptions = tuple(
            scenario.get(self._key(year)) for year in range(0, self.final_year + 1)
        )
        first_measure = assumptions[0].measure
        if first_measure.dimension.value != "money":
            raise ValueError("finite-life cash flows must use money measures")
        cashflows = tuple(item.measure.convert_to(first_measure.unit) for item in assumptions)
        one = Decimal("1")
        present_value = Decimal("0")
        for year, cashflow in enumerate(cashflows):
            present_value += cashflow.amount / (one + self.discount_rate) ** year

        as_of = max(item.measure.as_of for item in assumptions)
        economic_paths = tuple(
            dict.fromkeys(
                (
                    *(item.economic_path_id for item in assumptions),
                    f"{self.discount_rate_path_id}:{segment_id}",
                    f"{self.beta_path_id}:{segment_id}",
                )
            )
        )
        return SegmentValuation(
            contribution_id=(
                f"{segment_id}:{scenario.scenario_id}:{self.evaluator_id}:v{self.version}"
            ),
            segment_id=segment_id,
            scenario_id=scenario.scenario_id,
            value_kind=ValueKind.ENTERPRISE_VALUE,
            value=Measure(present_value, first_measure.unit, as_of),
            economic_path_ids=economic_paths,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.version,
        )


RegistryLoader = Callable[[OrchestratorContext], EvaluatorRegistry]


def live_finite_npv_registry_loader(
    *,
    registrations: tuple[FiniteLifeNPVRegistration, ...],
    base_loader: RegistryLoader | None = None,
    include_default_normalized_multiples: bool = True,
) -> RegistryLoader:
    """Build exact finite-life evaluators from the same-run live WACC.

    A base loader may be supplied to compose these exact registrations with the existing
    explicit-FCFF DCF family. No unknown method is interpreted as a finite-life NPV.
    """
    if not registrations:
        raise ValueError("finite-life NPV registry loader requires registrations")
    for registration in registrations:
        registration.validate()
    keys = tuple(ModelKey(item.archetype, item.method, item.version) for item in registrations)
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate finite-life NPV ModelKey registration")

    def load(context: OrchestratorContext) -> EvaluatorRegistry:
        leaked = tuple(sorted(key for key in _FORBIDDEN_PRE_FREEZE_KEYS if key in context.data))
        if leaked:
            raise PermissionError(
                "pre-freeze finite-life NPV context contains target Street/market fields: "
                + ", ".join(leaked)
            )
        wacc_result = context.data.get("live_wacc_result")
        if not isinstance(wacc_result, LiveWACCStageResult):
            raise ValueError("LiveWACCStageResult is required to build finite-life NPV evaluators")
        discount_rate_float = wacc_result.wacc_result.wacc
        if not isfinite(discount_rate_float) or discount_rate_float <= 0:
            raise ValueError("live WACC must be finite and positive")

        if base_loader is not None:
            registry = base_loader(context)
        else:
            registry = EvaluatorRegistry()
            if include_default_normalized_multiples:
                registry.register(NormalizedMultipleEvaluator("commodity_price_taker"))
                registry.register(NormalizedMultipleEvaluator("process_spread"))

        discount_rate = Decimal(str(discount_rate_float))
        for item in registrations:
            registry.register(
                FiniteLifeNPVEvaluator(
                    archetype=item.archetype,
                    method=item.method,
                    version=item.version,
                    final_year=item.final_year,
                    discount_rate=discount_rate,
                    discount_rate_path_id=f"wacc:{wacc_result.snapshot_hash}",
                    beta_path_id=f"beta:{wacc_result.beta_result.snapshot_hash}",
                    assumption_prefix=item.assumption_prefix,
                )
            )
        return registry

    return load
