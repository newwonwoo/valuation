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
from .method_capabilities import MethodCapabilityRegistry, require_execution_family
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


def _validate_relative_key(value: str, label: str) -> None:
    if not value or any(character.isspace() for character in value):
        raise ValueError(f"{label} must be a non-empty key without whitespace")


def _validate_capex_entries(
    *,
    forecast_years: int,
    primary_key: str | None,
    primary_year: int | None,
    additional: tuple[tuple[str, int], ...],
) -> None:
    if (primary_key is None) != (primary_year is None):
        raise ValueError(
            "expansion CAPEX requires both expansion_capex_key and expansion_capex_year"
        )
    entries: list[tuple[str, int]] = []
    if primary_key is not None:
        assert primary_year is not None
        entries.append((primary_key, primary_year))
    entries.extend(additional)
    keys: list[str] = []
    for key, year in entries:
        _validate_relative_key(key, "expansion CAPEX key")
        if not 1 <= int(year) <= forecast_years:
            raise ValueError("expansion CAPEX year must fall inside the explicit forecast")
        keys.append(key)
    if len(keys) != len(set(keys)):
        raise ValueError("expansion CAPEX keys must be unique")


@dataclass(frozen=True)
class LiveDCFRegistration:
    archetype: str
    method: str
    version: str
    forecast_years: int
    assumption_prefix: str = ""
    expansion_capex_key: str | None = None
    expansion_capex_year: int | None = None
    additive_fcff_prefixes: tuple[str, ...] = ()
    additional_expansion_capex: tuple[tuple[str, int], ...] = ()

    def validate(self) -> None:
        if not all((self.archetype, self.method, self.version)):
            raise ValueError("live DCF registration requires archetype, method and version")
        if self.forecast_years < 1 or self.forecast_years > 30:
            raise ValueError("live DCF forecast_years must be in [1, 30]")
        if any(character.isspace() for character in self.assumption_prefix):
            raise ValueError("assumption_prefix cannot contain whitespace")
        for prefix in self.additive_fcff_prefixes:
            _validate_relative_key(prefix, "additive FCFF prefix")
        if len(self.additive_fcff_prefixes) != len(set(self.additive_fcff_prefixes)):
            raise ValueError("additive FCFF prefixes must be unique")
        _validate_capex_entries(
            forecast_years=self.forecast_years,
            primary_key=self.expansion_capex_key,
            primary_year=self.expansion_capex_year,
            additional=self.additional_expansion_capex,
        )


