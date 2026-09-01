from __future__ import annotations

from dataclasses import replace
import inspect

import valuation_engine.assumption_range_rules as range_rules_module
from tests import test_generic_audit_freeze_e2e as audit_e2e
from valuation_engine.assumption_compiler import (
    CompiledAssumptionSet,
    compile_assumptions,
)
from valuation_engine.assumption_range_rules import AssumptionRangeReceipt
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer
from valuation_engine.run_hash import (
    compiled_evidence_hash_mismatches,
    compiled_input_evidence_hash,
)


def _rule_file(tmp_path):
    path = tmp_path / "range_rules.yaml"
    path.write_text(
        "\n".join(
            (
                "schema_version: assumption_range_rule_registry/v1",
                "rules:",
                "  - rule_id: normalized-multiple-history-v1",
                "    assumption_key: normalized_multiple",
                "    anchor_metric: normalized_multiple_history",
                "    canonical_unit: multiple",
                "    lookback_observations: 3",
                "    min_observations: 3",
                "    lower_multiplier: 1",
                "    upper_multiplier: 1",
                "    source_layers:",
                "      - realized_or_filing",
                "    review_ref: docs/ASSUMPTION_RANGE_RULES.md#normalized-multiple-history-v1",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _run_with_reviewed_range(tmp_path, monkeypatch):
    rule_path = _rule_file(tmp_path)
    monkeypatch.setattr(
        range_rules_module,
        "DEFAULT_RANGE_RULE_REGISTRY_PATH",
        rule_path,
    )
    original_build_inputs = audit_e2e.build_inputs

    def build_inputs_with_range_history():
        ledger, hypotheses, bridges, specs = original_build_inputs()
        for index, (effective_date, value) in enumerate(
            (("2023-12-31", 7), ("2024-12-31", 8), ("2025-12-31", 9)),
            start=1,
        ):
            ledger.append(
                EvidenceRecord(
                    id=f"E:RANGE:{index}",
                    target="T",
                    metric="normalized_multiple_history",
                    value=value,
                    unit="multiple",
                    source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
                    effective_date=effective_date,
                    observed_date="2026-07-01",
                    source_name="filing history",
                    source_ref=f"filing#range/{index}",
                    source_grade="A",
                    confidence=1.0,
                    segment="core",
                )
            )
        return ledger, hypotheses, bridges, specs

    monkeypatch.setattr(audit_e2e, "build_inputs", build_inputs_with_range_history)
    return audit_e2e.run_path()



def test_compile_api_has_no_runtime_registry_path_override():
    assert "range_rule_registry_path" not in inspect.signature(compile_assumptions).parameters


def test_audit_rejects_hash_consistent_receipt_attached_to_wrong_assumption(tmp_path, monkeypatch):
    result = _run_with_reviewed_range(tmp_path, monkeypatch)
    compiled = result.data["compiled_assumption_set"]
    ledger = result.data["evidence_ledger"]
    original = next(item for item in compiled.assumptions if item.range_provenance is not None)
    receipt = original.range_provenance
    assert isinstance(receipt, AssumptionRangeReceipt)
    forged_receipt = replace(receipt, assumption_key="wrong_assumption")
    forged_hash = compiled_input_evidence_hash(
        ledger,
        original.evidence_ids,
        range_provenance=forged_receipt,
    )
    forged = replace(
        original,
        range_provenance=forged_receipt,
        input_evidence_hash=forged_hash,
    )
    forged_set = CompiledAssumptionSet(
        target_id=compiled.target_id,
        assumptions=tuple(forged if item is original else item for item in compiled.assumptions),
        assumption_set_hash=compiled.assumption_set_hash,
    )
    assert f"{original.scenario_id}/{original.key}" in compiled_evidence_hash_mismatches(
        forged_set, ledger
    )


def test_audit_rederives_bounds_instead_of_trusting_hash_consistent_receipt(tmp_path, monkeypatch):
    result = _run_with_reviewed_range(tmp_path, monkeypatch)
    compiled = result.data["compiled_assumption_set"]
    ledger = result.data["evidence_ledger"]
    original = next(item for item in compiled.assumptions if item.range_provenance is not None)
    receipt = original.range_provenance
    assert isinstance(receipt, AssumptionRangeReceipt)
    forged_receipt = replace(receipt, max_value=receipt.max_value + 100)
    forged_hash = compiled_input_evidence_hash(
        ledger,
        original.evidence_ids,
        range_provenance=forged_receipt,
    )
    forged = replace(
        original,
        range_provenance=forged_receipt,
        input_evidence_hash=forged_hash,
    )
    forged_set = CompiledAssumptionSet(
        target_id=compiled.target_id,
        assumptions=tuple(forged if item is original else item for item in compiled.assumptions),
        assumption_set_hash=compiled.assumption_set_hash,
    )
    assert f"{original.scenario_id}/{original.key}" in compiled_evidence_hash_mismatches(
        forged_set, ledger
    )
