from decimal import Decimal

import pytest

import valuation_engine.assumption_range_rules as range_rules_module
from tests import test_generic_audit_freeze_e2e as audit_e2e
from valuation_engine.assumption_compiler import AssumptionSpec
from valuation_engine.assumption_range_rules import (
    AssumptionRangeRuleError,
    apply_reviewed_assumption_ranges,
    load_assumption_range_rule_registry,
)
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer
from valuation_engine.run_hash import compiled_evidence_hash_mismatches


def _rule_file(tmp_path, *, assumption_key="utilization", anchor_metric="utilization_history", unit="ratio"):
    path = tmp_path / "range_rules.yaml"
    path.write_text(
        "\n".join(
            (
                "schema_version: assumption_range_rule_registry/v1",
                "rules:",
                "  - rule_id: reviewed-history-v1",
                f"    assumption_key: {assumption_key}",
                f"    anchor_metric: {anchor_metric}",
                f"    canonical_unit: {unit}",
                "    lookback_observations: 3",
                "    min_observations: 3",
                "    lower_multiplier: 1",
                "    upper_multiplier: 1",
                "    source_layers:",
                "      - realized_or_filing",
                "    review_ref: docs/ASSUMPTION_RANGE_RULES.md#reviewed-history-v1",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _evidence(evidence_id, value, effective_date):
    return EvidenceRecord(
        id=evidence_id,
        target="T",
        metric="utilization_history",
        value=value,
        unit="ratio",
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date=effective_date,
        observed_date="2026-07-01",
        source_name="filing history",
        source_ref=f"filing#{evidence_id}",
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def test_effective_date_suffixes_do_not_create_fake_history_observations(tmp_path):
    registry = load_assumption_range_rule_registry(_rule_file(tmp_path))
    ledger = EvidenceLedger(
        (
            _evidence("H1", "0.70", "2024-12-31"),
            _evidence("H2", "0.80", "2025-12-31T01:00:00Z"),
            _evidence("H3", "0.90", "2025-12-31T02:00:00Z"),
        )
    )
    with pytest.raises(AssumptionRangeRuleError, match="ambiguous.*2025-12-31"):
        apply_reviewed_assumption_ranges(
            (
                AssumptionSpec(
                    "utilization",
                    "Base",
                    "B1",
                    "ratio",
                    "identity_observation",
                ),
            ),
            ledger=ledger,
            target_id="T",
            registry=registry,
        )


def test_non_finite_rule_multiplier_is_rejected(tmp_path):
    path = _rule_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "    upper_multiplier: 1", "    upper_multiplier: .inf"
        ),
        encoding="utf-8",
    )
    with pytest.raises(AssumptionRangeRuleError, match="multipliers must be finite"):
        load_assumption_range_rule_registry(path)


def test_reviewed_range_provenance_replays_through_audit_and_freeze(tmp_path, monkeypatch):
    path = _rule_file(
        tmp_path,
        assumption_key="normalized_multiple",
        anchor_metric="normalized_multiple_history",
        unit="multiple",
    )
    monkeypatch.setattr(
        range_rules_module,
        "DEFAULT_RANGE_RULE_REGISTRY_PATH",
        path,
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
    result = audit_e2e.run_path()

    assert result.blocked_reasons == ()
    assert result.freeze_token is not None
    compiled = result.data["compiled_assumption_set"]
    ranged = tuple(
        item for item in compiled.assumptions if item.key == "normalized_multiple"
    )
    assert ranged
    assert all(item.range_provenance is not None for item in ranged)
    assert compiled_evidence_hash_mismatches(
        compiled, result.data["evidence_ledger"]
    ) == ()
