from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.evidence_adapter import evidence_ledger_adapter, primary_evidence_collection_adapter
from valuation_engine.evidence_collection import collect_primary_evidence, static_evidence_collector
from valuation_engine.orchestrator import run_controlled_workflow
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer


def evidence(
    evidence_id: str,
    metric: str,
    value,
    unit: str,
    *,
    target: str = "T",
    layer: EvidenceSourceLayer = EvidenceSourceLayer.REALIZED_OR_FILING,
    effective_date: str = "2026-06-30",
    observed_date: str = "2026-07-01",
) -> EvidenceRecord:
    return EvidenceRecord(
        id=evidence_id,
        target=target,
        metric=metric,
        value=value,
        unit=unit,
        source_layer=layer,
        effective_date=effective_date,
        observed_date=observed_date,
        source_name="filing",
        source_ref=f"source#{evidence_id}",
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def collector(source_id: str, *records: EvidenceRecord):
    return static_evidence_collector(
        source_id=source_id,
        checked_at="2026-08-23",
        records=tuple(records),
        source_fingerprint=f"FP:{source_id}",
        document_ids=(f"DOC:{source_id}",),
    )


def test_collection_builds_ledger_and_complete_metric_coverage():
    result = collect_primary_evidence(
        target_id="T",
        required_metrics=("revenue", "margin"),
        collectors=(
            collector("DART", evidence("E1", "revenue", 100, "KRW_billion")),
            collector("IR", evidence("E2", "margin", 0.2, "ratio", layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN)),
        ),
    )
    assert result.coverage_complete
    assert result.covered_metrics == ("revenue", "margin")
    assert result.missing_metrics == ()
    assert {item.id for item in result.ledger.active()} == {"E1", "E2"}
    assert result.source_snapshot_hash


def test_source_snapshot_hash_is_order_independent_across_collectors():
    a = collector("A", evidence("E1", "revenue", 100, "KRW_billion"))
    b = collector("B", evidence("E2", "margin", 0.2, "ratio"))
    one = collect_primary_evidence(target_id="T", required_metrics=("revenue",), collectors=(a, b))
    two = collect_primary_evidence(target_id="T", required_metrics=("revenue",), collectors=(b, a))
    assert one.source_snapshot_hash == two.source_snapshot_hash


def test_non_primary_market_layer_is_rejected():
    bad = collector(
        "MARKET",
        evidence("E1", "price", 10000, "KRW", layer=EvidenceSourceLayer.MARKET_COMPARISON),
    )
    try:
        collect_primary_evidence(target_id="T", required_metrics=("price",), collectors=(bad,))
    except ValueError as exc:
        assert "non-primary intrinsic layer" in str(exc)
    else:
        raise AssertionError("market comparison evidence must be rejected")


def test_target_mismatch_is_rejected():
    bad = collector("DART", evidence("E1", "revenue", 100, "KRW_billion", target="OTHER"))
    try:
        collect_primary_evidence(target_id="T", required_metrics=("revenue",), collectors=(bad,))
    except ValueError as exc:
        assert "target mismatch" in str(exc)
    else:
        raise AssertionError("target mismatch must fail closed")


def test_record_observed_after_collection_checkpoint_is_rejected():
    future_known = static_evidence_collector(
        source_id="DART",
        checked_at="2026-08-23T12:00:00+09:00",
        records=(
            evidence(
                "E-FUTURE",
                "revenue",
                100,
                "KRW_billion",
                observed_date="2026-08-24",
            ),
        ),
        source_fingerprint="FP:DART",
    )
    try:
        collect_primary_evidence(
            target_id="T",
            required_metrics=("revenue",),
            collectors=(future_known,),
        )
    except ValueError as exc:
        assert "observed after source batch checked_at" in str(exc)
    else:
        raise AssertionError("future-observed evidence must fail closed")


def test_future_effective_plan_is_allowed_when_already_observed():
    known_plan = static_evidence_collector(
        source_id="IR",
        checked_at="2026-08-23",
        records=(
            evidence(
                "E-PLAN",
                "capacity",
                200,
                "MW",
                layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN,
                effective_date="2027-12-31",
                observed_date="2026-08-20",
            ),
        ),
        source_fingerprint="FP:IR",
    )
    result = collect_primary_evidence(
        target_id="T",
        required_metrics=("capacity",),
        collectors=(known_plan,),
    )
    assert result.coverage_complete
    assert result.ledger.active()[0].effective_date == "2027-12-31"


def test_observed_timestamp_before_checkpoint_remains_supported():
    timestamped = static_evidence_collector(
        source_id="DART",
        checked_at="2026-08-23T12:00:00+09:00",
        records=(
            evidence(
                "E-TIMESTAMP",
                "revenue",
                100,
                "KRW_billion",
                observed_date="2026-08-22T23:59:59+09:00",
            ),
        ),
        source_fingerprint="FP:DART",
    )
    result = collect_primary_evidence(
        target_id="T",
        required_metrics=("revenue",),
        collectors=(timestamped,),
    )
    assert result.coverage_complete
    assert result.ledger.active()[0].observed_date.startswith("2026-08-22")


def test_cross_offset_earlier_instant_is_not_rejected_by_local_date():
    timestamped = static_evidence_collector(
        source_id="DART",
        checked_at="2026-08-23T23:00:00+00:00",
        records=(
            evidence(
                "E-OFFSET-EARLY",
                "revenue",
                100,
                "KRW_billion",
                observed_date="2026-08-24T00:30:00+09:00",
            ),
        ),
        source_fingerprint="FP:DART",
    )
    result = collect_primary_evidence(
        target_id="T",
        required_metrics=("revenue",),
        collectors=(timestamped,),
    )
    assert result.coverage_complete


def test_cross_offset_later_instant_is_rejected_even_with_earlier_local_date():
    future_known = static_evidence_collector(
        source_id="DART",
        checked_at="2026-08-24T00:00:00+09:00",
        records=(
            evidence(
                "E-OFFSET-LATE",
                "revenue",
                100,
                "KRW_billion",
                observed_date="2026-08-23T23:30:00-10:00",
            ),
        ),
        source_fingerprint="FP:DART",
    )
    try:
        collect_primary_evidence(
            target_id="T",
            required_metrics=("revenue",),
            collectors=(future_known,),
        )
    except ValueError as exc:
        assert "observed after source batch checked_at" in str(exc)
    else:
        raise AssertionError("later cross-offset observation must fail closed")


def test_timezone_naive_observed_timestamp_is_rejected():
    naive = static_evidence_collector(
        source_id="DART",
        checked_at="2026-08-23T00:00:00+00:00",
        records=(
            evidence(
                "E-NAIVE",
                "revenue",
                100,
                "KRW_billion",
                observed_date="2026-08-23T23:59:59",
            ),
        ),
        source_fingerprint="FP:DART",
    )
    try:
        collect_primary_evidence(
            target_id="T",
            required_metrics=("revenue",),
            collectors=(naive,),
        )
    except ValueError as exc:
        assert "observed_date timestamp must be timezone-aware" in str(exc)
    else:
        raise AssertionError("timezone-naive observed timestamp must fail closed")


def test_control_plane_collection_and_ledger_stages_pass():
    result = run_controlled_workflow(
        run_id="COLLECT",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("PRIMARY_EVIDENCE_COLLECTION", "EVIDENCE_LEDGER"),
        adapters={
            "PRIMARY_EVIDENCE_COLLECTION": primary_evidence_collection_adapter(
                collectors=(collector("DART", evidence("E1", "revenue", 100, "KRW_billion")),),
            ),
            "EVIDENCE_LEDGER": evidence_ledger_adapter(),
        },
        required_stages=("PRIMARY_EVIDENCE_COLLECTION", "EVIDENCE_LEDGER"),
        initial_data={"target_id": "T", "required_evidence": ("revenue",)},
    )
    assert result.blocked_reasons == ()
    assert [item.status for item in result.stage_traces] == [StageStatus.PASS, StageStatus.PASS]
    assert result.data["source_snapshot_hash"]
    assert result.data["ledger_snapshot_hash"]


def test_missing_required_metric_enters_recovery_not_silent_pass():
    result = run_controlled_workflow(
        run_id="COLLECT-MISSING",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("PRIMARY_EVIDENCE_COLLECTION",),
        adapters={
            "PRIMARY_EVIDENCE_COLLECTION": primary_evidence_collection_adapter(
                collectors=(collector("DART", evidence("E1", "revenue", 100, "KRW_billion")),),
            ),
        },
        required_stages=("PRIMARY_EVIDENCE_COLLECTION",),
        initial_data={"target_id": "T", "required_evidence": ("revenue", "margin")},
    )
    assert result.blocked_reasons
    assert result.stage_traces[0].status is StageStatus.RECOVERY_REQUIRED
    assert result.data["evidence_missing_metrics"] == ("margin",)
