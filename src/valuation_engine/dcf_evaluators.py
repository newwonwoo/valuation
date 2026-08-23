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
from .wacc import validate_terminal_consistency


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
class LiveDCFRegistration:
    archetype: str
    method: str
    version: str
    forecast_years: int
    assumption_prefix: str = ""

    def validate(self) -> None:
        if not all((self.archetype, self.method, self.version)):
            raise ValueError("live DCF registration requires archetype, method and version")
        if self.forecast_years < 1 or self.forecast_years > 30:
            raise ValueError("live DCF forecast_years must be in [1, 30]")
        if any(character.isspace() for character in self.assumption_prefix):
            raise ValueError("assumption_prefix cannot contain whitespace")


@dataclass(frozen=True)
class ExplicitFCFFDCFEvaluator:
    archetype: str
    method: str
    version: str
    forecast_years: int
    discount_rate: Decimal
    discount_rate_path_id: str
    assumption_prefix: str = ""

    def __post_init__(self) -> None:
        if not all((self.archetype, self.method, self.version, self.discount_rate_path_id)):
            raise ValueError("explicit FCFF DCF evaluator requires identity and discount-rate path")
        if self.forecast_years < 1 or self.forecast_years > 30:
            raise ValueError("forecast_years must be in [1, 30]")
        if not self.discount_rate.is_finite() or self.discount_rate <= 0:
            raise ValueError("discount_rate must be finite and positive")

    @property
    def key(self) -> ModelKey:
        return ModelKey(self.archetype, self.method, self.version)

    @property
    def evaluator_id(self) -> str:
        return f"{self.archetype}.{self.method}"

    def _key(self, name: str) -> str:
        return f"{self.assumption_prefix}{name}"

    @property
    def required_assumption_keys(self) -> tuple[str, ...]:
        return (
            *(self._key(f"fcff_year_{year}") for year in range(1, self.forecast_years + 1)),
            self._key("terminal_growth"),
            self._key("terminal_roic"),
        )

    def evaluate(self, scenario: BoundScenario, *, segment_id: str) -> SegmentValuation:
        fcff_assumptions = tuple(
            scenario.get(self._key(f"fcff_year_{year}"))
            for year in range(1, self.forecast_years + 1)
        )
        first_measure = fcff_assumptions[0].measure
        if first_measure.dimension.value != "money":
            raise ValueError("FCFF path must use money measures")
        fcff_path = tuple(item.measure.convert_to(first_measure.unit) for item in fcff_assumptions)

        terminal_growth_assumption = scenario.get(self._key("terminal_growth"))
        terminal_roic_assumption = scenario.get(self._key("terminal_roic"))
        terminal_growth = terminal_growth_assumption.measure.convert_to("ratio").amount
        terminal_roic = terminal_roic_assumption.measure.convert_to("ratio").amount
        validate_terminal_consistency(
            wacc=float(self.discount_rate),
            terminal_growth=float(terminal_growth),
            terminal_roic=float(terminal_roic),
        )
        if fcff_path[-1].amount <= 0:
            raise ValueError("Gordon terminal value requires positive final-year FCFF")

        one = Decimal("1")
        present_value = Decimal("0")
        for year, fcff in enumerate(fcff_path, start=1):
            present_value += fcff.amount / (one + self.discount_rate) ** year

        terminal_fcff = fcff_path[-1].amount * (one + terminal_growth)
        terminal_value = terminal_fcff / (self.discount_rate - terminal_growth)
        present_value += terminal_value / (one + self.discount_rate) ** self.forecast_years

        as_of = max(
            *(item.measure.as_of for item in fcff_assumptions),
            terminal_growth_assumption.measure.as_of,
            terminal_roic_assumption.measure.as_of,
        )
        economic_paths = tuple(
            dict.fromkeys(
                (
                    *(item.economic_path_id for item in fcff_assumptions),
                    terminal_growth_assumption.economic_path_id,
                    terminal_roic_assumption.economic_path_id,
                    f"{self.discount_rate_path_id}:{segment_id}",
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


def live_fcff_dcf_registry_loader(
    *,
    registrations: tuple[LiveDCFRegistration, ...],
    include_default_normalized_multiples: bool = True,
) -> RegistryLoader:
    if not registrations:
        raise ValueError("live FCFF DCF registry loader requires registrations")
    for registration in registrations:
        registration.validate()
    keys = tuple(
        ModelKey(item.archetype, item.method, item.version) for item in registrations
    )
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate live DCF ModelKey registration")

    def load(context: OrchestratorContext) -> EvaluatorRegistry:
        leaked = tuple(sorted(key for key in _FORBIDDEN_PRE_FREEZE_KEYS if key in context.data))
        if leaked:
            raise PermissionError(
                "pre-freeze DCF context contains target Street/market fields: "
                + ", ".join(leaked)
            )
        wacc_result = context.data.get("live_wacc_result")
        if not isinstance(wacc_result, LiveWACCStageResult):
            raise ValueError("LiveWACCStageResult is required to build live DCF evaluators")
        discount_rate_float = wacc_result.wacc_result.wacc
        if not isfinite(discount_rate_float) or discount_rate_float <= 0:
            raise ValueError("live WACC must be finite and positive")
        discount_rate = Decimal(str(discount_rate_float))
        registry = EvaluatorRegistry()
        if include_default_normalized_multiples:
            registry.register(NormalizedMultipleEvaluator("commodity_price_taker"))
            registry.register(NormalizedMultipleEvaluator("process_spread"))
        for item in registrations:
            registry.register(
                ExplicitFCFFDCFEvaluator(
                    archetype=item.archetype,
                    method=item.method,
                    version=item.version,
                    forecast_years=item.forecast_years,
                    discount_rate=discount_rate,
                    discount_rate_path_id=f"wacc:{wacc_result.snapshot_hash}",
                    assumption_prefix=item.assumption_prefix,
                )
            )
        return registry

    return load
