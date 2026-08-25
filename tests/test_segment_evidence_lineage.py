from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.live_primary_adapters import (
    AuthoritativeEvidenceLineage,
    IndustryKnowledgeSnapshot,
    ResolvedCompanyIdentity,
    SegmentDescriptor,
    live_segment_decomposition_adapter,
)
from valuation_engine.orchestrator import run_controlled_workflow


IDENTITY = ResolvedCompanyIdentity(
    target_id="KR:DART:00126380",
    legal_name="삼성전자",
    ticker="005930",
    jurisdiction="KR",
    external_ids=(("opendart_corp_code", "00126380"),),
    source_refs=("https://opendart.fss.or.kr/api/corpCode.xml",),
)


def _segment(evidence_id: str = "E_SEG") -> SegmentDescriptor:
    return SegmentDescriptor(
        segment_id="company",
        name="Company",
        revenue_recognition="shipment",
        price_formation="negotiated",
        asset_ownership="owner",
        capital_intensity="high",
        regulation_intensity="medium",
        customer_structure="diversified",
        reinvestment_model="capex",
        cashflow_duration="multi_year",
        evidence_ids=(evidence_id,),
    )


def _snapshot(
    lineage: AuthoritativeEvidenceLineage | None,
    *,
    evidence_ids: tuple[str, ...] = ("E_SEG",),
    source_ids: tuple[str, ...] = ("KR_OPENDART",),
    content_hashes: tuple[str, ...] = ("HASH-1",),
    as_of: str = "2026-08-25",
) -> IndustryKnowledgeSnapshot:
    return IndustryKnowledgeSnapshot.build(
        as_of=as_of,
        source_ids=source_ids,
        document_ids=("D1",),
        evidence_ids=evidence_ids,
        content_hashes=content_hashes,
        evidence_lineage=(() if lineage is None else (lineage,)),
    )


def _lineage(**overrides) -> AuthoritativeEvidenceLineage:
    values = {
        "evidence_id": "E_SEG",
        "target_id": IDENTITY.target_id,
        "source_id": "KR_OPENDART",
        "observed_date": "2026-08-24",
        "content_hash": "HASH-1",
        "event_date": "2026-06-30",
        "effective_date": "2026-06-30",
        "published_at": "2026-08-24T09:00:00+09:00",
        "first_seen_at": "2026-08-24T09:05:00+09:00",
        "revision_id": "original",
        "revision_at": "2026-08-24T09:00:00+09:00",
        "active": True,
    }
    values.update(overrides)
    return AuthoritativeEvidenceLineage(**values)


def _run(snapshot: IndustryKnowledgeSnapshot, *, evidence_id: str = "E_SEG"):
    return run_controlled_workflow(
        run_id="LINEAGE",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_sequence=("SEGMENT_DECOMPOSITION",),
        adapters={
            "SEGMENT_DECOMPOSITION": live_segment_decomposition_adapter(
                decomposer=lambda *_: (_segment(evidence_id),)
            )
        },
        required_stages=("SEGMENT_DECOMPOSITION",),
        initial_data={
            "resolved_company_identity": IDENTITY,
            "industry_knowledge_snapshot": snapshot,
        },
    )


def test_segment_evidence_requires_authoritative_lineage():
    result = _run(_snapshot(None))
    assert result.blocked_reasons
    assert "lacks authoritative lineage" in result.stage_traces[-1].rationale


def test_segment_evidence_target_must_match_resolved_company():
    result = _run(_snapshot(_lineage(target_id="KR:DART:99999999")))
    assert result.blocked_reasons
    assert "target mismatch" in result.stage_traces[-1].rationale


def test_snapshot_rejects_unknown_hash_and_late_first_seen_backfill():
    declared_other_source = _snapshot(
        _lineage(source_id="KR_KIET_PSI"),
        source_ids=("KR_OPENDART", "KR_KIET_PSI"),
    )
    assert not _run(declared_other_source).blocked_reasons

    try:
        _snapshot(_lineage(content_hash="OTHER"))
    except ValueError as exc:
        assert "content hash" in str(exc)
    else:
        raise AssertionError("unknown content hash must fail snapshot validation")

    late_backfill = _lineage(
        event_date="2026-06-30",
        effective_date="2026-06-30",
        published_at="2026-08-26T09:00:00+09:00",
        first_seen_at="2026-08-26T09:05:00+09:00",
        revision_at="2026-08-26T09:00:00+09:00",
    )
    try:
        _snapshot(late_backfill, as_of="2026-08-25")
    except ValueError as exc:
        assert "first seen after snapshot" in str(exc)
    else:
        raise AssertionError("late-discovered backfill must fail snapshot cutoff")


def test_revision_and_first_seen_chronology_is_validated():
    try:
        _snapshot(
            _lineage(
                revision_id="rev-2",
                revision_at="2026-08-24T10:00:00+09:00",
                first_seen_at="2026-08-24T09:05:00+09:00",
            )
        )
    except ValueError as exc:
        assert "cannot precede revision_at" in str(exc)
    else:
        raise AssertionError("revision after first-seen must fail")


def test_inactive_segment_evidence_blocks_before_industry_dna():
    result = _run(_snapshot(_lineage(active=False)))
    assert result.blocked_reasons
    assert "not active" in result.stage_traces[-1].rationale


def test_verified_lineage_emits_hash_for_downstream_gate():
    result = _run(_snapshot(_lineage()))
    assert result.blocked_reasons == ()
    assert result.stage_traces[-1].status is StageStatus.PASS
    assert result.data["segment_evidence_lineage_hash"]
