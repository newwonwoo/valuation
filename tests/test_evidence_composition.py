from __future__ import annotations

from decimal import Decimal

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption, CompiledAssumptionSet
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.evidence_composition import (
    EvidenceCompositionError,
    EvidenceCompositionPolicy,
    build_evidence_composition_report,
    evidence_composition_audit_adapter,
    layer_label_ko,
)
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer


def _evidence(
    evidence_id: str,
    layer: EvidenceSourceLayer,
    *,
    confidence: float = 0.6,
) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        target="KR:DART:00000000",
        metric=f"metric_{evidence_id}",
        value=1.0,
        unit="KRW_billion",
        source_layer=layer,
        effective_date="2026-08-27",
        observed_date="2026-08-27",
        source_name="source",
        source_ref="https://example.test/doc",
        source_grade="A",
        confidence=confidence,
        segment="seg",
    )


def _assumption(key: str, evidence_ids: tuple[str, ...]) -> CompiledAssumption:
    return CompiledAssumption(
        key=key,
        scenario_id="Core",
        measure=Measure(Decimal("1"), "KRW_billion", "2026-08-27"),
        bridge_id=f"BR_{key}",
        evidence_ids=evidence_ids,
        hypothesis_id="H1",
        economic_path_id=f"path:{key}",
        transform_id="identity_observation",
        input_evidence_hash="hash",
    )


def _compiled(assumptions: tuple[CompiledAssumption, ...]) -> CompiledAssumptionSet:
    return CompiledAssumptionSet(
        target_id="KR:DART:00000000",
        assumptions=assumptions,
        assumption_set_hash="assumption-set-hash",
    )


def _ledger(records: tuple[EvidenceRecord, ...]) -> EvidenceLedger:
    return EvidenceLedger(records)


def test_valuation_inputs_are_measured_separately_from_the_ledger():
    """Filings sitting in the ledger as context must not count as value drivers."""
    ledger = _ledger(
        (
            _evidence("E_FILING_1", EvidenceSourceLayer.REALIZED_OR_FILING),
            _evidence("E_FILING_2", EvidenceSourceLayer.REALIZED_OR_FILING),
            _evidence("E_UW_1", EvidenceSourceLayer.ANALYST_UNDERWRITING),
        )
    )
    compiled = _compiled((_assumption("fcff_year_1", ("E_UW_1",)),))
    report = build_evidence_composition_report(ledger=ledger, compiled=compiled)

    assert report.ledger_active_count == 3
    assert report.valuation_input_count == 1
    assert report.valuation_input_evidence_ids == ("E_UW_1",)
    assert report.valuation_underwriting_share == Decimal("1")
    assert report.valuation_primary_backed_share == Decimal("0")
    assert report.layer_count(EvidenceSourceLayer.REALIZED_OR_FILING) == 0


def test_primary_backed_share_counts_filings_and_official_plans():
    ledger = _ledger(
        (
            _evidence("E1", EvidenceSourceLayer.REALIZED_OR_FILING),
            _evidence("E2", EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN),
            _evidence("E3", EvidenceSourceLayer.ANALYST_UNDERWRITING),
            _evidence("E4", EvidenceSourceLayer.POLICY_PRIMARY_SOURCE),
        )
    )
    compiled = _compiled(
        (
            _assumption("a", ("E1", "E2")),
            _assumption("b", ("E3", "E4")),
        )
    )
    report = build_evidence_composition_report(ledger=ledger, compiled=compiled)
    assert report.valuation_input_count == 4
    assert report.valuation_primary_backed_share == Decimal("0.5")
    assert report.valuation_underwriting_share == Decimal("0.25")


def test_shared_evidence_is_counted_once():
    ledger = _ledger((_evidence("E1", EvidenceSourceLayer.ANALYST_UNDERWRITING),))
    compiled = _compiled(
        (
            _assumption("a", ("E1",)),
            _assumption("b", ("E1",)),
        )
    )
    report = build_evidence_composition_report(ledger=ledger, compiled=compiled)
    assert report.valuation_input_count == 1


def test_superseded_evidence_leaves_the_ledger_population():
    original = _evidence("E1", EvidenceSourceLayer.REALIZED_OR_FILING)
    replacement = EvidenceRecord(
        id="E2",
        target=original.target,
        metric=original.metric,
        value=2.0,
        unit=original.unit,
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-08-28",
        observed_date="2026-08-28",
        source_name="source",
        source_ref="https://example.test/doc2",
        source_grade="A",
        confidence=0.9,
        segment=original.segment,
        supersedes_id="E1",
    )
    ledger = _ledger((original, replacement))
    compiled = _compiled((_assumption("a", ("E2",)),))
    report = build_evidence_composition_report(ledger=ledger, compiled=compiled)
    assert report.ledger_active_count == 1


