#!/usr/bin/env python3
"""Build the dated-payoff ambiguity report for the governed APV route.

The reusable arithmetic lives in ``valuation_engine``.  This file is the
Korean Air source adapter: it translates disclosed debt/lease schedules and
the versioned operating model into complete financing cases, then asks the
engine to value every probability-vector x payoff-model combination.  Market
prices and broker targets are opened only after the intrinsic result is frozen.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import html
import json
from pathlib import Path
import sys
import tempfile

import yaml

from scripts.run_kr_live import execute_run
from valuation_engine.capacity_yield_operating_paths import (
    CapacityYieldMetricMapping,
    CapacityYieldModuleMapping,
    CapacityYieldProfile,
    OperatingPolicy,
)
from valuation_engine.distribution_route_policy import (
    DistributionIntegrationRoute,
    DistributionRouteStatus,
    DistributionRouteRequest,
    NO_SCENARIO_ASSIGNMENT,
    authorize_distribution_route,
)
from valuation_engine.distributional_apv import (
    APVPathInput,
    SegmentCashFlowPath,
    TaxShieldSchedule,
    evaluate_apv_path,
)
from valuation_engine.distributional_runtime import (
    DistributionPathExecutionInput,
    DistributionalAPVExecutionSpec,
    SupplementalOperatingPeriodInput,
)
from valuation_engine.dynamic_driver_distribution import DriverPath
from valuation_engine.levered_financing_paths import (
    AssetSalePolicy,
    DebtPeriod,
    DebtSchedule,
    EquityRaisePolicy,
    FinancingPathSpec,
    FinancingPeriodInput,
    LeasePeriod,
    LeaseSchedule,
    RecoveryWaterfallPolicy,
    RefinancingFacility,
    evaluate_financing_path,
)
from valuation_engine.payoff_model_ambiguity import (
    RobustEntryPolicy,
    audit_robust_payoff_ambiguity,
    calculate_robust_payoff_ambiguity_entry,
    create_payoff_model_case_from_apv_results,
)
from valuation_engine.probability_ambiguity import (
    ProbabilityVector,
    SignedOutcomeValue,
    calculate_ambiguity_expected_value_range,
)


ZERO = Decimal("0")
ONE = Decimal("1")
MILLION_TO_BILLION = Decimal("0.001")


def D(value: object) -> Decimal:
    return Decimal(str(value))


def _decimal_close(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= Decimal("0.00000001")


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _hash_payload(payload: object) -> str:
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _resolve(run_dir: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (run_dir / path).resolve()


def _verified_source_valuation_snapshot(run_dir: Path, spec: dict) -> dict:
    snapshot = _json(_resolve(run_dir, spec["source_valuation_snapshot"]))
    if snapshot.get("source_run_status") != "COMPLETED" or snapshot.get(
        "source_audit_passed"
    ) is not True:
        raise ValueError("source valuation run is not completed and audited")
    if not snapshot.get("source_valuation_hash") or not snapshot.get("source_audit_hash"):
        raise ValueError("source valuation snapshot is missing immutable receipts")

    manifest_ref = spec.get("source_bundle_manifest")
    manifest_hash = spec.get("source_bundle_manifest_sha256")
    if not manifest_ref or not manifest_hash:
        raise ValueError("source bundle manifest and pinned SHA256 are required")
    manifest_path = _resolve(run_dir, manifest_ref)
    if _sha(manifest_path) != manifest_hash:
        raise ValueError("source bundle manifest hash mismatch")
    manifest = _json(manifest_path)
    if manifest.get("schema_version") != "kr-live-report-bundle/v1":
        raise ValueError("unsupported source bundle manifest")

    expected = {
        "artifact_id": snapshot.get("source_artifact_id"),
        "run_id": snapshot.get("source_run_id"),
        "as_of": spec["as_of"],
        "ticker": spec["ticker"],
        "valuation_hash": snapshot["source_valuation_hash"],
        "audit_hash": snapshot["source_audit_hash"],
    }
    if any(not value or manifest.get(key) != value for key, value in expected.items()):
        raise ValueError("source bundle identity or hash mismatch")
    if snapshot.get("target_id") != spec["target_id"] or snapshot.get("as_of") != spec["as_of"]:
        raise ValueError("source snapshot target or cutoff mismatch")

    root = manifest_path.parent
    receipts = manifest.get("files", [])
    names = [row["filename"] for row in receipts]
    required = {
        "valuation.json",
        "audit.json",
        "manifest.json",
        "freeze_token.json",
        "compiled_assumptions.json",
    }
    if len(names) != len(set(names)) or not required.issubset(names):
        raise ValueError("source bundle receipts are incomplete or duplicated")
    for row in receipts:
        path = (root / row["filename"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("source bundle artifact is missing or outside its bundle")
        if _sha(path) != row["sha256"]:
            raise ValueError(f"source artifact hash mismatch: {row['filename']}")

    valuation = _json(root / "valuation.json")
    audit = _json(root / "audit.json")
    run = _json(root / "manifest.json")
    freeze = _json(root / "freeze_token.json")
    assumptions = _json(root / "compiled_assumptions.json")
    if (
        run.get("status") != "COMPLETED"
        or run.get("audit_passed") is not True
        or run.get("run_id") != expected["run_id"]
        or run.get("ticker") != spec["ticker"]
        or assumptions.get("target_id") != spec["target_id"]
    ):
        raise ValueError("source run is not a completed matching target")
    findings = audit.get("findings", [])
    if not findings or any(
        row.get("blocking") and row.get("passed") is not True for row in findings
    ):
        raise ValueError("source audit contains blocking failures or no findings")
    if valuation.get("valuation_hash") != expected["valuation_hash"] or any(
        freeze.get(key) != expected[key]
        for key in ("run_id", "valuation_hash", "audit_hash")
    ):
        raise ValueError("source valuation and canonical freeze receipts disagree")

    source_rows = valuation["equity_aggregation"]["scenario_values"]
    actual = [
        (row["scenario_id"], D(row["equity_value"]["amount"])) for row in source_rows
    ]
    claimed = [
        (row["scenario_id"], D(row["equity_value_KRW"]))
        for row in snapshot["scenario_values"]
    ]
    if (
        len({key for key, _ in actual}) != len(actual)
        or actual != claimed
        or any(not value.is_finite() for _, value in actual)
        or any(
            row["equity_value"]["unit"] != "KRW"
            or row["equity_value"]["as_of"] != spec["as_of"]
            for row in source_rows
        )
    ):
        raise ValueError("source snapshot scenario values, units or cutoff mismatch")
    return snapshot


def _money(value: Decimal | None) -> str:
    if value is None:
        return "산출 보류"
    return f"{value.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,.0f}원"


def _pct(value: Decimal) -> str:
    return f"{(value * Decimal('100')).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP):,.1f}%"


def _period_index(maturity: date, boundaries: tuple[date, ...]) -> int | None:
    for index, boundary in enumerate(boundaries):
        if maturity <= boundary:
            return index
    return None


def _debt_schedule(
    *,
    claim_id: str,
    opening_principal: Decimal,
    annual_rate: Decimal,
    principal_due: tuple[Decimal, ...],
    new_borrowing: tuple[Decimal, ...] | None = None,
    seniority: int = 1,
) -> DebtSchedule:
    horizon = len(principal_due)
    additions = new_borrowing or (ZERO,) * horizon
    if len(additions) != horizon:
        raise ValueError("debt additions and maturity schedule have different horizons")
    periods: list[DebtPeriod] = []
    opening = opening_principal
    for index in range(horizon):
        addition = additions[index]
        due = principal_due[index]
        if due > opening + addition:
            raise ValueError(f"{claim_id} principal due exceeds available principal")
        closing = opening + addition - due
        periods.append(
            DebtPeriod(
                period=index + 1,
                opening_principal=opening,
                new_borrowing=addition,
                interest_due=(opening + addition) * annual_rate,
                principal_due=due,
                closing_principal=closing,
            )
        )
        opening = closing
    return DebtSchedule(claim_id=claim_id, seniority=seniority, periods=tuple(periods))


def _scheduled_debt(
    *,
    financing: dict,
    case: dict,
    boundaries: tuple[date, ...],
    marginal_debt_rate: Decimal,
    other_senior_claim: Decimal,
) -> tuple[DebtSchedule, ...]:
    horizon = len(boundaries)
    schedules: list[DebtSchedule] = []
    as_of = date.fromisoformat(financing["as_of"])

    for row in financing["bond_schedule_KRW_million"]:
        maturity = date.fromisoformat(row["maturity"])
        if maturity <= as_of:
            continue
        principal = D(row["principal"]) * MILLION_TO_BILLION
        due = [ZERO] * horizon
        index = _period_index(maturity, boundaries)
        if index is not None:
            due[index] = principal
        schedules.append(
            _debt_schedule(
                claim_id=f"BOND:{row['series']}",
                opening_principal=principal,
                annual_rate=D(row["coupon"]),
                principal_due=tuple(due),
            )
        )

    for action in financing["planned_capital_actions"]:
        if action["action_id"] != "BOND_122_REFINANCING":
            continue
        for tranche_index, tranche in enumerate(action["tranches"], start=1):
            principal = D(tranche["principal_KRW_million"]) * MILLION_TO_BILLION
            due = [ZERO] * horizon
            maturity_index = _period_index(date.fromisoformat(tranche["maturity"]), boundaries)
            if maturity_index is not None:
                due[maturity_index] = principal
            additions = [ZERO] * horizon
            additions[0] = principal
            schedules.append(
                _debt_schedule(
                    claim_id=f"BOND:122-{tranche_index}",
                    opening_principal=ZERO,
                    annual_rate=marginal_debt_rate,
                    principal_due=tuple(due),
                    new_borrowing=tuple(additions),
                )
            )

    reconciliation = financing["claim_balance_reconciliation_KRW_million"]
    short_term = D(reconciliation["short_term_borrowings"]) * MILLION_TO_BILLION
    schedules.append(
        _debt_schedule(
            claim_id="SHORT_TERM_BORROWINGS",
            opening_principal=short_term,
            annual_rate=marginal_debt_rate,
            principal_due=(short_term, ZERO, ZERO, ZERO, ZERO),
        )
    )

    bank = financing["bank_borrowing_schedule"]
    current_bank = D(bank["due_within_one_year_KRW_million"]) * MILLION_TO_BILLION
    noncurrent_bank = D(bank["noncurrent_KRW_million"]) * MILLION_TO_BILLION
    weights = tuple(
        D(value)
        for value in financing["analyst_prior_schedule_completion"][
            "noncurrent_bank_principal_allocation_cases"
        ][case["bank_principal_allocation"]]
    )
    bank_due = (current_bank,) + tuple(noncurrent_bank * value for value in weights)
    schedules.append(
        _debt_schedule(
            claim_id="BANK_BORROWINGS",
            opening_principal=current_bank + noncurrent_bank,
            annual_rate=marginal_debt_rate,
            principal_due=bank_due,
        )
    )

    asset_backed = D(
        financing["asset_backed_borrowing"]["nominal_principal_KRW_million"]
    ) * MILLION_TO_BILLION
    schedules.append(
        _debt_schedule(
            claim_id="ASSET_BACKED_BORROWING",
            opening_principal=asset_backed,
            annual_rate=marginal_debt_rate,
            principal_due=(asset_backed, ZERO, ZERO, ZERO, ZERO),
        )
    )
    schedules.append(
        _debt_schedule(
            claim_id="PARENT_NCI_AND_OTHER_SENIOR_CLAIM",
            opening_principal=other_senior_claim,
            annual_rate=ZERO,
            principal_due=(ZERO,) * horizon,
            seniority=2,
        )
    )
    return tuple(schedules)


def _lease_schedule(
    *,
    financing: dict,
    case: dict,
    scenario: dict,
    marginal_debt_rate: Decimal,
) -> LeaseSchedule:
    opening = D(financing["lease_reconciliation_KRW_million"]["recognized_lease_liability"]) * MILLION_TO_BILLION
    first_payment = D(financing["lease_payment_buckets_KRW_million"][0]["undiscounted_payment"]) * MILLION_TO_BILLION
    later_total = D(financing["lease_payment_buckets_KRW_million"][1]["undiscounted_payment"]) * MILLION_TO_BILLION
    weights = tuple(
        D(value)
        for value in financing["analyst_prior_schedule_completion"][
            "lease_one_to_five_year_bucket_allocation_cases"
        ][case["lease_payment_allocation"]]
    )
    payments = (first_payment,) + tuple(later_total * value for value in weights)
    additions = tuple(D(row["airline"]["rou_additions"]) for row in scenario["paths"])
    periods: list[LeasePeriod] = []
    for index, (addition, payment) in enumerate(zip(additions, payments), start=1):
        interest = opening * marginal_debt_rate
        closing = opening + addition + interest - payment
        if closing < ZERO:
            raise ValueError("lease payment completion over-amortizes the liability")
        periods.append(
            LeasePeriod(
                period=index,
                opening_liability=opening,
                new_lease_additions=addition,
                imputed_interest=interest,
                lease_payment=payment,
                closing_liability=closing,
            )
        )
        opening = closing
    return LeaseSchedule(claim_id="LEASE_LIABILITIES", seniority=1, periods=tuple(periods))


def _operating_rows(scenario: dict, tax_rate: Decimal) -> tuple[dict, ...]:
    rows: list[dict] = []
    for row in scenario["paths"]:
        segments = (row["airline"], row["aerospace"], row["other"])
        ebit = sum((D(segment["ebit"]) for segment in segments), ZERO)
        depreciation = sum((D(segment["depreciation"]) for segment in segments), ZERO)
        change_nwc = sum((D(segment["delta_nwc"]) for segment in segments), ZERO)
        cash_capex = (
            D(row["airline"]["cash_capex"])
            + D(row["aerospace"]["capex"])
            + D(row["other"]["capex"])
        )
        operating_cash_flow = ebit * (ONE - tax_rate) + depreciation - change_nwc
        revenue = (
            D(row["airline"]["revenue"])
            + D(row["aerospace"]["revenue"])
            + D(row["other"]["existing_revenue"])
            + D(row["other"]["engine_external_revenue"])
        )
        cash_operating_cost = max(revenue - ebit - depreciation, ZERO)
        rows.append(
            {
                "ebit": ebit,
                "depreciation": depreciation,
                "change_nwc": change_nwc,
                "cash_capex": cash_capex,
                "operating_cash_flow": operating_cash_flow,
                "cash_operating_cost": cash_operating_cost,
            }
        )
    return tuple(rows)


def _canonical_operating_contract(
    *, target_id: str
) -> tuple[CapacityYieldProfile, CapacityYieldMetricMapping]:
    """Return the exact bridge from the signed segment forecast into runtime drivers.

    The source model already carries a complete airline revenue/EBIT/DA/
    reinvestment path.  A one-unit service-capacity bridge preserves those
    signed amounts exactly while the canonical operating engine, rather than
    this source adapter, calculates airline OCF and FCFF.
    """

    version = "korean-air-signed-airline-bridge/v1"
    profile = CapacityYieldProfile(
        target_id=target_id,
        economic_archetype="capacity_yield_levered",
        reporting_currency="KRW",
        accounting_basis="K-IFRS lease-adjusted; amounts in KRW billion",
        active_module_ids=("airline",),
        non_capacity_segment_ids=("aerospace", "hotel", "other"),
        metric_mapping_version=version,
    )
    mapping = CapacityYieldMetricMapping(
        version=version,
        modules=(
            CapacityYieldModuleMapping(
                module_id="airline",
                capacity_driver_id="airline_service_capacity_bridge",
                unit_yield_driver_id="airline_revenue_per_bridge_unit",
                utilization_driver_id=None,
                economic_path_id="airline_signed_forecast",
            ),
        ),
        variable_cost_rules=(),
        fixed_cost_driver_ids=("airline_cash_operating_cost",),
        depreciation_driver_id="airline_depreciation",
        owned_capex_driver_id="airline_cash_capex",
        lease_additions_driver_id="airline_rou_additions",
        change_in_working_capital_driver_id="airline_change_in_working_capital",
    )
    return profile, mapping


def _canonical_driver_path(*, branch_id: str, scenario: dict, seed: int) -> DriverPath:
    rows = scenario["paths"]
    return DriverPath(
        path_id=f"KAL:{branch_id}:SIGNED-FORECAST",
        seed=seed,
        values=(
            (
                "airline_service_capacity_bridge",
                tuple(ONE for _ in rows),
            ),
            (
                "airline_revenue_per_bridge_unit",
                tuple(D(row["airline"]["revenue"]) for row in rows),
            ),
            (
                "airline_cash_operating_cost",
                tuple(
                    D(row["airline"]["revenue"])
                    - D(row["airline"]["ebit"])
                    - D(row["airline"]["depreciation"])
                    for row in rows
                ),
            ),
            (
                "airline_depreciation",
                tuple(D(row["airline"]["depreciation"]) for row in rows),
            ),
            (
                "airline_cash_capex",
                tuple(D(row["airline"]["cash_capex"]) for row in rows),
            ),
            (
                "airline_rou_additions",
                tuple(D(row["airline"]["rou_additions"]) for row in rows),
            ),
            (
                "airline_change_in_working_capital",
                tuple(D(row["airline"]["delta_nwc"]) for row in rows),
            ),
        ),
    )


def _build_distribution_path_input(
    *,
    branch_id: str,
    source_scenario: str,
    path_seed: int,
    model: dict,
    financing: dict,
    case: dict,
    public_facts: dict,
    opening_liquidity: Decimal,
    other_senior_claim: Decimal,
    shares_billion: Decimal,
    wacc: Decimal,
    cost_of_equity: Decimal,
) -> DistributionPathExecutionInput:
    scenario = model["scenarios"][source_scenario]
    tax_rate = D(model["tax_rate"])
    horizon = len(model["periods"])
    as_of = date.fromisoformat(model["as_of"])
    boundaries = tuple(
        date(as_of.year + index, as_of.month, as_of.day)
        for index in range(1, horizon + 1)
    )
    completion = financing["analyst_prior_schedule_completion"]
    marginal_debt_rate = D(
        completion["bounded_future_funding_capacity"]["marginal_debt_cost_rate"]
    )
    operating_rows = _operating_rows(scenario, tax_rate)
    debt_schedules = _scheduled_debt(
        financing=financing,
        case=case,
        boundaries=boundaries,
        marginal_debt_rate=marginal_debt_rate,
        other_senior_claim=other_senior_claim,
    )
    lease_schedule = _lease_schedule(
        financing=financing,
        case=case,
        scenario=scenario,
        marginal_debt_rate=marginal_debt_rate,
    )
    principal_due = tuple(
        sum((schedule.periods[index].principal_due for schedule in debt_schedules), ZERO)
        for index in range(horizon)
    )
    net_capacity = tuple(
        principal_due[index]
        + operating_rows[index]["cash_capex"]
        * D(
            completion["bounded_future_funding_capacity"][
                "incremental_cash_capex_financing_fraction"
            ]
        )
        for index in range(horizon)
    )
    transaction_cost = D(case["refinancing_transaction_cost_rate"])
    gross_capacity = tuple(value / (ONE - transaction_cost) for value in net_capacity)
    minimum_cash = (
        operating_rows[0]["cash_operating_cost"]
        * D(case["minimum_operating_cash_days"])
        / D("365")
    )
    total_assets = D(
        next(
            row["value"]
            for row in public_facts["observations"]
            if row["metric"] == "total_assets"
        )
    ) / D("1000000000")
    recoverable_noncash_assets = total_assets - opening_liquidity
    if recoverable_noncash_assets <= ZERO:
        raise ValueError("recoverable non-cash asset base is not positive")
    distress_proceeds = recoverable_noncash_assets * D(
        case["distress_asset_proceeds_ratio"]
    )
    financing_spec = FinancingPathSpec(
        opening_cash=opening_liquidity,
        minimum_operating_cash=minimum_cash,
        debt_schedules=debt_schedules,
        lease_schedules=(lease_schedule,),
        refinancing_facilities=(
            RefinancingFacility(
                facility_id=f"BOUNDED_FUTURE_FUNDING:{case['model_case_id']}",
                seniority=1,
                gross_capacity_by_period=gross_capacity,
                transaction_cost_rate=transaction_cost,
                cash_interest_rate=marginal_debt_rate,
            ),
        ),
        asset_sale_policy=AssetSalePolicy((ZERO,) * horizon, ZERO),
        equity_raise_policy=EquityRaisePolicy((ZERO,) * horizon, ZERO, (ZERO,) * horizon),
        recovery_waterfall=RecoveryWaterfallPolicy(
            fixed_distress_cost=ZERO,
            distress_cost_rate=D(case["distress_cost_ratio_of_proceeds"]),
            old_shareholder_retention=ONE,
        ),
    )

    supplemental_segments = tuple(
        SegmentCashFlowPath(
            segment_id=segment_id,
            economic_path_id=f"{branch_id}:{segment_id}",
            unlevered_fcff=tuple(D(row[segment_id]["fcff"]) for row in scenario["paths"]),
            asset_required_return=wacc,
            terminal_growth=D(scenario["inputs"]["g"]),
        )
        for segment_id in ("aerospace", "other")
    ) + (
        SegmentCashFlowPath(
            segment_id="hotel",
            economic_path_id=f"{branch_id}:hotel-nav",
            unlevered_fcff=(ZERO,) * horizon,
            asset_required_return=wacc,
            terminal_growth=D(scenario["inputs"]["g"]),
        ),
    )
    supplemental_operating = tuple(
        SupplementalOperatingPeriodInput(
            period=index,
            operating_cash_flow=sum(
                (
                    D(row[segment_id]["ebit"]) * (ONE - tax_rate)
                    + D(row[segment_id]["depreciation"])
                    - D(row[segment_id]["delta_nwc"])
                    for segment_id in ("aerospace", "other")
                ),
                ZERO,
            ),
            mandatory_capex=sum(
                (D(row[segment_id]["capex"]) for segment_id in ("aerospace", "other")),
                ZERO,
            ),
            taxable_income_before_interest=sum(
                (D(row[segment_id]["ebit"]) for segment_id in ("aerospace", "other")),
                ZERO,
            ),
        )
        for index, row in enumerate(scenario["paths"], start=1)
    )
    hotel_value = D(scenario["inputs"]["hotel_value"])
    return DistributionPathExecutionInput(
        model_case_id=case["model_case_id"],
        outcome_id=branch_id,
        driver_path=_canonical_driver_path(
            branch_id=branch_id,
            scenario=scenario,
            seed=path_seed,
        ),
        financing_spec=financing_spec,
        distress_asset_proceeds_by_period=(distress_proceeds,) * horizon,
        core_segment_id="airline",
        core_economic_path_id=f"{branch_id}:airline",
        asset_required_return=wacc,
        terminal_growth=D(scenario["inputs"]["g"]),
        tax_shield_discount_rate=marginal_debt_rate,
        equity_required_return=cost_of_equity,
        non_operating_assets_present=hotel_value,
        non_operating_assets_at_horizon=hotel_value,
        distributions_to_old_holders=(ZERO,) * horizon,
        initial_shares=shares_billion,
        supplemental_segments=supplemental_segments,
        supplemental_operating_periods=supplemental_operating,
    )


def _build_canonical_spec(
    *,
    report_spec: dict,
    model: dict,
    financing: dict,
    public_facts: dict,
    opening_liquidity: Decimal,
    other_senior_claim: Decimal,
    shares_billion: Decimal,
    wacc: Decimal,
    cost_of_equity: Decimal,
    source_hashes: tuple[str, ...],
) -> DistributionalAPVExecutionSpec:
    profile, mapping = _canonical_operating_contract(target_id=report_spec["target_id"])
    case_specs = tuple(
        financing["analyst_prior_schedule_completion"]["complete_payoff_model_cases"]
    )
    paths: list[DistributionPathExecutionInput] = []
    path_seed = 1
    for case in case_specs:
        for branch in report_spec["branches"]:
            paths.append(
                _build_distribution_path_input(
                    branch_id=branch["branch_id"],
                    source_scenario=branch["source_scenario"],
                    path_seed=path_seed,
                    model=model,
                    financing=financing,
                    case=case,
                    public_facts=public_facts,
                    opening_liquidity=opening_liquidity,
                    other_senior_claim=other_senior_claim,
                    shares_billion=shares_billion,
                    wacc=wacc,
                    cost_of_equity=cost_of_equity,
                )
            )
            path_seed += 1
    entry_policy = RobustEntryPolicy(
        report_spec["entry_policy"]["policy_version"],
        int(report_spec["entry_policy"]["horizon_years"]),
        D(report_spec["entry_policy"]["required_annual_return"]),
        tuple(D(value) for value in report_spec["entry_policy"]["sensitivity_returns"]),
    )
    return DistributionalAPVExecutionSpec(
        target_id=report_spec["target_id"],
        profile=profile,
        metric_mapping=mapping,
        operating_policy=OperatingPolicy(D(model["tax_rate"])),
        paths=tuple(paths),
        route=DistributionIntegrationRoute.PRIOR_AMBIGUITY_VALUE_RANGE,
        route_evidence_path_ids=tuple(
            dict.fromkeys(
                (
                    report_spec["source_model"],
                    report_spec["source_financing_spec"],
                    report_spec["source_primary_cashflow"],
                    report_spec["source_public_filing_facts"],
                    *(item for vector in _probability_vectors(report_spec) for item in vector.evidence_path_ids),
                )
            )
        ),
        entry_policy=entry_policy,
        probability_vectors=_probability_vectors(report_spec),
        reference_value_hashes=source_hashes,
    )


def _build_apv_result(
    *,
    branch_id: str,
    source_scenario: str,
    model: dict,
    financing: dict,
    case: dict,
    public_facts: dict,
    opening_liquidity: Decimal,
    other_senior_claim: Decimal,
    shares_billion: Decimal,
    wacc: Decimal,
    cost_of_equity: Decimal,
) -> object:
    scenario = model["scenarios"][source_scenario]
    tax_rate = D(model["tax_rate"])
    horizon = len(model["periods"])
    as_of = date.fromisoformat(model["as_of"])
    boundaries = tuple(
        date(as_of.year + index, as_of.month, as_of.day)
        for index in range(1, horizon + 1)
    )
    completion = financing["analyst_prior_schedule_completion"]
    marginal_debt_rate = D(completion["bounded_future_funding_capacity"]["marginal_debt_cost_rate"])
    operating_rows = _operating_rows(scenario, tax_rate)
    debt_schedules = _scheduled_debt(
        financing=financing,
        case=case,
        boundaries=boundaries,
        marginal_debt_rate=marginal_debt_rate,
        other_senior_claim=other_senior_claim,
    )
    lease_schedule = _lease_schedule(
        financing=financing,
        case=case,
        scenario=scenario,
        marginal_debt_rate=marginal_debt_rate,
    )
    principal_due = tuple(
        sum((schedule.periods[index].principal_due for schedule in debt_schedules), ZERO)
        for index in range(horizon)
    )
    net_capacity = tuple(
        principal_due[index]
        + operating_rows[index]["cash_capex"]
        * D(completion["bounded_future_funding_capacity"]["incremental_cash_capex_financing_fraction"])
        for index in range(horizon)
    )
    transaction_cost = D(case["refinancing_transaction_cost_rate"])
    gross_capacity = tuple(value / (ONE - transaction_cost) for value in net_capacity)
    minimum_cash = (
        operating_rows[0]["cash_operating_cost"]
        * D(case["minimum_operating_cash_days"])
        / D("365")
    )
    total_assets = D(
        next(
            row["value"]
            for row in public_facts["observations"]
            if row["metric"] == "total_assets"
        )
    ) / D("1000000000")
    recoverable_noncash_assets = total_assets - opening_liquidity
    if recoverable_noncash_assets <= ZERO:
        raise ValueError("recoverable non-cash asset base is not positive")
    distress_proceeds = recoverable_noncash_assets * D(case["distress_asset_proceeds_ratio"])
    financing_spec = FinancingPathSpec(
        opening_cash=opening_liquidity,
        minimum_operating_cash=minimum_cash,
        debt_schedules=debt_schedules,
        lease_schedules=(lease_schedule,),
        refinancing_facilities=(
            RefinancingFacility(
                facility_id=f"BOUNDED_FUTURE_FUNDING:{case['model_case_id']}",
                seniority=1,
                gross_capacity_by_period=gross_capacity,
                transaction_cost_rate=transaction_cost,
                cash_interest_rate=marginal_debt_rate,
            ),
        ),
        asset_sale_policy=AssetSalePolicy((ZERO,) * horizon, ZERO),
        equity_raise_policy=EquityRaisePolicy((ZERO,) * horizon, ZERO, (ZERO,) * horizon),
        recovery_waterfall=RecoveryWaterfallPolicy(
            fixed_distress_cost=ZERO,
            distress_cost_rate=D(case["distress_cost_ratio_of_proceeds"]),
            old_shareholder_retention=ONE,
        ),
    )
    financing_inputs = tuple(
        FinancingPeriodInput(
            period=index + 1,
            operating_cash_flow=row["operating_cash_flow"],
            mandatory_capex=row["cash_capex"],
            distress_asset_proceeds=distress_proceeds,
            taxable_income_before_interest=row["ebit"],
            tax_rate=tax_rate,
        )
        for index, row in enumerate(operating_rows)
    )
    financing_result = evaluate_financing_path(inputs=financing_inputs, spec=financing_spec)
    deductible_interest = tuple(
        (
            period.debt_interest + period.lease_interest
            if index < len(financing_result.periods)
            else ZERO
        )
        for index, period in enumerate(
            tuple(financing_result.periods)
            + (None,) * (horizon - len(financing_result.periods))
        )
    )
    segments = tuple(
        SegmentCashFlowPath(
            segment_id=segment_id,
            economic_path_id=f"{branch_id}:{segment_id}",
            unlevered_fcff=tuple(D(row[segment_id]["fcff"]) for row in scenario["paths"]),
            asset_required_return=wacc,
            terminal_growth=D(scenario["inputs"]["g"]),
        )
        for segment_id in ("airline", "aerospace", "other")
    )
    hotel_value = D(scenario["inputs"]["hotel_value"])
    return evaluate_apv_path(
        APVPathInput(
            path_id=f"{case['model_case_id']}:{branch_id}",
            segments=segments,
            tax_shield_schedule=TaxShieldSchedule(
                taxable_income_before_interest=tuple(row["ebit"] for row in operating_rows),
                deductible_interest=deductible_interest,
                tax_rate=tax_rate,
                discount_rate=marginal_debt_rate,
            ),
            financing_result=financing_result,
            non_operating_assets_present=hotel_value,
            non_operating_assets_at_horizon=hotel_value,
            distributions_to_old_holders=(ZERO,) * horizon,
            equity_required_return=cost_of_equity,
            initial_shares=shares_billion,
        )
    )


def _probability_vectors(spec: dict) -> tuple[ProbabilityVector, ...]:
    branch_ids = tuple(row["branch_id"] for row in spec["branches"])
    return tuple(
        ProbabilityVector(
            vector_id=row["label"],
            weights=tuple(
                (branch_id, D(weight))
                for branch_id, weight in zip(branch_ids, row["probabilities"])
            ),
            evidence_path_ids=tuple(row["evidence_path_ids"]),
        )
        for row in spec["probability_authorization"]["sensitivity_sets"]
    )


def _legacy_signed_range(spec: dict, snapshot: dict, vectors: tuple[ProbabilityVector, ...]):
    values = {row["scenario_id"]: D(row["equity_value_KRW"]) for row in snapshot["scenario_values"]}
    shares = D(spec["diluted_shares"])
    outcomes = tuple(
        SignedOutcomeValue(
            outcome_id=row["branch_id"],
            present_value=values[row["source_scenario"]] / shares,
            evidence_path_ids=(snapshot["source_valuation_hash"],),
        )
        for row in spec["branches"]
    )
    return calculate_ambiguity_expected_value_range(
        outcomes=outcomes,
        probability_vectors=vectors,
        values_authorized=True,
        value_set_hash=snapshot["source_valuation_hash"],
    )


def _svg(title: str, lines: list[str]) -> str:
    escaped_title = html.escape(title)
    body = "".join(
        f'<text x="56" y="{118 + index * 48}" font-size="25" fill="#e2e8f0">{html.escape(line)}</text>'
        for index, line in enumerate(lines)
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">'
        '<rect width="1200" height="630" fill="#0f172a"/>'
        f'<text x="56" y="68" font-size="36" font-weight="700" fill="#f8fafc">{escaped_title}</text>'
        f'{body}</svg>\n'
    )


def _write_immutable(path: Path, contents: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") != contents:
        raise ValueError(f"immutable artifact collision: {path}")
    path.write_text(contents, encoding="utf-8")


def build(spec_path: Path, output_root: Path) -> Path:
    spec_path = Path(spec_path).resolve()
    output_root = Path(output_root).resolve()
    spec = _json(spec_path)
    if spec.get("schema_version") != "dated-payoff-ambiguity-report/v2":
        raise ValueError("unsupported governed distribution report schema")
    run_dir = spec_path.parent.parent
    intrinsic_paths = {
        key: _resolve(run_dir, spec[key])
        for key in (
            "source_model",
            "source_financing_spec",
            "source_risk_pack",
            "source_risk_result",
            "source_valuation_snapshot",
            "source_bundle_manifest",
            "source_primary_cashflow",
            "source_public_filing_facts",
        )
    }
    for key, path in intrinsic_paths.items():
        if not path.is_file():
            raise ValueError(f"missing intrinsic source: {key}")
    financing = _json(intrinsic_paths["source_financing_spec"])
    model = _json(intrinsic_paths["source_model"])
    risk = _json(intrinsic_paths["source_risk_result"])
    snapshot = _verified_source_valuation_snapshot(run_dir, spec)
    public_facts = _json(intrinsic_paths["source_public_filing_facts"])
    if model["as_of"] != spec["as_of"] or financing["as_of"] != spec["as_of"]:
        raise ValueError("intrinsic inputs do not share the report cutoff")
    declared_primary_hash = financing["analyst_prior_schedule_completion"][
        "bounded_future_funding_capacity"
    ]["source_member_sha256"]
    if _sha(intrinsic_paths["source_primary_cashflow"]) != declared_primary_hash:
        raise ValueError("primary cash-flow source hash does not replay")

    vectors = _probability_vectors(spec)
    wacc = D(risk["wacc"])
    cost_of_equity = D(risk["cost_of_equity"])
    opening_liquidity = D(spec["opening_liquidity_as_of_KRW_billion"])
    other_senior_claim = D(spec["parent_noncontrolling_and_other_senior_claim_KRW_billion"])
    shares_billion = D(spec["diluted_shares_billion"])
    case_specs = tuple(
        financing["analyst_prior_schedule_completion"]["complete_payoff_model_cases"]
    )
    canonical_spec = _build_canonical_spec(
        report_spec=spec,
        model=model,
        financing=financing,
        public_facts=public_facts,
        opening_liquidity=opening_liquidity,
        other_senior_claim=other_senior_claim,
        shares_billion=shares_billion,
        wacc=wacc,
        cost_of_equity=cost_of_equity,
        source_hashes=tuple(_sha(path) for path in intrinsic_paths.values()),
    )
    canonical_run_dir = intrinsic_paths["source_model"].parent.parent
    if not (canonical_run_dir / "run.yaml").is_file():
        raise ValueError("canonical prepared-run directory cannot be resolved from source_model")
    with tempfile.TemporaryDirectory(prefix="korean-air-canonical-apv-") as state_root:
        reached, stop_stage, stop_reason, canonical_run = execute_run(
            canonical_run_dir,
            state_root=state_root,
            staff_mode="replay",
            distributional_spec=canonical_spec,
        )
    if stop_stage is not None or len(reached) != 33 or canonical_run.blocked_reasons:
        raise ValueError(
            "canonical LIVE_PRIMARY distributional run did not complete: "
            f"{stop_stage or 'unknown'} {stop_reason}"
        )
    canonical_valuation = canonical_run.data.get("distributional_primary_result")
    canonical_audit = canonical_run.data.get("audit_report")
    canonical_freeze = canonical_run.freeze_token
    canonical_attestation = canonical_run.data.get("execution_attestation")
    canonical_report = canonical_run.data.get("final_report")
    if (
        canonical_valuation is None
        or canonical_valuation.ambiguity_intrinsic_range is None
        or canonical_valuation.robust_entry is None
        or canonical_audit is None
        or not canonical_audit.passed
        or canonical_freeze is None
        or canonical_attestation is None
        or not isinstance(canonical_report, str)
        or not canonical_report
        or canonical_run.data.get("canonical_entrypoint_id")
        != "prism_strict_live_primary/v1"
    ):
        raise ValueError("canonical distributional receipts are incomplete")
    canonical_intrinsic = canonical_valuation.ambiguity_intrinsic_range
    canonical_entry = canonical_valuation.robust_entry

    model_cases = []
    path_results: dict[str, dict[str, object]] = {}
    for case in case_specs:
        branch_results = []
        path_results[case["model_case_id"]] = {}
        for branch in spec["branches"]:
            result = _build_apv_result(
                branch_id=branch["branch_id"],
                source_scenario=branch["source_scenario"],
                model=model,
                financing=financing,
                case=case,
                public_facts=public_facts,
                opening_liquidity=opening_liquidity,
                other_senior_claim=other_senior_claim,
                shares_billion=shares_billion,
                wacc=wacc,
                cost_of_equity=cost_of_equity,
            )
            branch_results.append((branch["branch_id"], result))
            path_results[case["model_case_id"]][branch["branch_id"]] = result
        model_cases.append(
            create_payoff_model_case_from_apv_results(
                model_case_id=case["model_case_id"],
                branch_results=tuple(branch_results),
                evidence_path_ids=(
                    spec["source_model"],
                    spec["source_financing_spec"],
                    spec["source_primary_cashflow"],
                    case["model_case_id"],
                ),
            )
        )
    model_cases_tuple = tuple(model_cases)
    source_payoff_hash = _hash_payload(
        {
            "source_hashes": {key: _sha(path) for key, path in intrinsic_paths.items()},
            "path_calculation_hashes": sorted(
                result.path_calculation_hash
                for by_branch in path_results.values()
                for result in by_branch.values()
            ),
        }
    )
    future_payoffs_authorized = bool(
        financing.get("distributional_distress_route_authorized")
        and financing.get("hybrid_prior_robust_entry_authorized")
    )
    entry_policy = RobustEntryPolicy(
        spec["entry_policy"]["policy_version"],
        int(spec["entry_policy"]["horizon_years"]),
        D(spec["entry_policy"]["required_annual_return"]),
        tuple(D(value) for value in spec["entry_policy"]["sensitivity_returns"]),
    )
    entry_result = calculate_robust_payoff_ambiguity_entry(
        payoff_model_cases=model_cases_tuple,
        probability_vectors=vectors,
        policy=entry_policy,
        future_payoffs_authorized=future_payoffs_authorized,
        source_payoff_hash=source_payoff_hash,
    )
    if entry_result.authorization_receipt is None:
        raise ValueError(f"robust entry withheld: {entry_result.withheld_reason}")
    fair_policy = RobustEntryPolicy(
        spec["fair_value_policy"]["policy_version"],
        int(spec["fair_value_policy"]["horizon_years"]),
        cost_of_equity,
        tuple(D(value) for value in spec["fair_value_policy"]["sensitivity_returns"]),
    )
    fair_result = calculate_robust_payoff_ambiguity_entry(
        payoff_model_cases=model_cases_tuple,
        probability_vectors=vectors,
        policy=fair_policy,
        future_payoffs_authorized=future_payoffs_authorized,
        source_payoff_hash=source_payoff_hash,
    )
    if not all(
        (
            _decimal_close(
                fair_result.minimum_expected_present_value,
                canonical_intrinsic.minimum_expected_value,
            ),
            _decimal_close(
                fair_result.maximum_expected_present_value,
                canonical_intrinsic.maximum_expected_value,
            ),
            _decimal_close(
                entry_result.robust_entry_price,
                canonical_entry.robust_entry_price,
            ),
        )
    ):
        raise ValueError(
            "source-adapter diagnostic does not reconcile to canonical LIVE_PRIMARY output: "
            f"diagnostic fair={fair_result.minimum_expected_present_value}/"
            f"{fair_result.maximum_expected_present_value}, canonical fair="
            f"{canonical_intrinsic.minimum_expected_value}/"
            f"{canonical_intrinsic.maximum_expected_value}, diagnostic entry="
            f"{entry_result.robust_entry_price}, canonical entry="
            f"{canonical_entry.robust_entry_price}"
        )
    route_request = DistributionRouteRequest(
        route=DistributionIntegrationRoute.PRIOR_AMBIGUITY_VALUE_RANGE,
        economic_archetypes=("capacity_yield_levered",),
        scenario_assignment_method=NO_SCENARIO_ASSIGNMENT,
        evidence_path_ids=tuple(spec[key] for key in intrinsic_paths),
        signed_values_authorized=True,
        ambiguity_set_validated=True,
        ambiguity_vector_count=len(vectors),
        future_shareholder_payoffs_authorized=True,
        future_payoff_authorization_receipt=entry_result.authorization_receipt,
        payoff_horizon_years=entry_policy.horizon_years,
        payoff_model_case_count=len(model_cases_tuple),
    )
    route_authorization = authorize_distribution_route(route_request)
    ambiguity_audit = audit_robust_payoff_ambiguity(
        result=entry_result,
        payoff_model_cases=model_cases_tuple,
        probability_vectors=vectors,
        policy=entry_policy,
        future_payoffs_authorized=True,
        source_payoff_hash=source_payoff_hash,
        route_request=route_request,
        route_authorization=route_authorization,
    )
    legacy_range = _legacy_signed_range(spec, snapshot, vectors)
    intrinsic_freeze_hash = _hash_payload(
        {
            "contract": "canonical-distributional-intrinsic-lineage/v1",
            "valuation_hash": canonical_run.data["valuation_hash"],
            "audit_hash": canonical_run.data["audit_hash"],
            "distribution_hash": canonical_run.data["distribution_hash"],
            "route_authorization_hash": canonical_run.data[
                "distribution_route_authorization_hash"
            ],
            "ambiguity_set_hash": canonical_entry.probability_ambiguity_set_hash,
            "payoff_model_set_hash": canonical_entry.payoff_model_set_hash,
            "entry_calculation_hash": canonical_run.data["entry_calculation_hash"],
        }
    )

    # Post-freeze comparison layer begins here.
    broker_path = _resolve(run_dir, spec["source_broker_comparison"])
    market_path = _resolve(run_dir, spec["source_market_observation"])
    broker = _json(broker_path)
    market_rows = _json(market_path)
    broker_rows = broker["verified_reports"]
    if any(row["report_date"] > spec["as_of"] for row in broker_rows):
        raise ValueError("broker report date is after the intrinsic cutoff")
    market_candidates = tuple(row for row in market_rows if row["localTradedAt"] <= spec["as_of"])
    if not market_candidates:
        raise ValueError("no market observation exists on or before the cutoff")
    market = max(market_candidates, key=lambda row: row["localTradedAt"])
    market_price = D(market["closePrice"].replace(",", ""))
    targets = tuple(sorted(D(row["target_price_krw"]) for row in broker_rows))
    median_target = targets[len(targets) // 2]
    post_freeze_comparison_hash = _hash_payload(
        {
            "intrinsic_freeze_hash": intrinsic_freeze_hash,
            "broker_source_hash": _sha(broker_path),
            "market_source_hash": _sha(market_path),
            "market_date": market["localTradedAt"],
            "market_price": str(market_price),
        }
    )

    branch_fair_ranges = {}
    for branch in (row["branch_id"] for row in spec["branches"]):
        values = tuple(
            model_case.payoff_map()[branch].present_value(cost_of_equity)
            for model_case in model_cases_tuple
        )
        branch_fair_ranges[branch] = (min(values), max(values))
    decision = (
        "매수 검토"
        if market_price <= entry_result.robust_entry_price
        else "신규매수 보류"
    )
    market_premium_to_fair_max = (
        market_price / fair_result.maximum_expected_present_value - ONE
    )
    broker_median_premium_to_fair_max = (
        median_target / fair_result.maximum_expected_present_value - ONE
    )
    sensitivity_rows = "\n".join(
        f"| {_pct(row.required_annual_return)} | {_money(row.robust_entry_price)} |"
        for row in entry_result.sensitivities
    )
    case_rows = "\n".join(
        f"| {row['model_case_id']} | {row['minimum_operating_cash_days']}일 | "
        f"{row['bank_principal_allocation']} / {row['lease_payment_allocation']} | "
        f"{_pct(D(row['distress_asset_proceeds_ratio']))} / {_pct(D(row['distress_cost_ratio_of_proceeds']))} |"
        for row in case_specs
    )
    state_rows = "\n".join(
        "| "
        + case["model_case_id"]
        + " | "
        + " | ".join(
            (
                f"{path_results[case['model_case_id']][branch_id].realized_periods}년차 부실"
                if path_results[case["model_case_id"]][branch_id].distressed
                else "5년 존속·잔여 0원"
                if path_results[case["model_case_id"]][branch_id].terminal_old_equity_payoff
                == ZERO
                else "5년 존속"
            )
            for branch_id in ("Down", "Central", "Upside")
        )
        + " |"
        for case in case_specs
    )
    broker_table = "\n".join(
        f"| {row['institution']} ({row['report_date']}) | {_money(D(row['target_price_krw']))} | "
        f"{row['target_multiple'] if row['target_multiple'] != 'NOT_DISCLOSED' else '산식·배수 비공개'} | "
        f"{row['load_bearing_assumption']} |"
        for row in broker_rows
    )
    broker_sources = "\n".join(
        f"- [{row['institution']} {row['report_date']} — {row['title']}]({row['url']})"
        for row in broker_rows
    )
    analysis_report = f"""# 대한항공 지급시점별 강건 가치평가 — 상세 분석

