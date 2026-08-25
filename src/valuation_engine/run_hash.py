from __future__ import annotations

from hashlib import sha256
import json

from .assumption_compiler import CompiledAssumptionSet
from .ledger import EvidenceLedger
from .scenario_binding import BoundScenarioSet
from .valuation_execution import GenericValuationResult


def evidence_ledger_snapshot_hash(ledger: EvidenceLedger) -> str:
    """Recompute the canonical EvidenceLedger snapshot hash used by EVIDENCE_LEDGER."""
    payload = ledger.to_list()
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def compiled_input_evidence_hash(
    ledger: EvidenceLedger,
    evidence_ids: tuple[str, ...],
) -> str:
    """Replay the exact Evidence input hash contract used by the assumption compiler."""
    if not evidence_ids:
        raise ValueError("compiled assumption requires evidence_ids")
    parts: list[str] = []
    for evidence_id in evidence_ids:
        evidence = ledger.get(evidence_id)
        parts.append(
            f"{evidence.id}|{evidence.metric}|{evidence.value}|{evidence.unit}|"
            f"{evidence.effective_date}|{evidence.source_ref}"
        )
    return sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()


def compiled_assumption_set_hash(compiled: CompiledAssumptionSet) -> str:
    """Replay the v2 CompiledAssumptionSet identity/provenance hash contract."""
    payload = {
        "contract": "compiled_assumption_set/v2",
        "target_id": compiled.target_id,
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
                compiled.assumptions,
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


def bound_scenario_set_hash(
    compiled: CompiledAssumptionSet,
    scenario_set: BoundScenarioSet,
) -> str:
    """Replay the exact BoundScenarioSet hash contract against its compiled parent."""
    serialized = "\n".join(
        [
            compiled.assumption_set_hash,
            scenario_set.calibration_status.value,
            scenario_set.calibration_snapshot_hash or "NO_CERTIFICATE",
        ]
        + [
            f"{scenario.scenario_id}|"
            f"{scenario.probability if scenario.probability is not None else 'NA'}|"
            + ",".join(
                sorted(
                    f"{item.key}:{item.measure.amount}:{item.measure.unit}"
                    for item in scenario.assumptions
                )
            )
            for scenario in scenario_set.scenarios
        ]
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def generic_valuation_hash(
    scenario_set: BoundScenarioSet,
    valuation: GenericValuationResult,
) -> str:
    """Replay valuation value, scope and explicit UNVALUED_NOT_ZERO contract."""
    serialized = "\n".join(
        [
            scenario_set.scenario_set_hash,
            valuation.reporting_unit,
            f"scope={valuation.scope.value}",
        ]
        + [
            (
                f"unvalued={item.asset_id}|{item.segment_id}|{item.status.value}|"
                f"{item.resolution_status}|{item.rationale}|"
                f"{','.join(item.missing_assumptions)}"
            )
            for item in valuation.unvalued_segments
        ]
        + [
            (
                f"{item.scenario_id}|{item.equity_value_amount}|"
                f"{item.diluted_shares}|{item.value_per_share}|"
                f"{item.aggregation_hash}|"
                f"{','.join(item.economic_path_ids)}"
            )
            for item in valuation.scenarios
        ]
        + [
            "expected="
            + (
                str(valuation.expected_value_per_share)
                if valuation.expected_value_per_share is not None
                else "NA"
            )
        ]
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def compiled_evidence_hash_mismatches(
    compiled: CompiledAssumptionSet,
    ledger: EvidenceLedger,
) -> tuple[str, ...]:
    """Return scenario/key identities whose compiled Evidence hash no longer replays."""
    mismatches: list[str] = []
    for item in compiled.assumptions:
        try:
            replayed = compiled_input_evidence_hash(ledger, item.evidence_ids)
        except ValueError:
            mismatches.append(f"{item.scenario_id}/{item.key}")
            continue
        if replayed != item.input_evidence_hash:
            mismatches.append(f"{item.scenario_id}/{item.key}")
    return tuple(mismatches)
