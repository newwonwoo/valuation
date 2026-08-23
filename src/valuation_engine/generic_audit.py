from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .ablation import AblationBatchResult, AblationStatus
from .assumption_compiler import CompiledAssumptionSet
from .control_plane import DoctrineCoverageEntry, validate_doctrine_coverage
from .records import AuditFinding, AuditReport, CalibrationStatus
from .risk_adapters import LiveBetaStageResult, LiveWACCStageResult
from .risk_impact import audit_risk_consumption
from .scenario_binding import BoundScenarioSet
from .valuation_execution import GenericValuationResult


_PROHIBITED_PRE_FREEZE_KEYS = {
    "market_price",
    "current_market_price",
    "target_price",
    "target_multiple",
    "target_company_consensus",
    "street_consensus",
    "rating",
}


@dataclass(frozen=True)
class GenericAuditResult:
    report: AuditReport
    audit_hash: str

    @property
    def passed(self) -> bool:
        return self.report.passed


def audit_generic_intrinsic(
    *,
    compiled: CompiledAssumptionSet,
    scenario_set: BoundScenarioSet,
    valuation: GenericValuationResult,
    doctrine_coverage: tuple[DoctrineCoverageEntry, ...],
    expected_module_ids: tuple[str, ...],
    run_context_keys: tuple[str, ...] = (),
    decision_impact: AblationBatchResult | None = None,
    selected_methods: tuple[str, ...] = (),
    beta_result: LiveBetaStageResult | None = None,
    wacc_result: LiveWACCStageResult | None = None,
) -> GenericAuditResult:
    findings: list[AuditFinding] = []

    leaked = tuple(sorted(set(run_context_keys).intersection(_PROHIBITED_PRE_FREEZE_KEYS)))
    findings.append(
        AuditFinding(
            "pre_freeze_market_isolation",
            not leaked,
            True,
            "no target Street/current-price key may exist before freeze" if not leaked else f"leaked keys: {', '.join(leaked)}",
        )
    )

    traceability_ok = bool(compiled.assumptions) and all(
        item.bridge_id
        and item.hypothesis_id
        and item.evidence_ids
        and item.economic_path_id
        and item.input_evidence_hash
        and item.transform_id
        for item in compiled.assumptions
    )
    findings.append(
        AuditFinding(
            "compiled_traceability",
            traceability_ok,
            True,
            "every compiled assumption must preserve Evidence→Hypothesis→Bridge→Transform→EconomicPath trace",
        )
    )

    target_match = compiled.target_id == scenario_set.target_id
    findings.append(AuditFinding("target_identity_consistency", target_match, True, "compiled/scenario target IDs must match"))

    scenario_ids = tuple(item.scenario_id for item in scenario_set.scenarios)
    valuation_ids = tuple(item.scenario_id for item in valuation.scenarios)
    scenario_coverage_ok = set(scenario_ids) == set(valuation_ids) and len(valuation_ids) == len(set(valuation_ids))
    findings.append(
        AuditFinding(
            "scenario_valuation_coverage",
            scenario_coverage_ok,
            True,
            "valuation must cover each bound scenario exactly once",
        )
    )

    if scenario_set.numeric_weighting_allowed:
        probability_ok = (
            scenario_set.calibration_status is CalibrationStatus.CALIBRATED
            and all(item.probability is not None for item in scenario_set.scenarios)
            and valuation.expected_value_per_share is not None
        )
        probability_detail = "calibrated weights require a numeric expected value"
    else:
        probability_ok = valuation.expected_value_per_share is None
        probability_detail = "uncalibrated/descriptive scenarios must not emit numeric expected value"
    findings.append(AuditFinding("probability_integrity", probability_ok, True, probability_detail))

    path_trace_ok = all(
        item.economic_path_ids and len(item.economic_path_ids) == len(set(item.economic_path_ids))
        for item in valuation.scenarios
    )
    findings.append(
        AuditFinding(
            "valuation_path_trace",
            path_trace_ok,
            True,
            "each scenario value must preserve unique economic paths including ownership/debt/dilution",
        )
    )

    risk_consumption = audit_risk_consumption(
        valuation=valuation,
        selected_methods=selected_methods,
        beta_result=beta_result,
        wacc_result=wacc_result,
    )
    risk_detail = (
        "selected valuation methods do not require a discount-rate risk chain"
        if not risk_consumption.required
        else (
            "Beta→WACC economic paths are present in every DCF/NPV scenario valuation"
            if risk_consumption.passed
            else "DCF/NPV valuation omitted required Beta/WACC paths for scenarios: "
            + ", ".join(risk_consumption.missing_scenarios)
        )
    )
    findings.append(
        AuditFinding(
            "risk_to_valuation_consumption",
            risk_consumption.passed,
            True,
            risk_detail,
        )
    )

    hashes_ok = bool(compiled.assumption_set_hash and scenario_set.scenario_set_hash and valuation.valuation_hash)
    findings.append(AuditFinding("immutable_hash_chain", hashes_ok, True, "compiled/scenario/valuation hashes are required"))

    try:
        validate_doctrine_coverage(doctrine_coverage, expected_module_ids=expected_module_ids)
        blockers = tuple(item.module_id for item in doctrine_coverage if item.unresolved_blocker)
        doctrine_ok = not blockers
        doctrine_detail = "pre-audit doctrine coverage complete" if doctrine_ok else f"unresolved blockers: {', '.join(blockers)}"
    except ValueError as exc:
        doctrine_ok = False
        doctrine_detail = str(exc)
    findings.append(AuditFinding("doctrine_coverage", doctrine_ok, True, doctrine_detail))

    if decision_impact is None:
        impact_ok = False
        impact_detail = "decision-impact artifact missing"
    else:
        failed = tuple(
            item.module_id
            for item in decision_impact.module_observations
            if item.status is AblationStatus.FAILED
        )
        not_measurable = tuple(
            item.module_id
            for item in decision_impact.module_observations
            if item.status is AblationStatus.NOT_MEASURABLE
        )
        impact_ok = not failed
        impact_detail = (
            "decision-impact measurement completed"
            + (f"; explicit NOT_MEASURABLE: {', '.join(not_measurable)}" if not_measurable else "")
            + (f"; failed: {', '.join(failed)}" if failed else "")
        )
    findings.append(
        AuditFinding(
            "decision_impact_trace",
            impact_ok,
            False,
            impact_detail,
        )
    )

    report = AuditReport(tuple(findings))
    payload = "\n".join(
        [compiled.assumption_set_hash, scenario_set.scenario_set_hash, valuation.valuation_hash]
        + [f"{item.check}|{item.passed}|{item.blocking}|{item.detail}" for item in report.findings]
    )
    return GenericAuditResult(report, sha256(payload.encode("utf-8")).hexdigest())