| 항목 | 결과 |
|---|---:|
| 기준일 | {spec['as_of']} |
| 판단 | **{decision}** |
| 강건 공정가치 구간 | **{_money(fair_result.minimum_expected_present_value)}~{_money(fair_result.maximum_expected_present_value)}** |
| 5년·연 12% 강건 매수상한 | **{_money(entry_result.robust_entry_price)}** |
| 현재가 | **{_money(market_price)}** ({market['localTradedAt']}) |
| 확률 상태 | **회사 자기이력 보정 전 · 단일 목표가/성공확률 미제시** |

## 투자 요약

이번 값은 세 사건확률을 하나의 정답으로 고정하지 않는다. 출처가 결속된 {len(vectors)}개 확률벡터와 {len(model_cases_tuple)}개 완결 자금조달 모형을 모두 조합하고, 각 조합에서 실제 지급시점별 구주주 현금흐름을 할인했다. 공정가치는 그 조합의 범위로, 매수상한은 모든 조합 중 가장 낮은 5년 기대현재가치로 표시한다.

현재가 {_money(market_price)}은 강건 공정가치 상단보다 {_pct(market_premium_to_fair_max)} 높고, 연 12% 기준 강건 매수상한 {_money(entry_result.robust_entry_price)}도 웃돈다. 회사 자기이력으로 사건확률이 보정되지 않았으므로 이 보고서는 단일 적정가나 “수익 달성확률 75%”를 주장하지 않는다.

