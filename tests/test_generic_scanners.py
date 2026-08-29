"""Generic scanner runners screen the run's own ledger, honestly."""

from __future__ import annotations

import yaml

from valuation_engine.generic_scanners import (
    generic_scanner_runners,
    ledger_screen_scanner_runner,
    load_scanner_screens,
    ScannerScreen,
)
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer
from valuation_engine.scanner_runtime import ScannerContext, ScannerFindingStatus


def _record(evidence_id: str, metric: str) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id, target="KR:DART:00999901", metric=metric, value=1.0,
        unit="dimensionless", source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-08-27", observed_date="2026-08-27",
        source_name="dart", source_ref="https://dart.fss.or.kr/x",
        source_grade="A", confidence=0.9, segment="core",
    )


def _context(ledger: EvidenceLedger, scanner_id: str) -> ScannerContext:
    return ScannerContext(
        scanner_id=scanner_id, company="한빛중전기", ticker="900990",
        target_id="KR:DART:00999901", ledger=ledger, module_requirement_plan=None,
    )


def test_every_declared_scanner_has_a_runner():
    runners = generic_scanner_runners()
    declared: set[str] = set()
    reqs = yaml.safe_load(
        open("config/archetype_control_requirements.yaml", encoding="utf-8")
    )["requirements"]
    for row in reqs.values():
        declared.update(row.get("mandatory_scanners") or [])
        declared.update(row.get("optional_scanners") or [])
    assert declared.issubset(set(runners))


def test_matching_evidence_is_cited_and_connected():
    ledger = EvidenceLedger((_record("E1", "order_backlog"), _record("E2", "revenue")))
    runner = ledger_screen_scanner_runner(
        ScannerScreen("BACKLOG_QUALITY", ("backlog", "order"))
    )
    finding = runner(_context(ledger, "BACKLOG_QUALITY"))
    assert finding.status is ScannerFindingStatus.PASS
    assert finding.evidence_ids == ("E1",)
    assert finding.hypothesis_candidates
    finding.validate(ledger)
    finding.impact_trace().validate()


def test_no_matching_evidence_is_an_explicit_warning_not_a_silent_pass():
    ledger = EvidenceLedger((_record("E1", "revenue"),))
    runner = ledger_screen_scanner_runner(
        ScannerScreen("CANCELLATION_TERMS", ("cancellation",))
    )
    finding = runner(_context(ledger, "CANCELLATION_TERMS"))
    assert finding.status is ScannerFindingStatus.WARNING
    assert not finding.evidence_ids
    assert finding.verification_requests
    finding.validate(ledger)


def test_screen_config_rows_are_validated():
    screens = load_scanner_screens()
    assert all(screen.metric_keywords for screen in screens)
