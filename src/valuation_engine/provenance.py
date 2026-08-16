from __future__ import annotations

from dataclasses import dataclass

from .ledger import EvidenceLedger, validate_traceability
from .records import (
    AffectedVariable,
    AssumptionRecord,
    BridgeRecord,
    CalibrationStatus,
    Direction,
    EvidenceRecord,
    EvidenceSourceLayer,
    HypothesisRecord,
)


@dataclass(frozen=True)
class TraceBundle:
    ledger: EvidenceLedger
    hypotheses: tuple[HypothesisRecord, ...]
    bridges: tuple[BridgeRecord, ...]
    assumptions: tuple[AssumptionRecord, ...]

    def validate(self) -> None:
        validate_traceability(self.ledger, self.hypotheses, self.bridges, self.assumptions)


def build_oci_legacy_trace(raw: dict, *, run_id: str) -> TraceBundle:
    """Migration trace for the frozen OCI v1.1 regression fixture.

    This does not claim that workbook assumptions are primary evidence. It records
    them as external legacy-model observations until fresh research replaces them.
    """
    company = raw["company"]["name"]
    ticker = raw["company"]["ticker"]
    items: list[tuple[str, str, float, str, EvidenceSourceLayer]] = []
    items.append(("company.shares", "COMMON", float(raw["company"]["shares"]), "shares", EvidenceSourceLayer.REALIZED_OR_FILING))
    for key, value in raw["common"].items():
        layer = EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN if "capacity" in key or "economic_share" in key else EvidenceSourceLayer.EXTERNAL_REFERENCE
        items.append((f"common.{key}", "COMMON", float(value), _unit_for(key), layer))
    for scenario in raw["scenarios"]:
        scenario_name = scenario["name"]
        for key, value in scenario.items():
            if key == "name":
                continue
            items.append((f"scenarios.{scenario_name}.{key}", scenario_name, float(value), _unit_for(key), EvidenceSourceLayer.EXTERNAL_REFERENCE))

    evidence: list[EvidenceRecord] = []
    bridges: list[BridgeRecord] = []
    assumptions: list[AssumptionRecord] = []
    evidence_ids: list[str] = []
    for index, (key, scenario_id, value, unit, layer) in enumerate(items, start=1):
        safe = f"{index:03d}"
        evidence_id = f"EV-OCI-LEGACY-{safe}"
        bridge_id = f"BR-OCI-LEGACY-{safe}"
        evidence_ids.append(evidence_id)
        evidence.append(EvidenceRecord(
            id=evidence_id,
            target=f"{company} ({ticker})",
            metric=key,
            value=value,
            unit=unit,
            source_layer=layer,
            effective_date="2026-08-14",
            observed_date="2026-08-16",
            source_name="OCI Holdings Valuation Skill v1.1",
            source_ref="OCI_Holdings_Valuation_Skill_v1.1 (1).xlsx",
            source_grade="LEGACY_REGRESSION",
            confidence=0.5 if layer is EvidenceSourceLayer.EXTERNAL_REFERENCE else 0.9,
            segment=_segment_for(key),
            notes="Legacy regression input; replace with primary-source evidence before live use.",
            critical=True,
        ))
        bridges.append(BridgeRecord(
            id=bridge_id,
            evidence_ids=(evidence_id,),
            hypothesis_id="HY-OCI-LEGACY-CONTINUITY",
            affected_variable=_affected_variable(key),
            direction=Direction.UNCHANGED,
            old_value=value,
            new_value=value,
            unit=unit,
            rationale="Preserve the audited OCI v1.1 regression input during v0.3 architecture migration.",
            confidence=0.5,
            kill_condition="Fresh primary evidence contradicts the legacy input.",
            verification_event="Next primary-source OCI research run.",
            economic_path_id=_economic_path_for(key),
            run_id=run_id,
        ))
        assumptions.append(AssumptionRecord(key, scenario_id, value, unit, bridge_id, run_id))

    hypothesis = HypothesisRecord(
        id="HY-OCI-LEGACY-CONTINUITY",
        statement="The frozen OCI v1.1 inputs remain usable only as a regression baseline during migration.",
        causal_chain=("legacy workbook input", "deterministic OCI model variable", "regression fair value"),
        supporting_evidence_ids=tuple(evidence_ids),
        probability=0.5,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        kill_conditions=("Fresh primary evidence contradicts any legacy input.",),
        next_checks=("Replace legacy observations with DART, IR and policy evidence.",),
        run_id=run_id,
    )
    bundle = TraceBundle(EvidenceLedger(evidence), (hypothesis,), tuple(bridges), tuple(assumptions))
    bundle.validate()
    return bundle


def _unit_for(key: str) -> str:
    if key == "probability" or "utilization" in key or "discount_rate" in key or "economic_share" in key:
        return "ratio"
    if "capacity_kmt" in key:
        return "kMT"
    if "capacity_gw" in key:
        return "GW"
    if "usd_per_kg" in key:
        return "USD/kg"
    if "usd_per_w" in key:
        return "USD/W"
    if "fx_" in key:
        return "KRW/USD"
    if "multiple" in key:
        return "x"
    if "debt" in key or "business_pv" in key:
        return "KRW_trillion"
    if "terminal_years" in key:
        return "years"
    if key.endswith("shares"):
        return "shares"
    return "dimensionless"


def _affected_variable(key: str) -> AffectedVariable:
    for token, variable in (
        ("probability", AffectedVariable.PROBABILITY),
        ("asp", AffectedVariable.PRICE),
        ("capacity", AffectedVariable.QUANTITY),
        ("utilization", AffectedVariable.UTILIZATION),
        ("cost", AffectedVariable.MARGIN),
        ("ebitda", AffectedVariable.MARGIN),
        ("debt", AffectedVariable.NET_DEBT),
        ("discount", AffectedVariable.DISCOUNT_RATE),
        ("multiple", AffectedVariable.MULTIPLE),
        ("business_pv", AffectedVariable.SEGMENT_VALUE),
        ("shares", AffectedVariable.SHARE_COUNT),
    ):
        if token in key:
            return variable
    return AffectedVariable.SEGMENT_VALUE


def _segment_for(key: str) -> str:
    if "poly" in key:
        return "polysilicon"
    if "wafer" in key:
        return "wafer"
    if "business" in key:
        return "other_business"
    return "consolidated"


def _economic_path_for(key: str) -> str:
    scenario = key.split(".")[1] if key.startswith("scenarios.") else "common"
    return f"{scenario}:{_segment_for(key)}:{_affected_variable(key).value}"
