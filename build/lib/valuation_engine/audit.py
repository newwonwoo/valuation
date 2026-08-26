from __future__ import annotations

from dataclasses import dataclass, replace
from inspect import signature

from .engine import run_valuation
from .models import Scenario
from .records import AuditFinding, AuditReport


def audit_model(scenarios: list[Scenario], shares: int) -> dict:
    base = run_valuation(scenarios, shares)
    anchor_pass = "market_price" not in signature(run_valuation).parameters

    asp_up = [replace(s, poly_asp_usd_per_kg=s.poly_asp_usd_per_kg + 1.0) for s in scenarios]
    asp_stress = run_valuation(asp_up, shares)
    asp_pass = asp_stress.expected_value_per_share > base.expected_value_per_share

    utilization_up = [replace(s, poly_utilization=min(1.0, s.poly_utilization + 0.01)) for s in scenarios]
    utilization_stress = run_valuation(utilization_up, shares)
    utilization_pass = utilization_stress.expected_value_per_share > base.expected_value_per_share

    debt_up = [replace(s, net_debt_trn_krw=s.net_debt_trn_krw + 0.1) for s in scenarios]
    debt_stress = run_valuation(debt_up, shares)
    debt_pass = debt_stress.expected_value_per_share < base.expected_value_per_share

    discount_up = [replace(s, discount_rate=s.discount_rate + 0.01) for s in scenarios]
    discount_stress = run_valuation(discount_up, shares)
    discount_pass = discount_stress.expected_value_per_share < base.expected_value_per_share

    return {
        "probabilities_sum_to_one": abs(sum(s.probability for s in scenarios) - 1) < 1e-9,
        "current_price_anchor_zero": anchor_pass,
        "asp_sensitivity_positive": asp_pass,
        "utilization_sensitivity_positive": utilization_pass,
        "net_debt_sensitivity_negative": debt_pass,
        "discount_rate_sensitivity_negative": discount_pass,
        "asp_plus_1_expected_value_change": asp_stress.expected_value_per_share - base.expected_value_per_share,
        "pass": anchor_pass and asp_pass and utilization_pass and debt_pass and discount_pass,
    }


@dataclass(frozen=True)
class ValueContribution:
    contribution_id: str
    evidence_ids: tuple[str, ...]
    economic_path_id: str
    category: str


@dataclass(frozen=True)
class ExpansionTreatment:
    expansion_id: str
    future_ebitda_included: bool
    gross_capex_deducted: bool
    funding_gap_or_terminal_debt_included: bool


def assert_no_duplicate_value_paths(contributions: list[ValueContribution]) -> None:
    seen: dict[tuple[str, str], str] = {}
    for contribution in contributions:
        for evidence_id in contribution.evidence_ids:
            key = (evidence_id, contribution.economic_path_id)
            prior = seen.get(key)
            if prior is not None and prior != contribution.contribution_id:
                raise ValueError(
                    f"duplicate value path for {evidence_id}/{contribution.economic_path_id}: "
                    f"{prior}, {contribution.contribution_id}"
                )
            seen[key] = contribution.contribution_id


def assert_no_capex_double_count(treatments: list[ExpansionTreatment]) -> None:
    for item in treatments:
        if item.future_ebitda_included and item.gross_capex_deducted and item.funding_gap_or_terminal_debt_included:
            raise ValueError(f"CAPEX double count for expansion {item.expansion_id}")


def gate_report(core_audit: dict, *, traceability_ok: bool, extra_findings: list[AuditFinding] | None = None) -> AuditReport:
    findings = [
        AuditFinding("current_price_isolation", bool(core_audit["current_price_anchor_zero"]), True, "intrinsic value must ignore price"),
        AuditFinding("probability_integrity", bool(core_audit["probabilities_sum_to_one"]), True, "probabilities must sum to one"),
        AuditFinding("asp_sensitivity", bool(core_audit["asp_sensitivity_positive"]), True, "ASP increase must increase OCI value"),
        AuditFinding("utilization_sensitivity", bool(core_audit["utilization_sensitivity_positive"]), True, "utilization increase must increase OCI value"),
        AuditFinding("net_debt_sensitivity", bool(core_audit["net_debt_sensitivity_negative"]), True, "net debt increase must reduce equity value"),
        AuditFinding("discount_rate_sensitivity", bool(core_audit["discount_rate_sensitivity_negative"]), True, "discount increase must reduce PV"),
        AuditFinding("source_traceability", traceability_ok, True, "every assumption requires evidence, hypothesis and bridge"),
    ]
    findings.extend(extra_findings or [])
    return AuditReport(tuple(findings))