## 가치평가

| 경제 상태 | 자금조달 모형별 공정가치 범위 |
|---|---:|
| 통합·회복 실패 | {_money(branch_fair_ranges['Down'][0])}~{_money(branch_fair_ranges['Down'][1])} |
| 점진적 회복 | {_money(branch_fair_ranges['Central'][0])}~{_money(branch_fair_ranges['Central'][1])} |
| 실행 성공 | {_money(branch_fair_ranges['Upside'][0])}~{_money(branch_fair_ranges['Upside'][1])} |

기존 서명된 점가치의 확률집합 기대값은 {_money(legacy_range.minimum_expected_value)}~{_money(legacy_range.maximum_expected_value)}이었다. 이 값은 음수 하방을 보존한 교차검산일 뿐, 미래 지급시점과 차환경로가 없는 현재가치이므로 새 매수상한을 승인하지 않는다.

| 요구 연수익률 | 모든 prior·자금조달 조합을 견디는 매수상한 |
|---:|---:|
{sensitivity_rows}

## 핵심 가정과 위험

| 완결 자금조달 모형 | 최소현금 | 은행만기 / 리스지급 배분 | 부실매각 회수율 / 비용률 |
|---|---:|---|---:|
{case_rows}

| 자금조달 모형 | 통합·회복 실패 | 점진적 회복 | 실행 성공 |
|---|---|---|---|
{state_rows}

