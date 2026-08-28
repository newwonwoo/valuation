from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from typing import Callable

from .actual_units import Measure, measure_from_raw, to_decimal
from .ledger import EvidenceLedger
from .records import (
    AssumptionRecord,
    AffectedVariable,
    BridgeRecord,
    CalibrationStatus,
    EvidenceSourceLayer,
    HypothesisRecord,
)
from .runtime_authority import DecisionDomain, forbid_llm_decision


class CompilationStatus(str, Enum):
    COMPILED = "compiled"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class AssumptionSpec:
    key: str
    scenario_id: str
    bridge_id: str
    canonical_unit: str
    transform_id: str
    required: bool = True
    min_value: Decimal | None = None
    max_value: Decimal | None = None
    probability_only_if_calibrated: bool = False

    def validate(self) -> None:
        if not all((self.key, self.scenario_id, self.bridge_id, self.canonical_unit, self.transform_id)):
            raise ValueError("assumption spec requires key, scenario, bridge, unit and transform")
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError("assumption spec min_value cannot exceed max_value")


@dataclass(frozen=True)
class CompilationFinding:
    code: str
    detail: str
    blocking: bool = True


@dataclass(frozen=True)
class CompiledAssumption:
    key: str
    scenario_id: str
    measure: Measure
    bridge_id: str
    evidence_ids: tuple[str, ...]
    hypothesis_id: str
    economic_path_id: str
    transform_id: str
    input_evidence_hash: str
    calibration_status: CalibrationStatus | None = None


@dataclass(frozen=True)
class CompiledAssumptionSet:
    target_id: str
    assumptions: tuple[CompiledAssumption, ...]
    assumption_set_hash: str

    def get(self, key: str, scenario_id: str) -> CompiledAssumption:
        for item in self.assumptions:
            if item.key == key and item.scenario_id == scenario_id:
                return item
        raise KeyError((key, scenario_id))


@dataclass(frozen=True)
class CompilationResult:
    status: CompilationStatus
    assumption_set: CompiledAssumptionSet | None
    findings: tuple[CompilationFinding, ...]

    @property
    def passed(self) -> bool:
        return self.status is CompilationStatus.COMPILED and self.assumption_set is not None


Transform = Callable[[tuple[Measure, ...], str], Measure]


def _identity(inputs: tuple[Measure, ...], output_unit: str) -> Measure:
    if len(inputs) != 1:
        raise ValueError("identity transform requires one input")
    return inputs[0].convert_to(output_unit)


def _ratio(inputs: tuple[Measure, ...], output_unit: str) -> Measure:
    if len(inputs) != 2:
        raise ValueError("ratio transform requires numerator and denominator")
    if output_unit != "ratio":
        raise ValueError("ratio transform output unit must be ratio")
    numerator, denominator = inputs
    numerator_base = numerator.to_base()
    denominator_base = denominator.to_base()
    if numerator_base.unit != denominator_base.unit:
        raise ValueError("ratio transform requires matching base units")
    if denominator_base.amount == 0:
        raise ValueError("ratio denominator cannot be zero")
    return Measure(
        numerator_base.amount / denominator_base.amount,
        "ratio",
        max(numerator.as_of, denominator.as_of),
    )


def _product(inputs: tuple[Measure, ...], output_unit: str) -> Measure:
    if len(inputs) != 2:
        raise ValueError("product transform requires two inputs")
    left, right = inputs
    for ratio_candidate, value_candidate in ((left, right), (right, left)):
        try:
            ratio = ratio_candidate.convert_to("ratio")
        except ValueError:
            continue
        value = value_candidate.convert_to(output_unit)
        return Measure(ratio.amount * value.amount, output_unit, max(left.as_of, right.as_of))
    raise ValueError("product transform currently requires one ratio input")


def _weighted_average(inputs: tuple[Measure, ...], output_unit: str) -> Measure:
    if len(inputs) < 2 or len(inputs) % 2:
        raise ValueError("weighted_average requires value/weight pairs")
    total_weight = Decimal("0")
    weighted_sum = Decimal("0")
    as_of = ""
    for index in range(0, len(inputs), 2):
        value = inputs[index].convert_to(output_unit)
        weight = inputs[index + 1].convert_to("ratio")
        if weight.amount < 0:
            raise ValueError("weights cannot be negative")
        total_weight += weight.amount
        weighted_sum += value.amount * weight.amount
        as_of = max(as_of, value.as_of, weight.as_of)
    if total_weight == 0:
        raise ValueError("weighted_average total weight cannot be zero")
    return Measure(weighted_sum / total_weight, output_unit, as_of)


