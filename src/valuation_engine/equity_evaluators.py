from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from .actual_units import Dimension, Measure
from .dcf_evaluators import RegistryLoader
from .evaluator_registry import EvaluatorRegistry, ModelKey, SegmentValuation, ValueKind
from .method_capabilities import (
    MethodCapabilityRegistry,
    load_default_method_capability_registry,
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

_DISCOUNTED_FAMILIES = {
    "gordon_ddm",
    "justified_pb_roe",
    "residual_income",
    "rate_base_roe",
}


@dataclass(frozen=True)
class GordonDDMEvaluator:
    archetype: str
    cost_of_equity: Decimal
    cost_of_equity_path_id: str
    beta_path_id: str
    method: str = "ddm"
    version: str = "1"
    distribution_key: str = "forward_distribution"
    terminal_growth_key: str = "terminal_growth"

    @property
    def key(self) -> ModelKey:
        return ModelKey(self.archetype, self.method, self.version)

    @property
    def evaluator_id(self) -> str:
        return f"{self.archetype}.{self.method}"

    @property
    def required_assumption_keys(self) -> tuple[str, ...]:
        return (self.distribution_key, self.terminal_growth_key)

    def evaluate(self, scenario: BoundScenario, *, segment_id: str) -> SegmentValuation:
        distribution = scenario.get(self.distribution_key)
        growth_assumption = scenario.get(self.terminal_growth_key)
        _require_money(distribution.measure, "forward distribution")
        growth = growth_assumption.measure.convert_to("ratio").amount
        _validate_discount_growth(self.cost_of_equity, growth)
        if distribution.measure.amount < 0:
            raise ValueError("forward distribution cannot be negative")
        value = distribution.measure.amount / (self.cost_of_equity - growth)
        return _equity_value(
            scenario=scenario,
            segment_id=segment_id,
            evaluator_id=self.evaluator_id,
            version=self.version,
            value=Measure(
                value,
                distribution.measure.unit,
                max(distribution.measure.as_of, growth_assumption.measure.as_of),
            ),
            assumptions=(distribution, growth_assumption),
            upstream_paths=(
                f"{self.beta_path_id}:{segment_id}",
                f"{self.cost_of_equity_path_id}:{segment_id}",
            ),
        )


@dataclass(frozen=True)
class JustifiedPBROEEvaluator:
    archetype: str
    cost_of_equity: Decimal
    cost_of_equity_path_id: str
    beta_path_id: str
    method: str = "pb_roe"
    version: str = "1"
    book_value_key: str = "current_book_value"
    forward_roe_key: str = "forward_roe"
    terminal_growth_key: str = "terminal_growth"

    @property
    def key(self) -> ModelKey:
        return ModelKey(self.archetype, self.method, self.version)

    @property
    def evaluator_id(self) -> str:
        return f"{self.archetype}.{self.method}"

    @property
    def required_assumption_keys(self) -> tuple[str, ...]:
        return (self.book_value_key, self.forward_roe_key, self.terminal_growth_key)

    def evaluate(self, scenario: BoundScenario, *, segment_id: str) -> SegmentValuation:
        book = scenario.get(self.book_value_key)
        roe_assumption = scenario.get(self.forward_roe_key)
        growth_assumption = scenario.get(self.terminal_growth_key)
        _require_money(book.measure, "current book value")
        if book.measure.amount <= 0:
            raise ValueError("current book value must be positive")
        roe = roe_assumption.measure.convert_to("ratio").amount
        growth = growth_assumption.measure.convert_to("ratio").amount
        _validate_discount_growth(self.cost_of_equity, growth)
        if roe <= growth:
            raise ValueError("forward ROE must exceed terminal growth for justified P/B")
        justified_pb = (roe - growth) / (self.cost_of_equity - growth)
        value = book.measure.amount * justified_pb
        return _equity_value(
            scenario=scenario,
            segment_id=segment_id,
            evaluator_id=self.evaluator_id,
            version=self.version,
            value=Measure(
                value,
                book.measure.unit,
                max(
                    book.measure.as_of,
                    roe_assumption.measure.as_of,
                    growth_assumption.measure.as_of,
                ),
            ),
            assumptions=(book, roe_assumption, growth_assumption),
            upstream_paths=(
                f"{self.beta_path_id}:{segment_id}",
                f"{self.cost_of_equity_path_id}:{segment_id}",
            ),
        )


@dataclass(frozen=True)
class ResidualIncomeEvaluator:
    archetype: str
    cost_of_equity: Decimal
    cost_of_equity_path_id: str
    beta_path_id: str
    forecast_years: int = 3
    method: str = "residual_income"
    version: str = "1"
    assumption_prefix: str = ""

    def __post_init__(self) -> None:
        if self.forecast_years < 1 or self.forecast_years > 30:
            raise ValueError("residual-income forecast_years must be in [1, 30]")

    def _key(self, name: str) -> str:
        return f"{self.assumption_prefix}{name}"

    @property
    def key(self) -> ModelKey:
        return ModelKey(self.archetype, self.method, self.version)

    @property
    def evaluator_id(self) -> str:
        return f"{self.archetype}.{self.method}"

    @property
    def required_assumption_keys(self) -> tuple[str, ...]:
        return (
            self._key("beginning_book_value"),
            *(self._key(f"roe_year_{year}") for year in range(1, self.forecast_years + 1)),
            *(
                self._key(f"distribution_year_{year}")
                for year in range(1, self.forecast_years + 1)
            ),
            self._key("terminal_roe"),
            self._key("terminal_growth"),
        )

    def evaluate(self, scenario: BoundScenario, *, segment_id: str) -> SegmentValuation:
        beginning = scenario.get(self._key("beginning_book_value"))
        _require_money(beginning.measure, "beginning book value")
        if beginning.measure.amount <= 0:
            raise ValueError("beginning book value must be positive")
        terminal_roe_assumption = scenario.get(self._key("terminal_roe"))
        growth_assumption = scenario.get(self._key("terminal_growth"))
        terminal_roe = terminal_roe_assumption.measure.convert_to("ratio").amount
        growth = growth_assumption.measure.convert_to("ratio").amount
        _validate_discount_growth(self.cost_of_equity, growth)

        one = Decimal("1")
        current_book = beginning.measure.amount
        pv_residual = Decimal("0")
        assumptions = [beginning]
        as_of = beginning.measure.as_of
        for year in range(1, self.forecast_years + 1):
            roe_assumption = scenario.get(self._key(f"roe_year_{year}"))
            distribution = scenario.get(self._key(f"distribution_year_{year}"))
            roe = roe_assumption.measure.convert_to("ratio").amount
            distribution_measure = distribution.measure.convert_to(beginning.measure.unit)
            if distribution_measure.amount < 0:
                raise ValueError("residual-income distributions cannot be negative")
            net_income = roe * current_book
            residual_income = (roe - self.cost_of_equity) * current_book
            pv_residual += residual_income / (one + self.cost_of_equity) ** year
            current_book = current_book + net_income - distribution_measure.amount
            if current_book <= 0:
                raise ValueError("residual-income ending book value must remain positive")
            assumptions.extend((roe_assumption, distribution))
            as_of = max(as_of, roe_assumption.measure.as_of, distribution.measure.as_of)

        terminal_residual_income = (
            (terminal_roe - self.cost_of_equity)
            * current_book
            * (one + growth)
        )
        terminal_value = terminal_residual_income / (self.cost_of_equity - growth)
        pv_terminal = terminal_value / (one + self.cost_of_equity) ** self.forecast_years
        equity_value = beginning.measure.amount + pv_residual + pv_terminal
        assumptions.extend((terminal_roe_assumption, growth_assumption))
        as_of = max(
            as_of,
            terminal_roe_assumption.measure.as_of,
            growth_assumption.measure.as_of,
        )
        return _equity_value(
            scenario=scenario,
            segment_id=segment_id,
            evaluator_id=self.evaluator_id,
            version=self.version,
            value=Measure(equity_value, beginning.measure.unit, as_of),
            assumptions=tuple(assumptions),
            upstream_paths=(
                f"{self.beta_path_id}:{segment_id}",
                f"{self.cost_of_equity_path_id}:{segment_id}",
            ),
        )


@dataclass(frozen=True)
class NetAssetValueEvaluator:
    archetype: str
    method: str = "nav"
    version: str = "1"
    asset_value_key: str = "gross_asset_value"
    liabilities_key: str = "liabilities"

    @property
    def key(self) -> ModelKey:
        return ModelKey(self.archetype, self.method, self.version)

    @property
    def evaluator_id(self) -> str:
        return f"{self.archetype}.{self.method}"

    @property
    def required_assumption_keys(self) -> tuple[str, ...]:
        return (self.asset_value_key, self.liabilities_key)

    def evaluate(self, scenario: BoundScenario, *, segment_id: str) -> SegmentValuation:
        assets = scenario.get(self.asset_value_key)
        liabilities = scenario.get(self.liabilities_key)
        _require_money(assets.measure, "gross asset value")
        liabilities_measure = liabilities.measure.convert_to(assets.measure.unit)
        if assets.measure.amount < 0 or liabilities_measure.amount < 0:
            raise ValueError("NAV assets and liabilities must be non-negative")
        return _equity_value(
            scenario=scenario,
            segment_id=segment_id,
            evaluator_id=self.evaluator_id,
            version=self.version,
            value=Measure(
                assets.measure.amount - liabilities_measure.amount,
                assets.measure.unit,
                max(assets.measure.as_of, liabilities.measure.as_of),
            ),
            assumptions=(assets, liabilities),
        )


@dataclass(frozen=True)
class FFOMultipleEvaluator:
    archetype: str
    method: str = "ffo_multiple"
    version: str = "1"
    ffo_key: str = "normalized_forward_ffo"
    multiple_key: str = "ffo_multiple"

    @property
    def key(self) -> ModelKey:
        return ModelKey(self.archetype, self.method, self.version)

    @property
    def evaluator_id(self) -> str:
        return f"{self.archetype}.{self.method}"

    @property
    def required_assumption_keys(self) -> tuple[str, ...]:
        return (self.ffo_key, self.multiple_key)

    def evaluate(self, scenario: BoundScenario, *, segment_id: str) -> SegmentValuation:
        ffo = scenario.get(self.ffo_key)
        multiple = scenario.get(self.multiple_key)
        _require_money(ffo.measure, "normalized forward FFO")
        normalized_multiple = multiple.measure.convert_to("multiple")
        if ffo.measure.amount <= 0:
            raise ValueError("normalized forward FFO must be positive")
        if normalized_multiple.amount < 0:
            raise ValueError("FFO multiple cannot be negative")
        return _equity_value(
            scenario=scenario,
            segment_id=segment_id,
            evaluator_id=self.evaluator_id,
            version=self.version,
            value=Measure(
                ffo.measure.amount * normalized_multiple.amount,
                ffo.measure.unit,
                max(ffo.measure.as_of, multiple.measure.as_of),
            ),
            assumptions=(ffo, multiple),
        )


@dataclass(frozen=True)
class RateBaseROEEvaluator:
    archetype: str
    cost_of_equity: Decimal
    cost_of_equity_path_id: str
    beta_path_id: str
    method: str = "rate_base_roe"
    version: str = "1"
    rate_base_key: str = "rate_base"
    equity_ratio_key: str = "equity_ratio"
    allowed_roe_key: str = "allowed_roe"
    terminal_growth_key: str = "terminal_growth"

    @property
    def key(self) -> ModelKey:
        return ModelKey(self.archetype, self.method, self.version)

    @property
    def evaluator_id(self) -> str:
        return f"{self.archetype}.{self.method}"

    @property
    def required_assumption_keys(self) -> tuple[str, ...]:
        return (
            self.rate_base_key,
            self.equity_ratio_key,
            self.allowed_roe_key,
            self.terminal_growth_key,
        )

    def evaluate(self, scenario: BoundScenario, *, segment_id: str) -> SegmentValuation:
        rate_base = scenario.get(self.rate_base_key)
        equity_ratio_assumption = scenario.get(self.equity_ratio_key)
        allowed_roe_assumption = scenario.get(self.allowed_roe_key)
        growth_assumption = scenario.get(self.terminal_growth_key)
        _require_money(rate_base.measure, "rate base")
        equity_ratio = equity_ratio_assumption.measure.convert_to("ratio").amount
        allowed_roe = allowed_roe_assumption.measure.convert_to("ratio").amount
        growth = growth_assumption.measure.convert_to("ratio").amount
        _validate_discount_growth(self.cost_of_equity, growth)
        if rate_base.measure.amount <= 0:
            raise ValueError("rate base must be positive")
        if not Decimal("0") < equity_ratio <= Decimal("1"):
            raise ValueError("equity ratio must be in (0, 1]")
        if allowed_roe <= 0:
            raise ValueError("allowed ROE must be positive")
        forward_equity_income = (
            rate_base.measure.amount
            * equity_ratio
            * allowed_roe
            * (Decimal("1") + growth)
        )
        value = forward_equity_income / (self.cost_of_equity - growth)
        return _equity_value(
            scenario=scenario,
            segment_id=segment_id,
            evaluator_id=self.evaluator_id,
            version=self.version,
            value=Measure(
                value,
                rate_base.measure.unit,
                max(
                    rate_base.measure.as_of,
                    equity_ratio_assumption.measure.as_of,
                    allowed_roe_assumption.measure.as_of,
                    growth_assumption.measure.as_of,
                ),
            ),
            assumptions=(
                rate_base,
                equity_ratio_assumption,
                allowed_roe_assumption,
                growth_assumption,
            ),
            upstream_paths=(
                f"{self.beta_path_id}:{segment_id}",
                f"{self.cost_of_equity_path_id}:{segment_id}",
            ),
        )


@dataclass(frozen=True)
class NormalizedEBITDAMultipleEvaluator:
    archetype: str = "contracted_backlog"
    method: str = "normalized_ebitda"
    version: str = "1"
    ebitda_key: str = "normalized_ebitda"
    multiple_key: str = "normalized_ebitda_multiple"

    @property
    def key(self) -> ModelKey:
        return ModelKey(self.archetype, self.method, self.version)

    @property
    def evaluator_id(self) -> str:
        return f"{self.archetype}.{self.method}"

    @property
    def required_assumption_keys(self) -> tuple[str, ...]:
        return (self.ebitda_key, self.multiple_key)

    def evaluate(self, scenario: BoundScenario, *, segment_id: str) -> SegmentValuation:
        ebitda = scenario.get(self.ebitda_key)
        multiple = scenario.get(self.multiple_key)
        _require_money(ebitda.measure, "normalized EBITDA")
        normalized_multiple = multiple.measure.convert_to("multiple")
        if ebitda.measure.amount <= 0:
            raise ValueError("normalized EBITDA must be positive")
        if normalized_multiple.amount < 0:
            raise ValueError("normalized EBITDA multiple cannot be negative")
        return _enterprise_value(
            scenario=scenario,
            segment_id=segment_id,
            evaluator_id=self.evaluator_id,
            version=self.version,
            value=Measure(
                ebitda.measure.amount * normalized_multiple.amount,
                ebitda.measure.unit,
                max(ebitda.measure.as_of, multiple.measure.as_of),
            ),
            assumptions=(ebitda, multiple),
        )


@dataclass(frozen=True)
class LiveEquityMethodRegistration:
    archetype: str
    method: str
    version: str = "1"
    forecast_years: int = 3
    assumption_prefix: str = ""
    segment_id: str | None = None

    def validate(self) -> None:
        if not self.archetype or not self.method or not self.version:
            raise ValueError("equity-method registration requires archetype/method/version")
        if self.forecast_years < 1 or self.forecast_years > 30:
            raise ValueError("equity-method forecast_years must be in [1, 30]")
        if any(character.isspace() for character in self.assumption_prefix):
            raise ValueError("equity-method assumption_prefix cannot contain whitespace")
        if self.segment_id is not None and not self.segment_id:
            raise ValueError("equity-method segment_id cannot be empty")


BaseRegistryLoader = Callable[[OrchestratorContext], EvaluatorRegistry]


def live_equity_evaluator_registry_loader(
    *,
    registrations: tuple[LiveEquityMethodRegistration, ...],
    base_loader: BaseRegistryLoader | None = None,
    capability_registry: MethodCapabilityRegistry | None = None,
) -> RegistryLoader:
    """Register exact equity/NAV evaluator families on top of an optional base registry.

    Callers should pass only methods that are in the current route. Discounted equity methods
    consume the same-run LiveWACCStageResult and preserve both Beta and WACC economic paths.
    """
    if not registrations:
        raise ValueError("live equity evaluator loader requires registrations")
    effective_capabilities = capability_registry or load_default_method_capability_registry()
    seen: set[tuple[str | None, ModelKey]] = set()
    for registration in registrations:
        registration.validate()
        capability = effective_capabilities.get(
            registration.archetype,
            registration.method,
        )
        if capability.execution_family not in {
            "gordon_ddm",
            "justified_pb_roe",
            "residual_income",
            "net_asset_value",
            "ffo_multiple",
            "rate_base_roe",
            "normalized_ebitda_multiple",
        }:
            raise ValueError(
                f"unsupported equity evaluator execution family {capability.execution_family}"
            )
        key = ModelKey(registration.archetype, registration.method, registration.version)
        scoped_key = (registration.segment_id, key)
        if scoped_key in seen:
            raise ValueError(f"duplicate equity evaluator registration: {key}")
        seen.add(scoped_key)

    def load(context: OrchestratorContext) -> EvaluatorRegistry:
        leaked = tuple(
            sorted(key for key in _FORBIDDEN_PRE_FREEZE_KEYS if key in context.data)
        )
        if leaked:
            raise PermissionError(
                "pre-freeze equity evaluator context contains target Street/market fields: "
                + ", ".join(leaked)
            )
        registry = base_loader(context) if base_loader is not None else EvaluatorRegistry()
        if not isinstance(registry, EvaluatorRegistry):
            raise TypeError("base_loader must return EvaluatorRegistry")
        wacc_raw = context.data.get("live_wacc_result")
        wacc_result = wacc_raw if isinstance(wacc_raw, LiveWACCStageResult) else None
        for registration in registrations:
            capability = effective_capabilities.get(
                registration.archetype,
                registration.method,
            )
            needs_discount = capability.execution_family in _DISCOUNTED_FAMILIES
            if needs_discount and wacc_result is None:
                raise ValueError(
                    f"{registration.archetype}/{registration.method} requires LiveWACCStageResult"
                )
            cost_of_equity = (
                Decimal(str(wacc_result.wacc_result.cost_of_equity))
                if wacc_result is not None
                else None
            )
            beta_path = (
                f"beta:{wacc_result.beta_result.snapshot_hash}"
                if wacc_result is not None
                else ""
            )
            wacc_path = (
                f"wacc:{wacc_result.snapshot_hash}"
                if wacc_result is not None
                else ""
            )
            family = capability.execution_family
            # The registration's assumption_prefix is the segment namespace:
            # every family's key names take it, so two segments running the
            # same equity family cannot share one compiled assumption.
            prefix = registration.assumption_prefix
            if family == "gordon_ddm":
                evaluator = GordonDDMEvaluator(
                    registration.archetype,
                    cost_of_equity=cost_of_equity,
                    cost_of_equity_path_id=wacc_path,
                    beta_path_id=beta_path,
                    method=registration.method,
                    version=registration.version,
                    distribution_key=f"{prefix}forward_distribution",
                    terminal_growth_key=f"{prefix}terminal_growth",
                )
            elif family == "justified_pb_roe":
                evaluator = JustifiedPBROEEvaluator(
                    registration.archetype,
                    cost_of_equity=cost_of_equity,
                    cost_of_equity_path_id=wacc_path,
                    beta_path_id=beta_path,
                    method=registration.method,
                    version=registration.version,
                    book_value_key=f"{prefix}current_book_value",
                    forward_roe_key=f"{prefix}forward_roe",
                    terminal_growth_key=f"{prefix}terminal_growth",
                )
            elif family == "residual_income":
                evaluator = ResidualIncomeEvaluator(
                    registration.archetype,
                    cost_of_equity=cost_of_equity,
                    cost_of_equity_path_id=wacc_path,
                    beta_path_id=beta_path,
                    forecast_years=registration.forecast_years,
                    method=registration.method,
                    version=registration.version,
                    assumption_prefix=registration.assumption_prefix,
                )
            elif family == "net_asset_value":
                evaluator = NetAssetValueEvaluator(
                    registration.archetype,
                    method=registration.method,
                    version=registration.version,
                    asset_value_key=f"{prefix}gross_asset_value",
                    liabilities_key=f"{prefix}liabilities",
                )
            elif family == "ffo_multiple":
                evaluator = FFOMultipleEvaluator(
                    registration.archetype,
                    method=registration.method,
                    version=registration.version,
                    ffo_key=f"{prefix}normalized_forward_ffo",
                    multiple_key=f"{prefix}ffo_multiple",
                )
            elif family == "rate_base_roe":
                evaluator = RateBaseROEEvaluator(
                    registration.archetype,
                    cost_of_equity=cost_of_equity,
                    cost_of_equity_path_id=wacc_path,
                    beta_path_id=beta_path,
                    method=registration.method,
                    version=registration.version,
                    rate_base_key=f"{prefix}rate_base",
                    equity_ratio_key=f"{prefix}equity_ratio",
                    allowed_roe_key=f"{prefix}allowed_roe",
                    terminal_growth_key=f"{prefix}terminal_growth",
                )
            elif family == "normalized_ebitda_multiple":
                evaluator = NormalizedEBITDAMultipleEvaluator(
                    registration.archetype,
                    method=registration.method,
                    version=registration.version,
                    ebitda_key=f"{prefix}normalized_ebitda",
                    multiple_key=f"{prefix}normalized_ebitda_multiple",
                )
            else:  # validated above
                raise AssertionError(family)
            registry.register(evaluator, segment_id=registration.segment_id)
        return registry

    return load


def _require_money(measure: Measure, label: str) -> None:
    if measure.dimension is not Dimension.MONEY:
        raise ValueError(f"{label} must be a money measure")


def _validate_discount_growth(cost_of_equity: Decimal, growth: Decimal) -> None:
    if not cost_of_equity.is_finite() or cost_of_equity <= 0:
        raise ValueError("cost of equity must be finite and positive")
    if not growth.is_finite() or growth <= Decimal("-1"):
        raise ValueError("terminal growth must be finite and greater than -100%")
    if cost_of_equity <= growth:
        raise ValueError("cost of equity must exceed terminal growth")


def _economic_paths(assumptions, upstream_paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            tuple(item.economic_path_id for item in assumptions)
            + tuple(path for path in upstream_paths if path)
        )
    )


