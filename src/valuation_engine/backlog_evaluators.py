"""Backlog-burn driver DCF for contracted-backlog businesses.

Every DCF binding in the capability registry previously resolved to
``explicit_fcff_dcf``, which takes a finished FCFF path as an input. For a
contracted-backlog business that discards the one thing that makes the archetype
forecastable: revenue is not a free assumption, it is drawn down from a stock of
signed orders that is itself replenished by new orders.

This family models that identity directly::

    revenue(t)      = backlog_open(t) x burn_rate(t)
    backlog_open(t+1) = backlog_open(t) + new_orders(t) - revenue(t)

and only then converts revenue to cash::

    FCFF(t) = revenue(t) x margin(t) x (1 - tax)
              + revenue(t) x depreciation_rate
              - revenue(t) x maintenance_capex_rate
              - max(0, revenue(t) - revenue(t-1)) x working_capital_rate

Two constraints are the point of doing it this way rather than accepting an FCFF
path. Neither is expressible in the explicit-FCFF family:

burn rate is bounded at one
    A year cannot recognise more revenue than the backlog standing at its start,
    so the order book can never be drawn negative.

the Gordon tail requires a self-sustaining order book
    A perpetual-growth terminal value asserts the business continues at the final
    year's run rate forever. If the final year books fewer orders than it burns,
    the explicit period is shrinking the backlog while the tail assumes it grows.
    The floor is declared per registration rather than hard-wired, so relaxing it
    is a recorded decision instead of a silent one.

Working capital is charged only on each year's revenue *increase*, so a one-time
ramp is not capitalised into the terminal value forever.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from .actual_units import Measure
from .evaluator_registry import (
    EvaluatorRegistry,
    ModelKey,
    NormalizedMultipleEvaluator,
    SegmentValuation,
    SegmentValuationDiagnostics,
    ValueKind,
)
from .method_capabilities import MethodCapabilityRegistry, require_execution_family
from .orchestrator import OrchestratorContext
from .risk_adapters import LiveWACCStageResult
from .scenario_binding import BoundScenario
from .wacc import validate_terminal_consistency


EXECUTION_FAMILY = "contracted_backlog_dcf"

_ZERO = Decimal("0")
_ONE = Decimal("1")

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

_RATE_KEYS = (
    "operating_tax_rate",
    "depreciation_rate_of_revenue",
    "maintenance_capex_rate_of_revenue",
    "incremental_working_capital_rate",
)


@dataclass(frozen=True)
class BacklogYear:
    """One modelled year of the order-book roll-forward."""

    year: int
    opening_backlog: Decimal
    burn_rate: Decimal
    revenue: Decimal
    new_orders: Decimal
    closing_backlog: Decimal
    operating_margin: Decimal
    operating_profit: Decimal
    fcff: Decimal

    @property
    def book_to_bill(self) -> Decimal:
        return self.new_orders / self.revenue


@dataclass(frozen=True)
class BacklogBurnRegistration:
    archetype: str
    method: str
    version: str
    forecast_years: int
    assumption_prefix: str = ""
    terminal_book_to_bill_floor: Decimal = _ONE

    def validate(self) -> None:
        if not all((self.archetype, self.method, self.version)):
            raise ValueError(
                "backlog-burn registration requires archetype, method and version"
            )
        if self.forecast_years < 1 or self.forecast_years > 30:
            raise ValueError("backlog-burn forecast_years must be in [1, 30]")
        if any(character.isspace() for character in self.assumption_prefix):
            raise ValueError("assumption_prefix cannot contain whitespace")
        floor = self.terminal_book_to_bill_floor
        if not floor.is_finite() or not _ZERO <= floor <= Decimal("2"):
            raise ValueError(
                "terminal_book_to_bill_floor must be finite and within [0, 2]"
            )


def _money(assumption, label: str) -> Measure:
    measure = assumption.measure
    if measure.dimension.value != "money":
        raise ValueError(f"{label} must be a money measure")
    return measure


def _ratio(assumption, label: str) -> Decimal:
    try:
        return assumption.measure.convert_to("ratio").amount
    except ValueError as exc:
        raise ValueError(f"{label} must be convertible to a ratio") from exc


@dataclass(frozen=True)
class BacklogBurnDCFEvaluator:
    archetype: str
    method: str
    version: str
    forecast_years: int
    discount_rate: Decimal
    discount_rate_path_id: str
    assumption_prefix: str = ""
    beta_path_id: str | None = None
    terminal_book_to_bill_floor: Decimal = _ONE

    def __post_init__(self) -> None:
        BacklogBurnRegistration(
            archetype=self.archetype,
            method=self.method,
            version=self.version,
            forecast_years=self.forecast_years,
            assumption_prefix=self.assumption_prefix,
            terminal_book_to_bill_floor=self.terminal_book_to_bill_floor,
        ).validate()
        if not self.discount_rate_path_id:
            raise ValueError("backlog-burn evaluator requires a discount-rate path")
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
        annual = tuple(
            self._key(f"{name}_year_{year}")
            for year in range(1, self.forecast_years + 1)
            for name in ("new_orders", "backlog_burn_rate", "operating_margin")
        )
        return (
            self._key("opening_backlog"),
            self._key("opening_revenue"),
            *annual,
            *(self._key(name) for name in _RATE_KEYS),
            self._key("terminal_growth"),
            self._key("terminal_roic"),
        )

    def _roll_forward(
        self, scenario: BoundScenario
    ) -> tuple[tuple[BacklogYear, ...], str, list]:
        opening_backlog_assumption = scenario.get(self._key("opening_backlog"))
        opening_revenue_assumption = scenario.get(self._key("opening_revenue"))
        opening_backlog_measure = _money(opening_backlog_assumption, "opening backlog")
        unit = opening_backlog_measure.unit
        opening_backlog = opening_backlog_measure.amount
        prior_revenue = _money(
            opening_revenue_assumption, "opening revenue"
        ).convert_to(unit).amount
        if opening_backlog <= 0:
            raise ValueError("opening backlog must be positive")
        if prior_revenue < 0:
            raise ValueError("opening revenue cannot be negative")

        consumed = [opening_backlog_assumption, opening_revenue_assumption]
        rates: dict[str, Decimal] = {}
        for name in _RATE_KEYS:
            assumption = scenario.get(self._key(name))
            consumed.append(assumption)
            rates[name] = _ratio(assumption, name)
        tax_rate = rates["operating_tax_rate"]
        if not _ZERO <= tax_rate < _ONE:
            raise ValueError("operating tax rate must be in [0, 1)")
        for name in (
            "depreciation_rate_of_revenue",
            "maintenance_capex_rate_of_revenue",
            "incremental_working_capital_rate",
        ):
            if rates[name] < _ZERO:
                raise ValueError(f"{name} cannot be negative")

        rows: list[BacklogYear] = []
        backlog = opening_backlog
        for year in range(1, self.forecast_years + 1):
            orders_assumption = scenario.get(self._key(f"new_orders_year_{year}"))
            burn_assumption = scenario.get(self._key(f"backlog_burn_rate_year_{year}"))
            margin_assumption = scenario.get(self._key(f"operating_margin_year_{year}"))
            consumed.extend((orders_assumption, burn_assumption, margin_assumption))

            new_orders = _money(
                orders_assumption, f"new orders (year {year})"
            ).convert_to(unit).amount
            if new_orders < 0:
                raise ValueError(f"new orders cannot be negative in year {year}")
            burn_rate = _ratio(burn_assumption, f"backlog burn rate (year {year})")
            if not _ZERO < burn_rate <= _ONE:
                raise ValueError(
                    f"backlog burn rate must be within (0, 1] in year {year}; "
                    "a year cannot recognise more revenue than its opening backlog"
                )
            margin = _ratio(margin_assumption, f"operating margin (year {year})")
            if margin <= Decimal("-1") or margin > _ONE:
                raise ValueError(f"operating margin is outside (-100%, 100%] in year {year}")

            revenue = backlog * burn_rate
            if revenue <= 0:
                raise ValueError(f"modelled revenue must be positive in year {year}")
            closing = backlog + new_orders - revenue
            operating_profit = revenue * margin
            working_capital = (
                max(_ZERO, revenue - prior_revenue)
                * rates["incremental_working_capital_rate"]
            )
            fcff = (
                operating_profit * (_ONE - tax_rate)
                + revenue * rates["depreciation_rate_of_revenue"]
                - revenue * rates["maintenance_capex_rate_of_revenue"]
                - working_capital
            )
            rows.append(
                BacklogYear(
                    year=year,
                    opening_backlog=backlog,
                    burn_rate=burn_rate,
                    revenue=revenue,
                    new_orders=new_orders,
                    closing_backlog=closing,
                    operating_margin=margin,
                    operating_profit=operating_profit,
                    fcff=fcff,
                )
            )
            backlog = closing
            prior_revenue = revenue
        return tuple(rows), unit, consumed

    def evaluate(self, scenario: BoundScenario, *, segment_id: str) -> SegmentValuation:
        rows, unit, consumed = self._roll_forward(scenario)

        terminal_growth_assumption = scenario.get(self._key("terminal_growth"))
        terminal_roic_assumption = scenario.get(self._key("terminal_roic"))
        consumed.extend((terminal_growth_assumption, terminal_roic_assumption))
        terminal_growth = _ratio(terminal_growth_assumption, "terminal growth")
        terminal_roic = _ratio(terminal_roic_assumption, "terminal ROIC")
        validate_terminal_consistency(
            wacc=float(self.discount_rate),
            terminal_growth=float(terminal_growth),
            terminal_roic=float(terminal_roic),
        )

        final = rows[-1]
        if final.fcff <= 0:
            raise ValueError("Gordon terminal value requires positive final-year FCFF")
        if final.book_to_bill < self.terminal_book_to_bill_floor:
            raise ValueError(
                "perpetual-growth terminal value requires a self-sustaining order book: "
                f"final-year book-to-bill {final.book_to_bill} is below the declared floor "
                f"{self.terminal_book_to_bill_floor}"
            )

        present_value_explicit = _ZERO
        for row in rows:
            present_value_explicit += row.fcff / (_ONE + self.discount_rate) ** row.year
        terminal_value = (
            final.fcff * (_ONE + terminal_growth) / (self.discount_rate - terminal_growth)
        )
        present_value_terminal = (
            terminal_value / (_ONE + self.discount_rate) ** self.forecast_years
        )

        as_of = max(item.measure.as_of for item in consumed)
        upstream_paths = [f"{self.discount_rate_path_id}:{segment_id}"]
        if self.beta_path_id is not None:
            upstream_paths.append(f"{self.beta_path_id}:{segment_id}")
        economic_paths = tuple(
            dict.fromkeys(
                (
                    *(item.economic_path_id for item in consumed),
                    *upstream_paths,
                )
            )
        )
        diagnostics = SegmentValuationDiagnostics(
            execution_family=EXECUTION_FAMILY,
            value_unit=unit,
            discount_rate=self.discount_rate,
            forecast_years=self.forecast_years,
            fcff_path=tuple(row.fcff for row in rows),
            present_value_explicit=present_value_explicit,
            present_value_terminal=present_value_terminal,
            terminal_growth=terminal_growth,
            terminal_roic=terminal_roic,
        )
        diagnostics.validate()
        return SegmentValuation(
            contribution_id=(
                f"{segment_id}:{scenario.scenario_id}:{self.evaluator_id}:v{self.version}"
            ),
            segment_id=segment_id,
            scenario_id=scenario.scenario_id,
            value_kind=ValueKind.ENTERPRISE_VALUE,
            value=Measure(present_value_explicit + present_value_terminal, unit, as_of),
            economic_path_ids=economic_paths,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.version,
            diagnostics=diagnostics,
        )

    def backlog_path(self, scenario: BoundScenario) -> tuple[BacklogYear, ...]:
        """Expose the order-book roll-forward for reporting and provider checks."""
        rows, _, _ = self._roll_forward(scenario)
        return rows


RegistryLoader = Callable[[OrchestratorContext], EvaluatorRegistry]


def live_backlog_burn_registry_loader(
    *,
    registrations: tuple[BacklogBurnRegistration, ...],
    include_default_normalized_multiples: bool = True,
    capability_registry: MethodCapabilityRegistry | None = None,
    base_loader: RegistryLoader | None = None,
) -> RegistryLoader:
    if not registrations:
        raise ValueError("backlog-burn registry loader requires registrations")
    for registration in registrations:
        registration.validate()
        require_execution_family(
            archetype=registration.archetype,
            method=registration.method,
            expected_family=EXECUTION_FAMILY,
            registry=capability_registry,
        )
    keys = tuple(
        ModelKey(item.archetype, item.method, item.version) for item in registrations
    )
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate backlog-burn ModelKey registration")

    def load(context: OrchestratorContext) -> EvaluatorRegistry:
        leaked = tuple(
            sorted(key for key in _FORBIDDEN_PRE_FREEZE_KEYS if key in context.data)
        )
        if leaked:
            raise PermissionError(
                "pre-freeze backlog DCF context contains target Street/market fields: "
                + ", ".join(leaked)
            )
        wacc_result = context.data.get("live_wacc_result")
        if not isinstance(wacc_result, LiveWACCStageResult):
            raise ValueError(
                "LiveWACCStageResult is required to build backlog-burn evaluators"
            )
        discount_rate_float = wacc_result.wacc_result.wacc
        discount_rate = Decimal(str(discount_rate_float))
        if not discount_rate.is_finite() or discount_rate <= 0:
            raise ValueError("live WACC must be finite and positive")

        if base_loader is not None:
            registry = base_loader(context)
        else:
            registry = EvaluatorRegistry()
            if include_default_normalized_multiples:
                registry.register(NormalizedMultipleEvaluator("commodity_price_taker"))
                registry.register(NormalizedMultipleEvaluator("process_spread"))

        for item in registrations:
            registry.register(
                BacklogBurnDCFEvaluator(
                    archetype=item.archetype,
                    method=item.method,
                    version=item.version,
                    forecast_years=item.forecast_years,
                    discount_rate=discount_rate,
                    discount_rate_path_id=f"wacc:{wacc_result.snapshot_hash}",
                    beta_path_id=f"beta:{wacc_result.beta_result.snapshot_hash}",
                    assumption_prefix=item.assumption_prefix,
                    terminal_book_to_bill_floor=item.terminal_book_to_bill_floor,
                )
            )
        return registry

    return load