def test_mean_confidence_is_reported_for_value_inputs():
    ledger = _ledger(
        (
            _evidence("E1", EvidenceSourceLayer.ANALYST_UNDERWRITING, confidence=0.4),
            _evidence("E2", EvidenceSourceLayer.ANALYST_UNDERWRITING, confidence=0.8),
        )
    )
    compiled = _compiled((_assumption("a", ("E1", "E2")),))
    report = build_evidence_composition_report(ledger=ledger, compiled=compiled)
    assert report.valuation_mean_confidence == Decimal("0.6")


def test_underwriting_only_model_produces_non_blocking_warnings():
    ledger = _ledger((_evidence("E1", EvidenceSourceLayer.ANALYST_UNDERWRITING),))
    compiled = _compiled((_assumption("a", ("E1",)),))
    report = build_evidence_composition_report(ledger=ledger, compiled=compiled)

    assert not report.passed
    assert all(not item.blocking for item in report.findings)
    checks = {item.check for item in report.warnings}
    assert checks == {
        "evidence_composition_primary_backing",
        "evidence_composition_underwriting_concentration",
    }


def test_filing_backed_model_passes_thresholds():
    ledger = _ledger(
        (
            _evidence("E1", EvidenceSourceLayer.REALIZED_OR_FILING),
            _evidence("E2", EvidenceSourceLayer.ANALYST_UNDERWRITING),
        )
    )
    compiled = _compiled((_assumption("a", ("E1", "E2")),))
    report = build_evidence_composition_report(ledger=ledger, compiled=compiled)
    assert report.passed


def test_report_hash_tracks_composition_changes():
    filing_only = build_evidence_composition_report(
        ledger=_ledger((_evidence("E1", EvidenceSourceLayer.REALIZED_OR_FILING),)),
        compiled=_compiled((_assumption("a", ("E1",)),)),
    )
    underwriting_only = build_evidence_composition_report(
        ledger=_ledger((_evidence("E1", EvidenceSourceLayer.ANALYST_UNDERWRITING),)),
        compiled=_compiled((_assumption("a", ("E1",)),)),
    )
    assert filing_only.report_hash != underwriting_only.report_hash
    assert len(filing_only.report_hash) == 64


def test_unknown_evidence_reference_is_rejected():
    ledger = _ledger((_evidence("E1", EvidenceSourceLayer.REALIZED_OR_FILING),))
    compiled = _compiled((_assumption("a", ("MISSING",)),))
    with pytest.raises(EvidenceCompositionError):
        build_evidence_composition_report(ledger=ledger, compiled=compiled)


def test_policy_bounds_are_validated():
    with pytest.raises(EvidenceCompositionError):
        EvidenceCompositionPolicy(
            min_valuation_primary_backed_share=Decimal("1.5")
        ).validate()


def test_layer_labels_are_localized_and_total_fallback_is_safe():
    assert layer_label_ko(EvidenceSourceLayer.ANALYST_UNDERWRITING) == "분석가 추정"
    assert layer_label_ko("analyst_underwriting") == "분석가 추정"
    assert layer_label_ko("unknown_layer") == "unknown_layer"


# -------------------------------------------------------------------------- adapter


def _context(data: dict) -> OrchestratorContext:
    return OrchestratorContext("RUN", ExecutionMode.LIVE_PRIMARY, data, [], None)


def test_adapter_requires_ledger_and_compiled_set():
    adapter = evidence_composition_audit_adapter()
    assert adapter(_context({})).status is StageStatus.RECOVERY_REQUIRED
    partial = adapter(
        _context({"evidence_ledger": _ledger((_evidence("E1", EvidenceSourceLayer.REALIZED_OR_FILING),))})
    )
    assert partial.status is StageStatus.RECOVERY_REQUIRED


def test_adapter_warns_without_blocking_on_underwriting_only_models():
    ledger = _ledger((_evidence("E1", EvidenceSourceLayer.ANALYST_UNDERWRITING),))
    result = evidence_composition_audit_adapter()(
        _context(
            {
                "evidence_ledger": ledger,
                "compiled_assumption_set": _compiled((_assumption("a", ("E1",)),)),
            }
        )
    )
    assert result.status is StageStatus.WARNING
    assert not result.blocking
    assert set(result.outputs) == {
        "evidence_composition_report",
        "evidence_composition_hash",
        "evidence_composition_summary",
    }


def test_adapter_passes_on_filing_backed_models():
    ledger = _ledger(
        (
            _evidence("E1", EvidenceSourceLayer.REALIZED_OR_FILING),
            _evidence("E2", EvidenceSourceLayer.ANALYST_UNDERWRITING),
        )
    )
    result = evidence_composition_audit_adapter()(
        _context(
            {
                "evidence_ledger": ledger,
                "compiled_assumption_set": _compiled((_assumption("a", ("E1", "E2")),)),
            }
        )
    )
    assert result.status is StageStatus.PASS
    assert not result.blocking