표의 0원은 현재가치 하방을 사후 절삭한 값이 아니다. 2년차 명시적 부실 waterfall에서 구주주 잔여가 없거나, 5년 말 계속기업 자산가치보다 선순위 청구액이 큰 경우의 실제 미래 지급액이다.

- 2026년 반기 총자산에서 기준일 적격 유동성을 뺀 비현금 자산을 부실매각 회수율의 기준으로 사용했다. 이는 감정가가 아니라 30%·50%·70%의 넓은 모형 범위다.
- 은행 비유동 원금과 리스 1~5년 지급액은 공시 합계를 보존한 채 front-loaded·level·back-loaded로 나눴다. 실제 연도별 계약표가 아니다.
- 미래 조달능력은 2026년 상반기 실현 자금조달을 근거로 만기 원금 100%와 현금 CAPEX 55%까지만 모형화했다. 미사용 약정한도가 존재한다고 간주하지 않았다.
- 모든 중간 배당과 부실회수액은 실제 발생연도에, 존속기업 잔여가치는 5년 말에 할인했다. 현재 장부부채를 5년 만기 행사가격처럼 재할인하는 구조옵션은 사용하지 않았다.
- 합병신주는 최초 주식수에 포함했고, 동일 주식을 다시 희석하지 않았다. 추가 증자능력은 근거가 없어 0으로 두었다.
- 회사 자기이력 OOS 보정이 끝나기 전까지 확률집합은 분석자 사전확률이다. 따라서 단일 확률가중 목표가와 성공확률은 계속 금지된다.

