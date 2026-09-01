from decimal import Decimal
from pathlib import Path

import pytest

import valuation_engine.assumption_range_rules as range_rules_module
from valuation_engine.assumption_compiler import (
    AssumptionSpec,
    CompilationStatus,
    compile_assumptions,
)
from valuation_engine.assumption_range_rules import (
    AssumptionRangeRuleError,
    apply_reviewed_assumption_ranges,
    load_assumption_range_rule_registry,
)
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.records import (
    AffectedVariable,
    BridgeRecord,
    Direction,
    EvidenceRecord,
    EvidenceSourceLayer,
    HypothesisRecord,
)


def _evidence(
    evidence_id: str,
    metric: str,
    value,
    *,
    effective_date: str,
    layer: EvidenceSourceLayer = EvidenceSourceLayer.REALIZED_OR_FILING,
    target: str = "T",
) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        target=target,
        metric=metric,
        value=value,
        unit="ratio",
        source_layer=layer,
        effective_date=effective_date,
        observed_date="2026-07-01",
        source_name="filing",
        source_ref=f"https://example.com/{evidence_id}",
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def _rule_file(tmp_path: Path, *, source_layer: str = "realized_or_filing") -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(
        "\n".join(
            [
                "schema_version: assumption_range_rule_registry/v1",
                "rules:",
                "  - rule_id: utilization-filing-history-v1",
                "    assumption_key: utilization",
                "    anchor_metric: utilization_history",
                "    canonical_unit: ratio",
                "    lookback_observations: 3",
                "    min_observations: 3",
                "    lower_multiplier: 1",
                "    upper_multiplier: 1",
                "    source_layers:",
                f"      - {source_layer}",
                "    review_ref: docs/ASSUMPTION_RANGE_RULES.md#utilization-filing-history-v1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_reviewed_rule_derives_min_max_from_recent_filing_history(tmp_path):
    registry = load_assumption_range_rule_registry(_rule_file(tmp_path))
    ledger = EvidenceLedger(
        (
            _evidence("H1", "utilization_history", "0.70", effective_date="2023-12-31"),
            _evidence("H2", "utilization_history", "0.80", effective_date="2024-12-31"),
            _evidence("H3", "utilization_history", "0.90", effective_date="2025-12-31"),
        )
    )
    spec = AssumptionSpec(
        "utilization",
        "Base",
        "B1",
        "ratio",
        "identity_observation",
        min_value=Decimal("0.99"),
        max_value=Decimal("1.00"),
    )
    applied = apply_reviewed_assumption_ranges(
        (spec,),
        ledger=ledger,
        target_id="T",
        registry=registry,
        llm_bounds=(("Base", "utilization", "0.99", "1.00"),),
    )
    assert applied.specs[0].min_value == Decimal("0.70")
    assert applied.specs[0].max_value == Decimal("0.90")
    assert applied.receipts[0].anchor_evidence_ids == ("H3", "H2", "H1")
    assert applied.ignored_llm_bounds == (("Base", "utilization", "0.99", "1.00"),)


def test_range_rule_refuses_analyst_underwriting_as_anchor(tmp_path):
    with pytest.raises(AssumptionRangeRuleError, match="only realized_or_filing"):
        load_assumption_range_rule_registry(
            _rule_file(tmp_path, source_layer="analyst_underwriting")
        )


def test_range_rule_fails_closed_when_required_history_is_missing(tmp_path):
    registry = load_assumption_range_rule_registry(_rule_file(tmp_path))
    ledger = EvidenceLedger(
        (
            _evidence("H1", "utilization_history", "0.80", effective_date="2025-12-31"),
        )
    )
    with pytest.raises(AssumptionRangeRuleError, match="requires 3 filing observations"):
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


def test_range_rule_never_uses_another_targets_filing_history(tmp_path):
    registry = load_assumption_range_rule_registry(_rule_file(tmp_path))
    ledger = EvidenceLedger(
        (
            _evidence("U1", "utilization_history", "0.70", effective_date="2023-12-31", target="U"),
            _evidence("U2", "utilization_history", "0.80", effective_date="2024-12-31", target="U"),
            _evidence("U3", "utilization_history", "0.90", effective_date="2025-12-31", target="U"),
        )
    )
    with pytest.raises(AssumptionRangeRuleError, match="found 0"):
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


def test_compiler_ignores_unreviewed_llm_bounds_but_enforces_reviewed_rule(tmp_path, monkeypatch):
    direct = _evidence("E1", "utilization", "0.95", effective_date="2026-06-30")
    history = (
        _evidence("H1", "utilization_history", "0.70", effective_date="2023-12-31"),
        _evidence("H2", "utilization_history", "0.80", effective_date="2024-12-31"),
        _evidence("H3", "utilization_history", "0.90", effective_date="2025-12-31"),
    )
    ledger = EvidenceLedger((direct, *history))
    hypothesis = HypothesisRecord(
        id="HYP",
        statement="utilization persists",
        causal_chain=("filing", "utilization", "value"),
        supporting_evidence_ids=("E1",),
        kill_conditions=("utilization falls",),
    )
    bridge = BridgeRecord(
        id="B1",
        evidence_ids=("E1",),
        hypothesis_id="HYP",
        affected_variable=AffectedVariable.UTILIZATION,
        direction=Direction.UP,
        old_value=0.90,
        new_value=0.95,
        unit="ratio",
        rationale="identity filing observation",
        confidence=0.8,
        kill_condition="utilization falls",
        verification_event="next filing",
        economic_path_id="PATH:UTIL",
    )
    spec = AssumptionSpec(
        "utilization",
        "Base",
        "B1",
        "ratio",
        "identity_observation",
        min_value=Decimal("0"),
        max_value=Decimal("1"),
    )

    # Empty production registry: the draft's 0..1 range has no authority.
    unreviewed = compile_assumptions(
        target_id="T",
        ledger=ledger,
        hypotheses=(hypothesis,),
        bridges=(bridge,),
        specs=(spec,),
        bridge_input_map={},
    )
    assert unreviewed.status is CompilationStatus.COMPILED

    # Test-only monkeypatch replaces the canonical frozen registry; production
    # compilation exposes no registry-path override.
    monkeypatch.setattr(
        range_rules_module,
        "DEFAULT_RANGE_RULE_REGISTRY_PATH",
        _rule_file(tmp_path),
    )
    # A reviewed filing-history rule derives 0.70..0.90 and blocks 0.95.
    reviewed = compile_assumptions(
        target_id="T",
        ledger=ledger,
        hypotheses=(hypothesis,),
        bridges=(bridge,),
        specs=(spec,),
        bridge_input_map={},
    )
    assert reviewed.status is CompilationStatus.BLOCKED
    assert any(item.code == "DOMAIN_VIOLATION" for item in reviewed.findings)
