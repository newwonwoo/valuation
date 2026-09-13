"""Auditable annual contracted-business FCFF proposals, never equity values.

A path covers one economic mechanism: opening firm backlog plus explicit new
firm bookings. Speculative/spot demand needs a separately evidenced path. Revenue
is capped by demand, remaining backlog and executable capacity. Unused backlog
carries forward; demand timing does not silently carry forward. Capacity is an
annual nameplate level; price is money per capacity unit per year. A commissioning
delay shifts capacity only. Other schedules (including CAPEX and depreciation)
are explicit calendar-year inputs and must be amended separately if they change.

All numeric inputs, including explicit zeroes, have scoped evidence/assumption
references. Those IDs must resolve in the caller's frozen evidence ledger; this
module checks presence/scope, not source truth. No confidence or probability is
inferred here. Final model authority remains Compiler/Evaluator/Audit/Freeze.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Any, Mapping

from .actual_units import Dimension, unit_def


class BusinessResearchError(ValueError):
    """A research proposal is incomplete or dimensionally inconsistent."""


@dataclass(frozen=True)
class DriverEstimate:
    value: Decimal
    unit: str
    economic_path_id: str
    source_refs: tuple[str, ...]
    assumption_refs: tuple[str, ...]

    def validate(self, path_id: str, unit: str, name: str) -> None:
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise BusinessResearchError(f"{name}: finite Decimal required")
        if self.unit != unit:
            raise BusinessResearchError(f"{name}: expected unit {unit}, got {self.unit}")
        if not path_id.strip() or self.economic_path_id != path_id:
            raise BusinessResearchError(f"{name}: economic path scope mismatch")
        for refs in (self.source_refs, self.assumption_refs):
            if not isinstance(refs, tuple) or not refs or any(
                not isinstance(ref, str) or not ref.strip() for ref in refs
            ):
                raise BusinessResearchError(f"{name}: source and assumption references required")


@dataclass(frozen=True)
class BusinessPeriod:
    year: int
    backlog_additions: DriverEstimate
    revenue_demand: DriverEstimate
    capacity: DriverEstimate
    utilization: DriverEstimate
    price_per_capacity_year: DriverEstimate
    cash_cost_ratio: DriverEstimate
    fixed_cash_costs: DriverEstimate
    depreciation: DriverEstimate
    tax_rate: DriverEstimate
    capex: DriverEstimate
    delta_nwc: DriverEstimate


@dataclass(frozen=True)
class BusinessPath:
    economic_path_id: str
    money_unit: str
    capacity_unit: str
    opening_backlog: DriverEstimate
    precommissioning_capacity: DriverEstimate
    commissioning_delay_years: DriverEstimate
    loss_tax_treatment: str
    periods: tuple[BusinessPeriod, ...]

    def validate(self) -> None:
        if unit_def(self.money_unit).dimension != Dimension.MONEY:
            raise BusinessResearchError("money_unit must be monetary")
        if unit_def(self.capacity_unit).dimension not in (
            Dimension.POWER, Dimension.COUNT, Dimension.MASS, Dimension.AREA
        ):
            raise BusinessResearchError("capacity_unit must describe physical capacity")
        if self.loss_tax_treatment not in ("no_current_benefit", "immediate_relief"):
            raise BusinessResearchError("explicit loss tax treatment required")
        for name, estimate, unit in (
            ("opening_backlog", self.opening_backlog, self.money_unit),
            ("precommissioning_capacity", self.precommissioning_capacity, self.capacity_unit),
            ("commissioning_delay_years", self.commissioning_delay_years, "years"),
        ):
            estimate.validate(self.economic_path_id, unit, name)
            if estimate.value < 0:
                raise BusinessResearchError(f"{name}: cannot be negative")
        delay = self.commissioning_delay_years.value
        if delay != delay.to_integral_value():
            raise BusinessResearchError("commissioning delay must be whole years")
        if not self.periods:
            raise BusinessResearchError("annual periods required")
        prior_year = None
        for period in self.periods:
            if type(period.year) is not int or not 1 <= period.year <= 9999:
                raise BusinessResearchError("period year must be a calendar year")
            if prior_year is not None and period.year != prior_year + 1:
                raise BusinessResearchError("periods must be consecutive annual years")
            prior_year = period.year
            for name, estimate in _period_drivers(period):
                if name == "capacity":
                    unit = self.capacity_unit
                elif name == "price_per_capacity_year":
                    unit = f"{self.money_unit}_per_{self.capacity_unit}_year"
                elif name in ("utilization", "cash_cost_ratio", "tax_rate"):
                    unit = "ratio"
                else:
                    unit = self.money_unit
                estimate.validate(self.economic_path_id, unit, f"{period.year}.{name}")
                if name != "delta_nwc" and estimate.value < 0:
                    raise BusinessResearchError(f"{period.year}.{name}: cannot be negative")
                # Cash costs can exceed revenue; do not cap loss-making ramp years.
                if name in ("utilization", "tax_rate") and estimate.value > 1:
                    raise BusinessResearchError(f"{period.year}.{name}: ratio exceeds one")


@dataclass(frozen=True)
class CashflowPeriod:
    year: int
    opening_backlog: Decimal
    backlog_additions: Decimal
    executable_capacity: Decimal
    capacity_revenue_cap: Decimal
    revenue: Decimal
    closing_backlog: Decimal
    cash_costs: Decimal
    depreciation: Decimal
    ebit: Decimal
    cash_taxes: Decimal
    capex: Decimal
    delta_nwc: Decimal
    fcff: Decimal
    source_refs: tuple[str, ...]
    assumption_refs: tuple[str, ...]


@dataclass(frozen=True)
class CashflowProposal:
    economic_path_id: str
    money_unit: str
    capacity_unit: str
    periods: tuple[CashflowPeriod, ...]
    loss_tax_treatment: str

    @property
    def status(self) -> str:
        return "RESEARCH_ESTIMATE"

    @property
    def fcff_path(self) -> tuple[Decimal, ...]:
        return tuple(period.fcff for period in self.periods)

    def underwriting_rows(self, prefix: str = "fcff_year_") -> dict[str, dict[str, Any]]:
        if not isinstance(prefix, str) or not prefix.strip():
            raise BusinessResearchError("nonempty FCFF metric prefix required")
        return {
            f"{prefix}{index}": {
                "value": str(period.fcff),
                "unit": self.money_unit,
                "year": period.year,
                "economic_path_id": self.economic_path_id,
                "authority": "analyst_declared",
                "status": self.status,
                "source_refs": list(period.source_refs),
                "assumption_refs": list(period.assumption_refs),
                "rationale": (
                    "Research estimate: revenue=min(demand, remaining firm backlog, "
                    "executable capacity*utilization*annual price); "
                    "FCFF=EBIT-cash taxes+depreciation-CAPEX-deltaNWC. "
                    f"Loss tax treatment={self.loss_tax_treatment}; "
                    "calendar-year CAPEX is not automatically shifted by commissioning delay."
                ),
            }
            for index, period in enumerate(self.periods, start=1)
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "economic_path_id": self.economic_path_id,
            "money_unit": self.money_unit,
            "capacity_unit": self.capacity_unit,
            "loss_tax_treatment": self.loss_tax_treatment,
            "fcff_path": [str(value) for value in self.fcff_path],
            "periods": [
                {field.name: _primitive(getattr(period, field.name)) for field in fields(period)}
                for period in self.periods
            ],
            "underwriting_rows": self.underwriting_rows(),
        }


def _primitive(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    return value


def _period_drivers(period: BusinessPeriod) -> tuple[tuple[str, DriverEstimate], ...]:
    return tuple((field.name, getattr(period, field.name)) for field in fields(period) if field.name != "year")


def build_cashflow_proposal(path: BusinessPath) -> CashflowProposal:
    path.validate()
    backlog = path.opening_backlog.value
    delay = int(path.commissioning_delay_years.value)
    result: list[CashflowPeriod] = []
    # Prior drivers affect remaining backlog: retain them in downstream lineage.
    lineage = [path.opening_backlog, path.commissioning_delay_years]
    for index, period in enumerate(path.periods):
        capacity = (
            path.periods[index - delay].capacity
            if index >= delay else path.precommissioning_capacity
        )
        lineage.extend(estimate for _, estimate in _period_drivers(period))
        lineage.append(capacity)
        capacity_cap = capacity.value * period.utilization.value * period.price_per_capacity_year.value
        available_backlog = backlog + period.backlog_additions.value
        revenue = min(period.revenue_demand.value, capacity_cap, available_backlog)
        cash_costs = revenue * period.cash_cost_ratio.value + period.fixed_cash_costs.value
        ebit = revenue - cash_costs - period.depreciation.value
        taxable = ebit if path.loss_tax_treatment == "immediate_relief" else max(Decimal(0), ebit)
        taxes = taxable * period.tax_rate.value
        fcff = ebit - taxes + period.depreciation.value - period.capex.value - period.delta_nwc.value
        result.append(CashflowPeriod(
            year=period.year, opening_backlog=backlog,
            backlog_additions=period.backlog_additions.value,
            executable_capacity=capacity.value, capacity_revenue_cap=capacity_cap,
            revenue=revenue, closing_backlog=available_backlog - revenue,
            cash_costs=cash_costs, depreciation=period.depreciation.value,
            ebit=ebit, cash_taxes=taxes, capex=period.capex.value,
            delta_nwc=period.delta_nwc.value, fcff=fcff,
            source_refs=tuple(sorted({ref for estimate in lineage for ref in estimate.source_refs})),
            assumption_refs=tuple(sorted({ref for estimate in lineage for ref in estimate.assumption_refs})),
        ))
        backlog = available_backlog - revenue
    return CashflowProposal(path.economic_path_id, path.money_unit, path.capacity_unit, tuple(result), path.loss_tax_treatment)


def business_path_from_dict(payload: Mapping[str, Any]) -> BusinessPath:
    """Strict JSON ingress: every economic parameter must be supplied explicitly."""
    def exact_keys(raw: Mapping[str, Any], names: set[str], label: str) -> None:
        if not isinstance(raw, Mapping) or set(raw) != names:
            raise BusinessResearchError(f"{label}: requires exactly {sorted(names)}")

    def driver(raw: Mapping[str, Any]) -> DriverEstimate:
        exact_keys(raw, {field.name for field in fields(DriverEstimate)}, "driver")
        value = raw["value"]
        if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
            raise BusinessResearchError("driver: use decimal strings, not binary floats")
        try:
            decimal = Decimal(value)
        except Exception as exc:
            raise BusinessResearchError("driver: invalid decimal") from exc
        for key in ("source_refs", "assumption_refs"):
            if not isinstance(raw[key], (tuple, list)):
                raise BusinessResearchError(f"driver: {key} must be an array")
        return DriverEstimate(decimal, raw["unit"], raw["economic_path_id"], tuple(raw["source_refs"]), tuple(raw["assumption_refs"]))

    exact_keys(payload, {field.name for field in fields(BusinessPath)}, "business path")
    if not isinstance(payload["periods"], (tuple, list)):
        raise BusinessResearchError("periods must be an array")
    periods = []
    for raw in payload["periods"]:
        exact_keys(raw, {field.name for field in fields(BusinessPeriod)}, "period")
        periods.append(BusinessPeriod(year=raw["year"], **{name: driver(value) for name, value in raw.items() if name != "year"}))
    path = BusinessPath(
        economic_path_id=payload["economic_path_id"], money_unit=payload["money_unit"],
        capacity_unit=payload["capacity_unit"], opening_backlog=driver(payload["opening_backlog"]),
        precommissioning_capacity=driver(payload["precommissioning_capacity"]),
        commissioning_delay_years=driver(payload["commissioning_delay_years"]),
        loss_tax_treatment=payload["loss_tax_treatment"], periods=tuple(periods),
    )
    path.validate()
    return path


def business_path_snapshot_hash(payload: Mapping[str, Any]) -> str:
    """Hash the exact JSON model snapshot, without certifying its factual inputs."""
    import hashlib
    import json

    try:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BusinessResearchError("business path snapshot must be finite JSON") from exc
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def validate_cashflow_receipt(
    receipt: Mapping[str, Any], metric: str, value: str, unit: str
) -> None:
    """Verify reproducibility of a derived FCFF row, not truth of its assumptions.

    The campaign owns research-response validation and binding to its real plan.
    A plan hash here is a required lineage anchor, not proof of authorization.
    All baseline and revised driver inputs remain in the full path snapshot, so
    this helper cannot silently substitute defaults. ``assumptions`` may retain
    upstream research receipts but is never treated as independently verified.
    Existing compiler/evidence audit still owns admission and factual provenance.
    """
    import re

    if not isinstance(receipt, Mapping) or receipt.get("schema_version") != "business-cashflow-receipt/v1":
        raise BusinessResearchError("unsupported business cashflow receipt schema")
    for name in ("plan_hash", "path_hash"):
        if not isinstance(receipt.get(name), str) or re.fullmatch(r"[0-9a-f]{64}", receipt[name]) is None:
            raise BusinessResearchError(f"receipt {name} must be a SHA256 lineage hash")
    payload = receipt.get("path")
    if not isinstance(payload, Mapping):
        raise BusinessResearchError("receipt requires the full business path snapshot")
    if business_path_snapshot_hash(payload) != receipt["path_hash"]:
        raise BusinessResearchError("receipt business path snapshot hash mismatch")
    prefix = receipt.get("metric_prefix")
    if not isinstance(prefix, str) or not prefix.strip():
        raise BusinessResearchError("receipt requires explicit metric_prefix")
    proposal = build_cashflow_proposal(business_path_from_dict(payload))
    rows = proposal.underwriting_rows(prefix=prefix)
    if not isinstance(metric, str) or metric not in rows:
        raise BusinessResearchError("receipt metric is not produced by the business path")
    if not isinstance(value, str):
        raise BusinessResearchError("receipt row value must be a decimal string")
    try:
        amount = Decimal(value)
    except Exception as exc:
        raise BusinessResearchError("receipt row value must be a finite decimal") from exc
    row = rows[metric]
    if not amount.is_finite() or amount != Decimal(row["value"]):
        raise BusinessResearchError("receipt FCFF value does not reproduce")
    if unit != row["unit"]:
        raise BusinessResearchError("receipt FCFF unit does not reproduce")
    if "source_refs" in receipt:
        source_refs = receipt["source_refs"]
        if not isinstance(source_refs, (list, tuple)) or any(not isinstance(ref, str) for ref in source_refs):
            raise BusinessResearchError("receipt source_refs must be an array of references")
        if set(source_refs) != set(row["source_refs"]):
            raise BusinessResearchError("receipt FCFF source lineage does not reproduce")


def cashflow_path_hash(path: Mapping[str, Any]) -> str:
    """Public receipt-construction alias for the canonical path snapshot hash."""
    return business_path_snapshot_hash(path)