def _ramp_scaled_money(inputs: tuple[Measure, ...], output_unit: str) -> Measure:
    """Scale a frozen reference FCFF cohort by an explicit ramp-duration driver.

    The reference path preserves the reviewed underwriting shape.  A longer current
    ramp stretches that path and a shorter ramp accelerates it, capped at the
    declared steady-state FCFF.  This keeps timing changes from becoming trace-only
    metadata that cannot affect valuation.
    """
    if len(inputs) != 4:
        raise ValueError(
            "ramp_scaled_money requires reference FCFF, steady-state FCFF, "
            "reference ramp years and current ramp years"
        )
    reference_fcff = inputs[0].convert_to(output_unit)
    steady_state_fcff = inputs[1].convert_to(output_unit)
    reference_ramp = inputs[2].convert_to("years")
    current_ramp = inputs[3].convert_to("years")
    if reference_fcff.amount < 0 or steady_state_fcff.amount <= 0:
        raise ValueError("ramp FCFF inputs require non-negative reference and positive steady state")
    if reference_fcff.amount > steady_state_fcff.amount:
        raise ValueError("reference ramp FCFF cannot exceed steady-state FCFF")
    if reference_ramp.amount <= 0 or current_ramp.amount <= 0:
        raise ValueError("ramp durations must be positive")
    scaled = reference_fcff.amount * reference_ramp.amount / current_ramp.amount
    return Measure(
        min(steady_state_fcff.amount, scaled),
        output_unit,
        max(item.as_of for item in inputs),
    )


TRANSFORMS: dict[str, Transform] = {
    "identity_observation": _identity,
    "unit_conversion": _identity,
    "ratio": _ratio,
    "product": _product,
    "weighted_average": _weighted_average,
    "ramp_scaled_money": _ramp_scaled_money,
}


