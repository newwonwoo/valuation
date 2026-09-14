"""Liquidity, lease, refinancing, dilution and recovery-waterfall paths.

This is the financing half of the ``capacity_yield_levered`` archetype.  It is
independent of the operating sector: company disclosures determine schedules,
limits and recovery inputs; the action order and arithmetic are deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


ZERO = Decimal("0")
ONE = Decimal("1")


class FinancingPathError(ValueError):
    """Raised when financing inputs cannot be reconciled."""


class ClaimType(str, Enum):
    DEBT = "DEBT"
    LEASE = "LEASE"
    REFINANCING = "REFINANCING"


class FinancingActionType(str, Enum):
    REFINANCING = "REFINANCING"
    ASSET_SALE = "ASSET_SALE"
    EQUITY_RAISE = "EQUITY_RAISE"
    DISTRESS = "DISTRESS"


def _decimal(value: Decimal, label: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{label} must be Decimal")
    if not value.is_finite():
        raise FinancingPathError(f"{label} must be finite")
    return value


def _ratio(value: Decimal, label: str) -> Decimal:
    _decimal(value, label)
    if not ZERO <= value <= ONE:
        raise FinancingPathError(f"{label} must lie within [0,1]")
    return value


@dataclass(frozen=True)
class DebtPeriod:
    period: int
    opening_principal: Decimal
    new_borrowing: Decimal
    interest_due: Decimal
    principal_due: Decimal
    closing_principal: Decimal

    def validate(self) -> None:
        if self.period <= 0:
            raise FinancingPathError("debt period must be positive")
        for name in (
            "opening_principal",
            "new_borrowing",
            "interest_due",
            "principal_due",
            "closing_principal",
        ):
            value = _decimal(getattr(self, name), f"debt {name}")
            if value < ZERO:
                raise FinancingPathError(f"debt {name} cannot be negative")
        if self.opening_principal + self.new_borrowing - self.principal_due != self.closing_principal:
            raise FinancingPathError("debt principal roll-forward does not balance")


@dataclass(frozen=True)
class DebtSchedule:
    claim_id: str
    seniority: int
    periods: tuple[DebtPeriod, ...]

    def validate(self, horizon: int) -> None:
        if not self.claim_id or self.seniority < 0 or len(self.periods) != horizon:
            raise FinancingPathError("debt schedule identity or horizon is invalid")
        if tuple(item.period for item in self.periods) != tuple(range(1, horizon + 1)):
            raise FinancingPathError("debt schedule periods must be contiguous")
        for item in self.periods:
            item.validate()
        if any(
            right.opening_principal != left.closing_principal
            for left, right in zip(self.periods, self.periods[1:])
        ):
            raise FinancingPathError("debt schedule opening and closing balances do not chain")


@dataclass(frozen=True)
class LeasePeriod:
    period: int
    opening_liability: Decimal
    new_lease_additions: Decimal
    imputed_interest: Decimal
    lease_payment: Decimal
    closing_liability: Decimal

    def validate(self) -> None:
        if self.period <= 0:
            raise FinancingPathError("lease period must be positive")
        for name in (
            "opening_liability",
            "new_lease_additions",
            "imputed_interest",
            "lease_payment",
            "closing_liability",
        ):
            value = _decimal(getattr(self, name), f"lease {name}")
            if value < ZERO:
                raise FinancingPathError(f"lease {name} cannot be negative")
        if (
            self.opening_liability
            + self.new_lease_additions
            + self.imputed_interest
            - self.lease_payment
            != self.closing_liability
        ):
            raise FinancingPathError("lease liability roll-forward does not balance")


@dataclass(frozen=True)
class LeaseSchedule:
    claim_id: str
    seniority: int
    periods: tuple[LeasePeriod, ...]

    def validate(self, horizon: int) -> None:
        if not self.claim_id or self.seniority < 0 or len(self.periods) != horizon:
            raise FinancingPathError("lease schedule identity or horizon is invalid")
        if tuple(item.period for item in self.periods) != tuple(range(1, horizon + 1)):
            raise FinancingPathError("lease schedule periods must be contiguous")
        for item in self.periods:
            item.validate()
        if any(
            right.opening_liability != left.closing_liability
            for left, right in zip(self.periods, self.periods[1:])
        ):
            raise FinancingPathError("lease schedule opening and closing balances do not chain")


@dataclass(frozen=True)
class LeaseReconciliation:
    opening_liability: Decimal
    additions: Decimal
    imputed_interest: Decimal
    payments: Decimal
    closing_liability: Decimal
    balanced: bool


def reconcile_lease_schedule(schedule: LeaseSchedule) -> LeaseReconciliation:
    if not schedule.periods:
        raise FinancingPathError("lease schedule cannot be empty")
    schedule.validate(len(schedule.periods))
    opening = schedule.periods[0].opening_liability
    additions = sum((item.new_lease_additions for item in schedule.periods), ZERO)
    interest = sum((item.imputed_interest for item in schedule.periods), ZERO)
    payments = sum((item.lease_payment for item in schedule.periods), ZERO)
    closing = schedule.periods[-1].closing_liability
    return LeaseReconciliation(
        opening_liability=opening,
        additions=additions,
        imputed_interest=interest,
        payments=payments,
        closing_liability=closing,
        balanced=opening + additions + interest - payments == closing,
    )


@dataclass(frozen=True)
class RefinancingFacility:
    facility_id: str
    seniority: int
    gross_capacity_by_period: tuple[Decimal, ...]
    transaction_cost_rate: Decimal
    cash_interest_rate: Decimal = ZERO

    def validate(self, horizon: int) -> None:
        if not self.facility_id or self.seniority < 0 or len(self.gross_capacity_by_period) != horizon:
            raise FinancingPathError("refinancing facility identity or horizon is invalid")
        _ratio(self.transaction_cost_rate, "refinancing transaction cost rate")
        _ratio(self.cash_interest_rate, "refinancing cash interest rate")
        if self.transaction_cost_rate == ONE:
            raise FinancingPathError("refinancing transaction cost cannot consume all proceeds")
        for value in self.gross_capacity_by_period:
            if _decimal(value, "refinancing capacity") < ZERO:
                raise FinancingPathError("refinancing capacity cannot be negative")


@dataclass(frozen=True)
class AssetSalePolicy:
    gross_capacity_by_period: tuple[Decimal, ...]
    transaction_cost_rate: Decimal

    def validate(self, horizon: int) -> None:
        if len(self.gross_capacity_by_period) != horizon:
            raise FinancingPathError("asset-sale policy horizon mismatch")
        _ratio(self.transaction_cost_rate, "asset-sale transaction cost rate")
        if self.transaction_cost_rate == ONE:
            raise FinancingPathError("asset-sale transaction cost cannot consume all proceeds")
        if any(_decimal(value, "asset-sale capacity") < ZERO for value in self.gross_capacity_by_period):
            raise FinancingPathError("asset-sale capacity cannot be negative")


@dataclass(frozen=True)
class EquityRaisePolicy:
    gross_capacity_by_period: tuple[Decimal, ...]
    transaction_cost_rate: Decimal
    dilution_at_full_raise_by_period: tuple[Decimal, ...]

    def validate(self, horizon: int) -> None:
        if (
            len(self.gross_capacity_by_period) != horizon
            or len(self.dilution_at_full_raise_by_period) != horizon
        ):
            raise FinancingPathError("equity-raise policy horizon mismatch")
        _ratio(self.transaction_cost_rate, "equity transaction cost rate")
        if self.transaction_cost_rate == ONE:
            raise FinancingPathError("equity transaction cost cannot consume all proceeds")
        for capacity, dilution in zip(
            self.gross_capacity_by_period, self.dilution_at_full_raise_by_period
        ):
            if _decimal(capacity, "equity capacity") < ZERO:
                raise FinancingPathError("equity capacity cannot be negative")
            _ratio(dilution, "equity dilution")
            if capacity == ZERO and dilution != ZERO:
                raise FinancingPathError("zero equity capacity cannot carry dilution")


@dataclass(frozen=True)
class RecoveryWaterfallPolicy:
    fixed_distress_cost: Decimal
    distress_cost_rate: Decimal
    old_shareholder_retention: Decimal

    def validate(self) -> None:
        if _decimal(self.fixed_distress_cost, "fixed distress cost") < ZERO:
            raise FinancingPathError("fixed distress cost cannot be negative")
        _ratio(self.distress_cost_rate, "distress cost rate")
        _ratio(self.old_shareholder_retention, "old-shareholder retention")


@dataclass(frozen=True)
class FinancingPathSpec:
    opening_cash: Decimal
    minimum_operating_cash: Decimal
    debt_schedules: tuple[DebtSchedule, ...]
    lease_schedules: tuple[LeaseSchedule, ...]
    refinancing_facilities: tuple[RefinancingFacility, ...]
    asset_sale_policy: AssetSalePolicy
    equity_raise_policy: EquityRaisePolicy
    recovery_waterfall: RecoveryWaterfallPolicy

    def validate(self, horizon: int) -> None:
        if _decimal(self.opening_cash, "opening cash") < ZERO:
            raise FinancingPathError("opening cash cannot be negative")
        if _decimal(self.minimum_operating_cash, "minimum operating cash") < ZERO:
            raise FinancingPathError("minimum operating cash cannot be negative")
        for item in self.debt_schedules:
            item.validate(horizon)
        for item in self.lease_schedules:
            item.validate(horizon)
        for item in self.refinancing_facilities:
            item.validate(horizon)
        claim_ids = tuple(
            item.claim_id for item in self.debt_schedules + self.lease_schedules
        )
        facility_ids = tuple(item.facility_id for item in self.refinancing_facilities)
        if len(claim_ids + facility_ids) != len(set(claim_ids + facility_ids)):
            raise FinancingPathError("financing spec repeats a claim/facility ID")
        self.asset_sale_policy.validate(horizon)
        self.equity_raise_policy.validate(horizon)
        self.recovery_waterfall.validate()


@dataclass(frozen=True)
class FinancingPeriodInput:
    period: int
    operating_cash_flow: Decimal
    mandatory_capex: Decimal
    distress_asset_proceeds: Decimal
    taxable_income_before_interest: Decimal = ZERO
    tax_rate: Decimal = ZERO

    def validate(self) -> None:
        if self.period <= 0:
            raise FinancingPathError("financing input period must be positive")
        _decimal(self.operating_cash_flow, "operating cash flow")
        if _decimal(self.mandatory_capex, "mandatory capex") < ZERO:
            raise FinancingPathError("mandatory capex cannot be negative")
        if _decimal(self.distress_asset_proceeds, "distress asset proceeds") < ZERO:
            raise FinancingPathError("distress asset proceeds cannot be negative")
        _decimal(self.taxable_income_before_interest, "taxable income before interest")
        _ratio(self.tax_rate, "financing-path tax rate")


@dataclass(frozen=True)
class FinancingAction:
    action_type: FinancingActionType
    source_id: str
    gross_amount: Decimal
    net_cash: Decimal
    transaction_cost: Decimal
    dilution: Decimal = ZERO


@dataclass(frozen=True)
class ClaimBalance:
    claim_id: str
    claim_type: ClaimType
    seniority: int
    amount: Decimal


@dataclass(frozen=True)
class WaterfallAllocation:
    claim_id: str
    claim_type: ClaimType
    seniority: int
    claim_amount: Decimal
    recovery_amount: Decimal


@dataclass(frozen=True)
class DistressRecovery:
    period: int
    gross_asset_proceeds: Decimal
    distress_cost: Decimal
    creditor_allocations: tuple[WaterfallAllocation, ...]
    residual_equity: Decimal
    old_shareholder_recovery: Decimal
    old_shareholder_retention: Decimal


@dataclass(frozen=True)
class FinancingPeriodResult:
    period: int
    opening_cash: Decimal
    operating_cash_flow: Decimal
    scheduled_new_borrowing: Decimal
    debt_interest: Decimal
    lease_interest: Decimal
    cash_tax_shield: Decimal
    debt_principal_due: Decimal
    lease_payment: Decimal
    mandatory_capex: Decimal
    pre_action_liquidity: Decimal
    actions: tuple[FinancingAction, ...]
    ending_cash: Decimal
    old_shareholder_ownership: Decimal
    distressed: bool


@dataclass(frozen=True)
class FinancingPathResult:
    periods: tuple[FinancingPeriodResult, ...]
    distressed: bool
    distress_period: int | None
    dilution_occurred: bool
    old_shareholder_ownership: Decimal
    ending_cash: Decimal
    horizon_claims: tuple[ClaimBalance, ...]
    recovery: DistressRecovery | None
    explicit_financing_costs: tuple[Decimal, ...]

    @property
    def horizon_senior_claims(self) -> Decimal:
        return sum((item.amount for item in self.horizon_claims), ZERO)


def apply_recovery_waterfall(
    *,
    period: int,
    gross_asset_proceeds: Decimal,
    claims: tuple[ClaimBalance, ...],
    policy: RecoveryWaterfallPolicy,
    old_shareholder_ownership: Decimal,
) -> DistressRecovery:
    """Apply limited liability only at an explicit distress waterfall."""

    if period <= 0:
        raise FinancingPathError("distress period must be positive")
    if _decimal(gross_asset_proceeds, "distress asset proceeds") < ZERO:
        raise FinancingPathError("distress asset proceeds cannot be negative")
    _ratio(old_shareholder_ownership, "old-shareholder ownership")
    policy.validate()
    ids = tuple(item.claim_id for item in claims)
    if len(ids) != len(set(ids)):
        raise FinancingPathError("recovery waterfall repeats a claim")
    for item in claims:
        if not item.claim_id or item.seniority < 0 or _decimal(item.amount, "claim amount") < ZERO:
            raise FinancingPathError("recovery waterfall contains an invalid claim")

    distress_cost = min(
        gross_asset_proceeds,
        policy.fixed_distress_cost + gross_asset_proceeds * policy.distress_cost_rate,
    )
    remaining = gross_asset_proceeds - distress_cost
    allocations: list[WaterfallAllocation] = []
    ordered_claims = tuple(sorted(claims, key=lambda item: (item.seniority, item.claim_id)))
    for seniority in sorted({item.seniority for item in ordered_claims}):
        tier = tuple(item for item in ordered_claims if item.seniority == seniority)
        tier_claim = sum((item.amount for item in tier), ZERO)
        tier_recovery = min(tier_claim, remaining)
        remaining -= tier_recovery
        for claim in tier:
            recovery = (
                ZERO if tier_claim == ZERO else tier_recovery * claim.amount / tier_claim
            )
            allocations.append(
                WaterfallAllocation(
                    claim_id=claim.claim_id,
                    claim_type=claim.claim_type,
                    seniority=claim.seniority,
                    claim_amount=claim.amount,
                    recovery_amount=recovery,
                )
            )
    # This is the allowed future-state limited-liability floor.  No valuation
    # or report-time scenario value is transformed here.
    residual_equity = max(remaining, ZERO)
    retention = old_shareholder_ownership * policy.old_shareholder_retention
    return DistressRecovery(
        period=period,
        gross_asset_proceeds=gross_asset_proceeds,
        distress_cost=distress_cost,
        creditor_allocations=tuple(allocations),
        residual_equity=residual_equity,
        old_shareholder_recovery=residual_equity * retention,
        old_shareholder_retention=retention,
    )


def evaluate_financing_path(
    *,
    inputs: tuple[FinancingPeriodInput, ...],
    spec: FinancingPathSpec,
) -> FinancingPathResult:
    """Run refinance -> asset sale -> equity -> distress for one path."""

    if not inputs or tuple(item.period for item in inputs) != tuple(range(1, len(inputs) + 1)):
        raise FinancingPathError("financing inputs must be contiguous from period one")
    for item in inputs:
        item.validate()
    horizon = len(inputs)
    spec.validate(horizon)

    cash = spec.opening_cash
    old_ownership = ONE
    refinancing_claims: dict[str, ClaimBalance] = {}
    facility_by_id = {item.facility_id: item for item in spec.refinancing_facilities}
    period_results: list[FinancingPeriodResult] = []
    financing_costs: list[Decimal] = []
    recovery: DistressRecovery | None = None
    distressed = False
    distress_period: int | None = None

    for index, path_input in enumerate(inputs):
        opening_cash = cash
        scheduled_new_borrowing = sum(
            (schedule.periods[index].new_borrowing for schedule in spec.debt_schedules), ZERO
        )
        debt_interest = sum(
            (schedule.periods[index].interest_due for schedule in spec.debt_schedules), ZERO
        )
        debt_interest += sum(
            (
                claim.amount * facility_by_id[claim.claim_id.rsplit(":", 1)[0]].cash_interest_rate
                for claim in refinancing_claims.values()
            ),
            ZERO,
        )
        debt_principal = sum(
            (schedule.periods[index].principal_due for schedule in spec.debt_schedules), ZERO
        )
        lease_payment = sum(
            (schedule.periods[index].lease_payment for schedule in spec.lease_schedules), ZERO
        )
        lease_interest = sum(
            (schedule.periods[index].imputed_interest for schedule in spec.lease_schedules), ZERO
        )
        deductible_interest = debt_interest + lease_interest
        cash_tax_shield = (
            min(max(path_input.taxable_income_before_interest, ZERO), deductible_interest)
            * path_input.tax_rate
        )
        pre_action = (
            opening_cash
            + path_input.operating_cash_flow
            + scheduled_new_borrowing
            + cash_tax_shield
            - debt_interest
            - debt_principal
            - lease_payment
            - path_input.mandatory_capex
            - spec.minimum_operating_cash
        )
        shortfall = max(-pre_action, ZERO)
        actions: list[FinancingAction] = []

        for facility in spec.refinancing_facilities:
            if shortfall == ZERO:
                break
            gross, net, cost = _draw_capacity(
                shortfall,
                facility.gross_capacity_by_period[index],
                facility.transaction_cost_rate,
            )
            if gross == ZERO:
                continue
            claim_id = f"{facility.facility_id}:{path_input.period}"
            refinancing_claims[claim_id] = ClaimBalance(
                claim_id=claim_id,
                claim_type=ClaimType.REFINANCING,
                seniority=facility.seniority,
                amount=gross,
            )
            if cost != ZERO:
                financing_costs.append(cost)
            actions.append(
                FinancingAction(
                    action_type=FinancingActionType.REFINANCING,
                    source_id=facility.facility_id,
                    gross_amount=gross,
                    net_cash=net,
                    transaction_cost=cost,
                )
            )
            shortfall = max(shortfall - net, ZERO)

        if shortfall > ZERO:
            gross, net, cost = _draw_capacity(
                shortfall,
                spec.asset_sale_policy.gross_capacity_by_period[index],
                spec.asset_sale_policy.transaction_cost_rate,
            )
            if gross > ZERO:
                if cost != ZERO:
                    financing_costs.append(cost)
                actions.append(
                    FinancingAction(
                        action_type=FinancingActionType.ASSET_SALE,
                        source_id="NON_CORE_ASSET_SALE",
                        gross_amount=gross,
                        net_cash=net,
                        transaction_cost=cost,
                    )
                )
                shortfall = max(shortfall - net, ZERO)

        if shortfall > ZERO:
            equity_capacity = spec.equity_raise_policy.gross_capacity_by_period[index]
            gross, net, cost = _draw_capacity(
                shortfall,
                equity_capacity,
                spec.equity_raise_policy.transaction_cost_rate,
            )
            if gross > ZERO:
                full_dilution = spec.equity_raise_policy.dilution_at_full_raise_by_period[index]
                dilution = full_dilution * gross / equity_capacity
                old_ownership *= ONE - dilution
                if cost != ZERO:
                    financing_costs.append(cost)
                actions.append(
                    FinancingAction(
                        action_type=FinancingActionType.EQUITY_RAISE,
                        source_id="EXTERNAL_EQUITY",
                        gross_amount=gross,
                        net_cash=net,
                        transaction_cost=cost,
                        dilution=dilution,
                    )
                )
                shortfall = max(shortfall - net, ZERO)

        action_net_cash = sum((item.net_cash for item in actions), ZERO)
        if shortfall > ZERO:
            distressed = True
            distress_period = path_input.period
            claims = _claims_at_period(spec, index, tuple(refinancing_claims.values()))
            recovery = apply_recovery_waterfall(
                period=path_input.period,
                gross_asset_proceeds=path_input.distress_asset_proceeds,
                claims=claims,
                policy=spec.recovery_waterfall,
                old_shareholder_ownership=old_ownership,
            )
            actions.append(
                FinancingAction(
                    action_type=FinancingActionType.DISTRESS,
                    source_id="RECOVERY_WATERFALL",
                    gross_amount=ZERO,
                    net_cash=ZERO,
                    transaction_cost=ZERO,
                )
            )
            cash = ZERO
        else:
            cash = pre_action + action_net_cash + spec.minimum_operating_cash
        period_results.append(
            FinancingPeriodResult(
                period=path_input.period,
                opening_cash=opening_cash,
                operating_cash_flow=path_input.operating_cash_flow,
                scheduled_new_borrowing=scheduled_new_borrowing,
                debt_interest=debt_interest,
                lease_interest=lease_interest,
                cash_tax_shield=cash_tax_shield,
                debt_principal_due=debt_principal,
                lease_payment=lease_payment,
                mandatory_capex=path_input.mandatory_capex,
                pre_action_liquidity=pre_action,
                actions=tuple(actions),
                ending_cash=cash,
                old_shareholder_ownership=old_ownership,
                distressed=distressed,
            )
        )
        if distressed:
            break

    horizon_index = (distress_period - 1) if distress_period is not None else horizon - 1
    claims = _claims_at_period(spec, horizon_index, tuple(refinancing_claims.values()))
    return FinancingPathResult(
        periods=tuple(period_results),
        distressed=distressed,
        distress_period=distress_period,
        dilution_occurred=old_ownership < ONE,
        old_shareholder_ownership=old_ownership,
        ending_cash=cash,
        horizon_claims=claims,
        recovery=recovery,
        explicit_financing_costs=tuple(financing_costs),
    )


def _draw_capacity(
    net_need: Decimal, gross_capacity: Decimal, transaction_cost_rate: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    if net_need <= ZERO or gross_capacity <= ZERO:
        return ZERO, ZERO, ZERO
    net_factor = ONE - transaction_cost_rate
    gross = min(gross_capacity, net_need / net_factor)
    cost = gross * transaction_cost_rate
    return gross, gross - cost, cost


def _claims_at_period(
    spec: FinancingPathSpec,
    index: int,
    refinancing_claims: tuple[ClaimBalance, ...],
) -> tuple[ClaimBalance, ...]:
    claims = [
        ClaimBalance(
            claim_id=schedule.claim_id,
            claim_type=ClaimType.DEBT,
            seniority=schedule.seniority,
            amount=schedule.periods[index].closing_principal,
        )
        for schedule in spec.debt_schedules
    ]
    claims.extend(
        ClaimBalance(
            claim_id=schedule.claim_id,
            claim_type=ClaimType.LEASE,
            seniority=schedule.seniority,
            amount=schedule.periods[index].closing_liability,
        )
        for schedule in spec.lease_schedules
    )
    claims.extend(refinancing_claims)
    return tuple(sorted(claims, key=lambda item: (item.seniority, item.claim_id)))


__all__ = [
    "AssetSalePolicy",
    "ClaimBalance",
    "ClaimType",
    "DebtPeriod",
    "DebtSchedule",
    "DistressRecovery",
    "EquityRaisePolicy",
    "FinancingAction",
    "FinancingActionType",
    "FinancingPathError",
    "FinancingPathResult",
    "FinancingPathSpec",
    "FinancingPeriodInput",
    "FinancingPeriodResult",
    "LeasePeriod",
    "LeaseReconciliation",
    "LeaseSchedule",
    "RecoveryWaterfallPolicy",
    "RefinancingFacility",
    "WaterfallAllocation",
    "apply_recovery_waterfall",
    "evaluate_financing_path",
    "reconcile_lease_schedule",
]