def _equity_value(
    *,
    scenario: BoundScenario,
    segment_id: str,
    evaluator_id: str,
    version: str,
    value: Measure,
    assumptions,
    upstream_paths: tuple[str, ...] = (),
) -> SegmentValuation:
    return SegmentValuation(
        contribution_id=f"{segment_id}:{scenario.scenario_id}:{evaluator_id}:v{version}",
        segment_id=segment_id,
        scenario_id=scenario.scenario_id,
        value_kind=ValueKind.EQUITY_VALUE,
        value=value,
        economic_path_ids=_economic_paths(assumptions, upstream_paths),
        evaluator_id=evaluator_id,
        evaluator_version=version,
    )


def _enterprise_value(
    *,
    scenario: BoundScenario,
    segment_id: str,
    evaluator_id: str,
    version: str,
    value: Measure,
    assumptions,
) -> SegmentValuation:
    return SegmentValuation(
        contribution_id=f"{segment_id}:{scenario.scenario_id}:{evaluator_id}:v{version}",
        segment_id=segment_id,
        scenario_id=scenario.scenario_id,
        value_kind=ValueKind.ENTERPRISE_VALUE,
        value=value,
        economic_path_ids=_economic_paths(assumptions, ()),
        evaluator_id=evaluator_id,
        evaluator_version=version,
    )