def compiled_assumption_set_digest(
    target_id: str,
    assumptions: tuple[CompiledAssumption, ...],
) -> str:
    """Hash every immutable assumption identity/provenance field used downstream."""
    if not target_id:
        raise ValueError("compiled assumption hash requires target_id")
    payload = {
        "contract": "compiled_assumption_set/v2",
        "target_id": target_id,
        "assumptions": [
            {
                "key": item.key,
                "scenario_id": item.scenario_id,
                "measure": {
                    "amount": str(item.measure.amount),
                    "unit": item.measure.unit,
                    "as_of": item.measure.as_of,
                },
                "bridge_id": item.bridge_id,
                "evidence_ids": list(item.evidence_ids),
                "hypothesis_id": item.hypothesis_id,
                "economic_path_id": item.economic_path_id,
                "transform_id": item.transform_id,
                "input_evidence_hash": item.input_evidence_hash,
                "calibration_status": (
                    item.calibration_status.value
                    if item.calibration_status is not None
                    else None
                ),
            }
            for item in sorted(
                assumptions,
                key=lambda row: (row.scenario_id, row.key, row.bridge_id),
            )
        ],
    }
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def compile_assumptions(
    *,
    target_id: str,
    ledger: EvidenceLedger,
    hypotheses: tuple[HypothesisRecord, ...],
    bridges: tuple[BridgeRecord, ...],
    specs: tuple[AssumptionSpec, ...],
    bridge_input_map: dict[str, tuple[str, ...]],
) -> CompilationResult:
    # An LLM may propose BridgeDraft objects, but it may never invoke the
    # committing compiler from inside its callback scope.
    forbid_llm_decision(DecisionDomain.ASSUMPTION_COMPILE)
    findings: list[CompilationFinding] = []
    if not target_id:
        findings.append(CompilationFinding("MISSING_TARGET", "target_id is required"))
    hypothesis_map = {item.id: item for item in hypotheses}
    bridge_map = {item.id: item for item in bridges}
    if len(hypothesis_map) != len(hypotheses):
        findings.append(CompilationFinding("DUPLICATE_HYPOTHESIS", "duplicate hypothesis id"))
    if len(bridge_map) != len(bridges):
        findings.append(CompilationFinding("DUPLICATE_BRIDGE", "duplicate bridge id"))
    spec_keys = [(item.scenario_id, item.key) for item in specs]
    if len(spec_keys) != len(set(spec_keys)):
        findings.append(CompilationFinding("DUPLICATE_SPEC", "duplicate scenario/key assumption spec"))

    compiled: list[CompiledAssumption] = []
    for spec in specs:
        try:
            spec.validate()
        except ValueError as exc:
            findings.append(CompilationFinding("INVALID_SPEC", str(exc)))
            continue

        bridge = bridge_map.get(spec.bridge_id)
        if bridge is None:
            if spec.required:
                findings.append(
                    CompilationFinding("MISSING_BRIDGE", f"{spec.scenario_id}/{spec.key} missing {spec.bridge_id}")
                )
            continue
        hypothesis = hypothesis_map.get(bridge.hypothesis_id)
        if hypothesis is None:
            findings.append(CompilationFinding("UNKNOWN_HYPOTHESIS", f"bridge {bridge.id} references unknown hypothesis"))
            continue
        if not bridge.kill_condition or not bridge.verification_event:
            findings.append(CompilationFinding("INCOMPLETE_BRIDGE", f"bridge {bridge.id} lacks kill/verification contract"))
            continue
        if spec.probability_only_if_calibrated and hypothesis.calibration_status is not CalibrationStatus.CALIBRATED:
            findings.append(
                CompilationFinding("UNCALIBRATED_PROBABILITY", f"{spec.key} requires CALIBRATED hypothesis probability")
            )
            continue

        input_ids = bridge_input_map.get(bridge.id, bridge.evidence_ids)
        if not input_ids:
            findings.append(CompilationFinding("MISSING_TRANSFORM_INPUT", f"bridge {bridge.id} has no transform inputs"))
            continue
        measures: list[Measure] = []
        evidence_hash_parts: list[str] = []
        source_layers: list[EvidenceSourceLayer] = []
        try:
            for evidence_id in input_ids:
                evidence = ledger.get(evidence_id)
                source_layers.append(evidence.source_layer)
                if evidence.source_layer is EvidenceSourceLayer.MARKET_COMPARISON:
                    raise ValueError("market comparison evidence cannot enter intrinsic compilation")
                measures.append(measure_from_raw(evidence.value, evidence.unit, evidence.effective_date))
                evidence_hash_parts.append(
                    f"{evidence.id}|{evidence.metric}|{evidence.value}|{evidence.unit}|{evidence.effective_date}|{evidence.source_ref}"
                )
        except ValueError as exc:
            findings.append(CompilationFinding("INVALID_EVIDENCE_INPUT", f"bridge {bridge.id}: {exc}"))
            continue

        if bridge.affected_variable is AffectedVariable.PRICE and source_layers and all(
            layer is EvidenceSourceLayer.POLICY_PRIMARY_SOURCE for layer in source_layers
        ):
            findings.append(
                CompilationFinding("POLICY_PRICE_AS_ENTERPRISE_PRICE", f"bridge {bridge.id} uses policy-only price evidence")
            )
            continue

        transform = TRANSFORMS.get(spec.transform_id)
        if transform is None:
            findings.append(CompilationFinding("UNREGISTERED_TRANSFORM", spec.transform_id))
            continue
        try:
            calculated = transform(tuple(measures), spec.canonical_unit)
        except ValueError as exc:
            findings.append(CompilationFinding("TRANSFORM_FAILED", f"bridge {bridge.id}: {exc}"))
            continue

        proposed = to_decimal(bridge.new_value)
        tolerance = max(Decimal("1e-12"), abs(calculated.amount) * Decimal("1e-9"))
        if abs(calculated.amount - proposed) > tolerance:
            findings.append(
                CompilationFinding(
                    "PROPOSAL_RECALC_MISMATCH",
                    f"bridge {bridge.id}: proposal={proposed} calculated={calculated.amount}",
                )
            )
            continue
        if spec.min_value is not None and calculated.amount < spec.min_value:
            findings.append(CompilationFinding("DOMAIN_VIOLATION", f"{spec.key} below minimum"))
            continue
        if spec.max_value is not None and calculated.amount > spec.max_value:
            findings.append(CompilationFinding("DOMAIN_VIOLATION", f"{spec.key} above maximum"))
            continue

        evidence_hash = sha256("\n".join(sorted(evidence_hash_parts)).encode("utf-8")).hexdigest()
        compiled.append(
            CompiledAssumption(
                key=spec.key,
                scenario_id=spec.scenario_id,
                measure=calculated,
                bridge_id=bridge.id,
                evidence_ids=tuple(input_ids),
                hypothesis_id=hypothesis.id,
                economic_path_id=bridge.economic_path_id,
                transform_id=spec.transform_id,
                input_evidence_hash=evidence_hash,
                calibration_status=hypothesis.calibration_status if spec.probability_only_if_calibrated else None,
            )
        )

    if any(item.blocking for item in findings):
        return CompilationResult(CompilationStatus.BLOCKED, None, tuple(findings))
    if not compiled:
        return CompilationResult(
            CompilationStatus.BLOCKED,
            None,
            (CompilationFinding("EMPTY_COMPILED_SET", "no assumptions compiled"),),
        )
    compiled_tuple = tuple(compiled)
    assumption_set = CompiledAssumptionSet(
        target_id=target_id,
        assumptions=compiled_tuple,
        assumption_set_hash=compiled_assumption_set_digest(target_id, compiled_tuple),
    )
    return CompilationResult(CompilationStatus.COMPILED, assumption_set, ())


def legacy_assumptions_from_compiled(compiled: CompiledAssumptionSet) -> tuple[AssumptionRecord, ...]:
    """Compatibility output only; live evaluators should consume CompiledAssumption directly."""
    return tuple(
        AssumptionRecord(
            key=item.key,
            scenario_id=item.scenario_id,
            value=float(item.measure.amount),
            unit=item.measure.unit,
            bridge_id=item.bridge_id,
        )
        for item in compiled.assumptions
    )