@dataclass(frozen=True)
class ExplicitFCFFDCFEvaluator:
    archetype: str
    method: str
    version: str
    forecast_years: int
    discount_rate: Decimal
    discount_rate_path_id: str
    assumption_prefix: str = ""
    beta_path_id: str | None = None
    expansion_capex_key: str | None = None
    expansion_capex_year: int | None = None
    additive_fcff_prefixes: tuple[str, ...] = ()
    additional_expansion_capex: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        if not all((self.archetype, self.method, self.version, self.discount_rate_path_id)):
            raise ValueError("explicit FCFF DCF evaluator requires identity and discount-rate path")
        if self.forecast_years < 1 or self.forecast_years > 30:
            raise ValueError("forecast_years must be in [1, 30]")
        if not self.discount_rate.is_finite() or self.discount_rate <= 0:
            raise ValueError("discount_rate must be finite and positive")
        if self.beta_path_id is not None and not self.beta_path_id:
            raise ValueError("beta_path_id cannot be blank")
        if any(character.isspace() for character in self.assumption_prefix):
            raise ValueError("assumption_prefix cannot contain whitespace")
        for prefix in self.additive_fcff_prefixes:
            _validate_relative_key(prefix, "additive FCFF prefix")
        if len(self.additive_fcff_prefixes) != len(set(self.additive_fcff_prefixes)):
            raise ValueError("additive FCFF prefixes must be unique")
        _validate_capex_entries(
            forecast_years=self.forecast_years,
            primary_key=self.expansion_capex_key,
            primary_year=self.expansion_capex_year,
            additional=self.additional_expansion_capex,
        )

    @property
    def key(self) -> ModelKey:
        return ModelKey(self.archetype, self.method, self.version)

    @property
    def evaluator_id(self) -> str:
        return f"{self.archetype}.{self.method}"

    def _key(self, name: str) -> str:
        return f"{self.assumption_prefix}{name}"

    def _additive_key(self, prefix: str, name: str) -> str:
        return self._key(f"{prefix}{name}")

    def _capex_entries(self) -> tuple[tuple[str, int], ...]:
        primary = (
            ((self.expansion_capex_key, int(self.expansion_capex_year)),)
            if self.expansion_capex_key is not None
            and self.expansion_capex_year is not None
            else ()
        )
        return (*primary, *self.additional_expansion_capex)

    @property
    def required_assumption_keys(self) -> tuple[str, ...]:
        base_fcff = tuple(
            self._key(f"fcff_year_{year}")
            for year in range(1, self.forecast_years + 1)
        )
        additive_fcff = tuple(
            self._additive_key(prefix, f"fcff_year_{year}")
            for prefix in self.additive_fcff_prefixes
            for year in range(1, self.forecast_years + 1)
        )
        capex_keys = tuple(self._key(key) for key, _ in self._capex_entries())
        return (
            *base_fcff,
            *additive_fcff,
            *capex_keys,
            self._key("terminal_growth"),
            self._key("terminal_roic"),
        )

    def evaluate(self, scenario: BoundScenario, *, segment_id: str) -> SegmentValuation:
        base_assumptions = tuple(
            scenario.get(self._key(f"fcff_year_{year}"))
            for year in range(1, self.forecast_years + 1)
        )
        first_measure = base_assumptions[0].measure
        if first_measure.dimension.value != "money":
            raise ValueError("FCFF path must use money measures")
        fcff_path = [
            item.measure.convert_to(first_measure.unit) for item in base_assumptions
        ]

        additive_assumptions: list = []
        for prefix in self.additive_fcff_prefixes:
            cohort = tuple(
                scenario.get(self._additive_key(prefix, f"fcff_year_{year}"))
                for year in range(1, self.forecast_years + 1)
            )
            for index, assumption in enumerate(cohort):
                if assumption.measure.dimension.value != "money":
                    raise ValueError("additive FCFF path must use money measures")
                incremental = assumption.measure.convert_to(first_measure.unit)
                original = fcff_path[index]
                fcff_path[index] = Measure(
                    original.amount + incremental.amount,
                    original.unit,
                    max(original.as_of, incremental.as_of),
                )
            additive_assumptions.extend(cohort)

        capex_assumptions: list = []
        for relative_key, year in self._capex_entries():
            capex_assumption = scenario.get(self._key(relative_key))
            if capex_assumption.measure.dimension.value != "money":
                raise ValueError("expansion CAPEX must use a money measure")
            capex = capex_assumption.measure.convert_to(first_measure.unit)
            if capex.amount < 0:
                raise ValueError(
                    "expansion CAPEX must be expressed as a non-negative cash outflow"
                )
            index = int(year) - 1
            original = fcff_path[index]
            fcff_path[index] = Measure(
                original.amount - capex.amount,
                original.unit,
                max(original.as_of, capex.as_of),
            )
            capex_assumptions.append(capex_assumption)

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
            *(item.measure.as_of for item in base_assumptions),
            *(item.measure.as_of for item in additive_assumptions),
            *(item.measure.as_of for item in capex_assumptions),
            terminal_growth_assumption.measure.as_of,
            terminal_roic_assumption.measure.as_of,
        )
        upstream_paths = [f"{self.discount_rate_path_id}:{segment_id}"]
        if self.beta_path_id is not None:
            upstream_paths.append(f"{self.beta_path_id}:{segment_id}")
        economic_paths = tuple(
            dict.fromkeys(
                (
                    *(item.economic_path_id for item in base_assumptions),
                    *(item.economic_path_id for item in additive_assumptions),
                    *(item.economic_path_id for item in capex_assumptions),
                    terminal_growth_assumption.economic_path_id,
                    terminal_roic_assumption.economic_path_id,
                    *upstream_paths,
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
    capability_registry: MethodCapabilityRegistry | None = None,
) -> RegistryLoader:
    if not registrations:
        raise ValueError("live FCFF DCF registry loader requires registrations")
    for registration in registrations:
        registration.validate()
        require_execution_family(
            archetype=registration.archetype,
            method=registration.method,
            expected_family="explicit_fcff_dcf",
            registry=capability_registry,
        )
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
                    beta_path_id=f"beta:{wacc_result.beta_result.snapshot_hash}",
                    expansion_capex_key=item.expansion_capex_key,
                    expansion_capex_year=item.expansion_capex_year,
                    additive_fcff_prefixes=item.additive_fcff_prefixes,
                    additional_expansion_capex=item.additional_expansion_capex,
                )
            )
        return registry

    return load