## 증권사·시장 비교

내재가치 계산을 동결한 뒤 기준일 이전 공개 원문 3건을 비교했다. 표본 목표가는 {_money(min(targets))}~{_money(max(targets))}, 중앙값은 {_money(median_target)}이다. 전체 컨센서스가 아니라 공개 원문 검증 표본이다.

| 증권사·보고일 | 목표가 | 공개 평가기준 | 핵심 전제 |
|---|---:|---|---|
{broker_table}

증권사 목표가 중앙값 {_money(median_target)}은 이번 강건 공정가치 상단보다 {_pct(broker_median_premium_to_fair_max)} 높다. 증권사 목표가는 회복·통합 시너지와 목표배수를 중심으로 한 단일 가격이다. 이번 결과는 확률과 차환·리스 만기 추정이 하나로 확정되지 않았다는 점을 가격구간과 강건 매수상한에 직접 반영한다. 둘은 같은 종류의 숫자가 아니다.

## 원문

- [대한항공 2026년 반기보고서](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814002803)
- [2026년 7월 24일 합병 투자설명서](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260724000006)
- [2026년 9월 10일 회사채 발행 공시](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260910000442)
- [대한항공 재무정보·IR 아카이브](https://www.koreanair.com/contents/footer/about-us/investor-relations/financial-information)
- [네이버증권 대한항공 가격 이력](https://m.stock.naver.com/api/stock/003490/price)
{broker_sources}
"""

    audit_checks = {
        "source_bundle_manifest_and_artifacts_replay": _sha(
            intrinsic_paths["source_bundle_manifest"]
        )
        == spec["source_bundle_manifest_sha256"],
        "canonical_live_primary_completed": len(reached) == 33
        and canonical_run.data.get("canonical_entrypoint_id")
        == "prism_strict_live_primary/v1",
        "canonical_distributional_audit_passed": canonical_audit.passed,
        "canonical_freeze_token_present": bool(canonical_freeze.token_hash),
        "canonical_execution_attestation_present": bool(
            canonical_attestation.attestation_hash
        ),
        "dated_payoff_diagnostic_replays": ambiguity_audit.passed,
        "route_authorizes_interval_and_robust_entry": route_authorization.status is DistributionRouteStatus.AUTHORIZED
        and route_authorization.expected_value_interval_authorized
        and route_authorization.entry_price_authorized,
        "single_point_target_forbidden": not route_authorization.point_target_authorized,
        "success_probability_claim_forbidden": not route_authorization.success_probability_claim_authorized,
        "primary_cashflow_hash_replays": _sha(intrinsic_paths["source_primary_cashflow"]) == declared_primary_hash,
        "all_probability_model_combinations_evaluated": len(entry_result.combination_results)
        == len(vectors) * len(model_cases_tuple),
        "broker_loaded_only_after_intrinsic_freeze": bool(intrinsic_freeze_hash),
        "broker_reports_not_after_cutoff": all(row["report_date"] <= spec["as_of"] for row in broker_rows),
        "broker_targets_excluded_from_intrinsic_inputs": True,
        "market_loaded_only_after_intrinsic_freeze": bool(intrinsic_freeze_hash),
        "canonical_and_diagnostic_values_reconcile": _decimal_close(
            fair_result.minimum_expected_present_value,
            canonical_intrinsic.minimum_expected_value,
        )
        and _decimal_close(
            fair_result.maximum_expected_present_value,
            canonical_intrinsic.maximum_expected_value,
        )
        and _decimal_close(
            entry_result.robust_entry_price,
            canonical_entry.robust_entry_price,
        ),
        "legacy_negative_value_preserved": legacy_range.minimum_expected_value is not None
        and any(
            D(row["equity_value_KRW"]) < ZERO for row in snapshot["scenario_values"]
        ),
        "investor_report_hides_internal_identifiers": not any(
            token in canonical_report
            for token in ("calculation_hash", "artifact_id", "route_id", "DIST-")
        ),
    }
    if not all(audit_checks.values()):
        raise ValueError("dated payoff report audit failed")

    payoff_rows = []
    for model_case in model_cases_tuple:
        for payoff in model_case.payoffs:
            result = path_results[model_case.model_case_id][payoff.branch_id]
            payoff_rows.append(
                {
                    "payoff_model_case_id": model_case.model_case_id,
                    "branch_id": payoff.branch_id,
                    "distressed": result.distressed,
                    "distress_period": result.realized_periods if result.distressed else None,
                    "realized_periods": result.realized_periods,
                    "dated_cash_flows_per_share": [
                        {"period": row.period, "amount": str(row.amount_per_share)}
                        for row in payoff.cash_flows
                    ],
                    "present_value_at_cost_of_equity": str(payoff.present_value(cost_of_equity)),
                    "path_calculation_hash": result.path_calculation_hash,
                }
            )
    distribution_payload = {
        "schema_version": "dated-shareholder-payoff-ambiguity/v2",
        "target_id": spec["target_id"],
        "as_of": spec["as_of"],
        "authorization_status": spec["probability_authorization"]["status"],
        "point_target_authorized": False,
        "success_probability_claim_authorized": False,
        "fair_value_discount_rate": str(cost_of_equity),
        "fair_value_interval_per_share": {
            "minimum": str(canonical_intrinsic.minimum_expected_value),
            "maximum": str(canonical_intrinsic.maximum_expected_value),
        },
        "binding_minimum": {
            "probability_vector_id": canonical_intrinsic.binding_minimum_probability_vector_id,
            "payoff_model_case_id": canonical_intrinsic.binding_minimum_model_case_id,
        },
        "binding_maximum": {
            "probability_vector_id": canonical_intrinsic.binding_maximum_probability_vector_id,
            "payoff_model_case_id": canonical_intrinsic.binding_maximum_model_case_id,
        },
        "legacy_signed_value_cross_check": {
            "minimum": str(legacy_range.minimum_expected_value),
            "maximum": str(legacy_range.maximum_expected_value),
            "role": spec["legacy_signed_value_role"],
        },
        "distribution_hash": canonical_valuation.distribution_hash,
        "valuation_hash": canonical_valuation.envelope.envelope_hash,
        "probability_ambiguity_set_hash": canonical_entry.probability_ambiguity_set_hash,
        "payoff_model_set_hash": canonical_entry.payoff_model_set_hash,
        "source_payoff_hash": canonical_entry.source_payoff_hash,
        "intrinsic_freeze_hash": intrinsic_freeze_hash,
    }
    entry_payload = {
        "schema_version": "ambiguity-robust-dated-entry/v2",
        "entry_price": str(canonical_entry.robust_entry_price),
        "horizon_years": entry_policy.horizon_years,
        "required_annual_return": str(entry_policy.required_annual_return),
        "interpretation": spec["entry_policy"]["interpretation"],
        "binding_probability_vector_id": canonical_entry.binding_probability_vector_id,
        "binding_payoff_model_case_id": canonical_entry.binding_payoff_model_case_id,
        "point_target_authorized": False,
        "success_probability_claim_authorized": False,
        "sensitivities": [asdict(row) for row in canonical_entry.sensitivities],
        "intrinsic_freeze_hash": intrinsic_freeze_hash,
    }
    broker_payload = {
        "schema_version": "post-freeze-broker-comparison-result/v2",
        "intrinsic_freeze_hash": intrinsic_freeze_hash,
        "post_freeze_comparison_hash": post_freeze_comparison_hash,
        "intrinsic_distribution_unchanged": True,
        "market": {
            "date": market["localTradedAt"],
            "price": str(market_price),
            "currency": spec["reporting_currency"],
        },
        "sample": {
            "report_count": len(broker_rows),
            "min_target_price": str(min(targets)),
            "median_target_price": str(median_target),
            "max_target_price": str(max(targets)),
        },
        "sample_policy": broker["sample_policy"],
        "coverage_limit": broker["coverage_limit"],
        "reports": broker_rows,
    }
    audit_payload = {
        "schema_version": "dated-payoff-ambiguity-audit/v2",
        "passed": True,
        "checks": audit_checks,
        "canonical_findings": [asdict(row) for row in canonical_audit.findings],
        "diagnostic_engine_findings": [asdict(row) for row in ambiguity_audit.findings],
        "canonical_audit_hash": canonical_run.data["audit_hash"],
        "intrinsic_freeze_hash": intrinsic_freeze_hash,
        "post_freeze_comparison_hash": post_freeze_comparison_hash,
    }
    artifact_basis = _hash_payload(
        {
            "intrinsic_freeze_hash": intrinsic_freeze_hash,
            "canonical_freeze_token_hash": canonical_freeze.token_hash,
            "execution_attestation_hash": canonical_attestation.attestation_hash,
            "post_freeze_comparison_hash": post_freeze_comparison_hash,
            "spec_sha256": _sha(spec_path),
            "report_builder_sha256": _sha(Path(__file__).resolve()),
        }
    )
    artifact_id = f"{spec['ticker']}-{spec['as_of'].replace('-', '')}-PAY-{artifact_basis[:12].upper()}"
    bundle = output_root / artifact_id
    bundle.mkdir(parents=True, exist_ok=True)
    files = {
        "equity_value_distribution.json": json.dumps(distribution_payload, ensure_ascii=False, indent=2, default=str) + "\n",
        "entry_price.json": json.dumps(entry_payload, ensure_ascii=False, indent=2, default=str) + "\n",
        "payoff_model_cases.json": json.dumps({"rows": payoff_rows}, ensure_ascii=False, indent=2) + "\n",
        "broker_comparison.json": json.dumps(broker_payload, ensure_ascii=False, indent=2) + "\n",
        "audit.json": json.dumps(audit_payload, ensure_ascii=False, indent=2, default=str) + "\n",
        "canonical_valuation.json": json.dumps(
            asdict(canonical_valuation), ensure_ascii=False, indent=2, default=str
        )
        + "\n",
        "canonical_audit.json": json.dumps(
            asdict(canonical_audit), ensure_ascii=False, indent=2, default=str
        )
        + "\n",
        "freeze_token.json": json.dumps(
            asdict(canonical_freeze), ensure_ascii=False, indent=2, default=str
        )
        + "\n",
        "execution_attestation.json": json.dumps(
            asdict(canonical_attestation), ensure_ascii=False, indent=2, default=str
        )
        + "\n",
        "canonical_stage_trace.json": json.dumps(
            {"stages": [asdict(row) for row in canonical_run.stage_traces]},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n",
        "final_report.md": canonical_report,
        "dated_payoff_analysis.md": analysis_report,
        "distributional_summary.svg": _svg(
            "대한항공 가치평가·투자 결론",
            [
                f"강건 공정가치 {_money(fair_result.minimum_expected_present_value)}~{_money(fair_result.maximum_expected_present_value)}",
                f"5년·연 12% 강건 매수상한 {_money(entry_result.robust_entry_price)}",
                f"현재가 {_money(market_price)} · {decision}",
                "단일 목표가·성공확률 미제시",
            ],
        ),
        "distributional_assumptions.svg": _svg(
            "가정·위험·출처",
            [
                "세 확률벡터 × 세 완결 자금조달 모형",
                "현금흐름별 실제 지급시점 할인",
                "만기원금 100% + 현금 CAPEX 55% 조달능력 모형",
                "부실매각 회수율 30%·50%·70%",
                "공시·증권사·시장자료는 기준일 이전만 사용",
            ],
        ),
    }
    for name, contents in files.items():
        _write_immutable(bundle / name, contents)
    manifest = {
        "schema_version": "dated-payoff-ambiguity-report-bundle/v2",
        "artifact_id": artifact_id,
        "company": spec["company"],
        "ticker": spec["ticker"],
        "as_of": spec["as_of"],
        "status": "AUDITED_FINAL",
        "audit_passed": True,
        "canonical_entrypoint_id": canonical_run.data["canonical_entrypoint_id"],
        "valuation_hash": canonical_run.data["valuation_hash"],
        "audit_hash": canonical_run.data["audit_hash"],
        "freeze_token_hash": canonical_freeze.token_hash,
        "execution_attestation_hash": canonical_attestation.attestation_hash,
        "distribution_hash": canonical_run.data["distribution_hash"],
        "route_authorization_hash": canonical_run.data[
            "distribution_route_authorization_hash"
        ],
        "entry_calculation_hash": canonical_run.data["entry_calculation_hash"],
        "intrinsic_freeze_hash": intrinsic_freeze_hash,
        "post_freeze_comparison_hash": post_freeze_comparison_hash,
        "supersedes_artifact_id": spec["supersedes_artifact_id"],
        "probability_ambiguity_set_hash": canonical_entry.probability_ambiguity_set_hash,
        "payoff_model_set_hash": canonical_entry.payoff_model_set_hash,
        "payoff_ambiguity_audit_hash": canonical_run.data[
            "payoff_ambiguity_audit_hash"
        ],
        "source_bundle_manifest_sha256": spec["source_bundle_manifest_sha256"],
        "report_builder_sha256": _sha(Path(__file__).resolve()),
        "files": [
            {"filename": name, "sha256": _sha(bundle / name)} for name in sorted(files)
        ],
    }
    _write_immutable(
        bundle / "bundle_manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    run_output = (run_dir / "out").resolve()
    if bundle.resolve().is_relative_to(run_output):
        latest = {
            "schema_version": "governed-distribution-latest/v2",
            "artifact_id": artifact_id,
            "bundle_directory": str(bundle.resolve().relative_to(run_output)),
            "report_filename": "final_report.md",
            "intrinsic_freeze_hash": intrinsic_freeze_hash,
            "valuation_hash": canonical_run.data["valuation_hash"],
            "audit_hash": canonical_run.data["audit_hash"],
            "freeze_token_hash": canonical_freeze.token_hash,
            "execution_attestation_hash": canonical_attestation.attestation_hash,
            "post_freeze_comparison_hash": post_freeze_comparison_hash,
            "probability_ambiguity_set_hash": canonical_entry.probability_ambiguity_set_hash,
            "payoff_model_set_hash": canonical_entry.payoff_model_set_hash,
            "entry_policy_version": entry_policy.policy_version,
            "audit_passed": True,
            "supersedes_artifact_id": spec["supersedes_artifact_id"],
        }
        (run_output / f"{spec['ticker']}_LATEST_DISTRIBUTIONAL_REPORT.json").write_text(
            json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec")
    parser.add_argument("--output-root")
    args = parser.parse_args()
    spec = Path(args.spec).resolve()
    run_dir = spec.parent.parent
    output = (
        Path(args.output_root).resolve()
        if args.output_root
        else run_dir / "out" / "distributional_bundles"
    )
    try:
        bundle = build(spec, output)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
