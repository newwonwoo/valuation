from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.evidence_adapter import evidence_ledger_adapter
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer
from valuation_engine.run_hash import evidence_ledger_snapshot_hash


def evidence(evidence_id: str, *, value=100) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        target="T",
        metric="revenue",
        value=value,
        unit="KRW",
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-06-30",
        observed_date="2026-08-01",
        source_name="filing",
        source_ref=f"filing://{evidence_id}",
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def test_audit_hash_reuse_does_not_serialize_frozen_ledger_twice(monkeypatch):
    ledger = EvidenceLedger((evidence("E1"),))
    original = EvidenceLedger.to_list
    calls = 0

    def counted(self):
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(EvidenceLedger, "to_list", counted)
    context = OrchestratorContext(
        "RUN",
        ExecutionMode.PRIMARY_SHADOW,
        {"evidence_ledger": ledger},
    )
    result = evidence_ledger_adapter()(context)
    assert result.status is StageStatus.PASS
    assert calls == 1

    frozen_hash = result.outputs["ledger_snapshot_hash"]
    assert ledger.runtime_snapshot is not None
    assert evidence_ledger_snapshot_hash(ledger) == frozen_hash
    assert calls == 1


def test_append_after_snapshot_invalidates_hash_without_reserializing(monkeypatch):
    ledger = EvidenceLedger((evidence("E1"),))
    original = EvidenceLedger.to_list
    calls = 0

    def counted(self):
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(EvidenceLedger, "to_list", counted)
    context = OrchestratorContext(
        "RUN",
        ExecutionMode.PRIMARY_SHADOW,
        {"evidence_ledger": ledger},
    )
    result = evidence_ledger_adapter()(context)
    frozen_hash = result.outputs["ledger_snapshot_hash"]
    ledger.append(evidence("E2", value=200))

    replay = evidence_ledger_snapshot_hash(ledger)
    assert replay != frozen_hash
    assert replay.startswith("MUTATED_LEDGER:")
    assert calls == 1


def test_same_count_private_record_replacement_still_invalidates_cached_snapshot(monkeypatch):
    ledger = EvidenceLedger((evidence("E1"),))
    original = EvidenceLedger.to_list
    calls = 0

    def counted(self):
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(EvidenceLedger, "to_list", counted)
    context = OrchestratorContext(
        "RUN",
        ExecutionMode.PRIMARY_SHADOW,
        {"evidence_ledger": ledger},
    )
    result = evidence_ledger_adapter()(context)
    frozen_hash = result.outputs["ledger_snapshot_hash"]

    ledger._records["E1"] = evidence("E1", value=999)  # deliberate tamper regression
    replay = evidence_ledger_snapshot_hash(ledger)
    assert replay != frozen_hash
    assert replay.startswith("MUTATED_LEDGER:")
    assert calls == 1
