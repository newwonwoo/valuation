from decimal import Decimal
from hashlib import sha256

import pytest

from valuation_engine.authorized_primary_sources import (
    AuthorizedPrimaryDocument,
    PrimaryEvidenceRecord,
    PrimaryMetricObservation,
    PrimarySourceKind,
    authorized_primary_source_collector,
)
from valuation_engine.evidence_collection import collect_primary_evidence
from valuation_engine.records import EvidenceSourceLayer


def _document(kind, *, target="T", document_id="DOC-1", source_ref="https://example.test/doc"):
    raw = f"{kind.value}:{target}:{document_id}".encode()
    return AuthorizedPrimaryDocument(
        source_id=f"SRC-{kind.value}",
        target_id=target,
        kind=kind,
        document_id=document_id,
        document_hash=sha256(raw).hexdigest(),
        source_ref=source_ref,
        published_at="2026-08-20T09:00:00+09:00",
        checked_at="2026-08-25T10:00:00+09:00",
        access_basis="public",
    )


def _observation(metric, value, *, effective="2026-06-30", segment="core"):
    return PrimaryMetricObservation(
        metric=metric,
        segment=segment,
        value=value,
        unit="KRW",
        effective_date=effective,
        locator=f"note:{metric}",
    )


def test_filing_ir_and_regulator_sources_map_to_distinct_primary_layers():
    collectors = (
        authorized_primary_source_collector(
            document=_document(PrimarySourceKind.REGULATORY_FILING, document_id="FILING"),
            observations=(_observation("realized_revenue", 100),),
            allowed_metrics=("realized_revenue",),
            allowed_segments=("core",),
        ),
        authorized_primary_source_collector(
            document=_document(PrimarySourceKind.COMPANY_IR, document_id="IR"),
            observations=(
                _observation("committed_capacity", 200, effective="2027-12-31"),
            ),
            allowed_metrics=("committed_capacity",),
            allowed_segments=("core",),
        ),
        authorized_primary_source_collector(
            document=_document(PrimarySourceKind.PRIMARY_REGULATOR, document_id="REG"),
            observations=(
                _observation("regulated_tariff", 300, effective="2027-01-01"),
            ),
            allowed_metrics=("regulated_tariff",),
            allowed_segments=("core",),
        ),
    )
    result = collect_primary_evidence(
        target_id="T",
        required_metrics=("realized_revenue", "committed_capacity", "regulated_tariff"),
        collectors=collectors,
    )
    assert result.coverage_complete
    by_metric = {item.metric: item for item in result.ledger.active()}
    assert by_metric["realized_revenue"].source_layer is EvidenceSourceLayer.REALIZED_OR_FILING
    assert by_metric["committed_capacity"].source_layer is EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN
    assert by_metric["regulated_tariff"].source_layer is EvidenceSourceLayer.POLICY_PRIMARY_SOURCE


def test_primary_source_preserves_publication_first_seen_and_revision_identity():
    document = _document(PrimarySourceKind.REGULATORY_FILING)
    result = collect_primary_evidence(
        target_id="T",
        required_metrics=("realized_revenue",),
        collectors=(
            authorized_primary_source_collector(
                document=document,
                observations=(_observation("realized_revenue", 100),),
                allowed_metrics=("realized_revenue",),
                allowed_segments=("core",),
            ),
        ),
    )
    record = result.ledger.active()[0]
    assert isinstance(record, PrimaryEvidenceRecord)
    assert record.published_at == document.published_at
    assert record.first_seen_at == document.checked_at
    assert record.observed_date == "2026-08-25"
    assert record.source_revision == document.document_hash


def test_primary_source_fingerprint_and_evidence_id_are_deterministic():
    document = _document(PrimarySourceKind.REGULATORY_FILING)
    observations = (_observation("realized_revenue", 100),)
    kwargs = dict(
        document=document,
        observations=observations,
        allowed_metrics=("realized_revenue",),
        allowed_segments=("core",),
    )
    first = collect_primary_evidence(
        target_id="T",
        required_metrics=("realized_revenue",),
        collectors=(authorized_primary_source_collector(**kwargs),),
    )
    second = collect_primary_evidence(
        target_id="T",
        required_metrics=("realized_revenue",),
        collectors=(authorized_primary_source_collector(**kwargs),),
    )
    assert first.source_snapshot_hash == second.source_snapshot_hash
    assert first.ledger.active()[0].id == second.ledger.active()[0].id


def test_decimal_value_is_canonicalized_before_ledger_hashing():
    collector = authorized_primary_source_collector(
        document=_document(PrimarySourceKind.REGULATORY_FILING),
        observations=(_observation("realized_revenue", Decimal("123.4500")),),
        allowed_metrics=("realized_revenue",),
        allowed_segments=("core",),
    )
    result = collect_primary_evidence(
        target_id="T",
        required_metrics=("realized_revenue",),
        collectors=(collector,),
    )
    assert result.ledger.active()[0].value == "123.4500"
    assert result.source_snapshot_hash


def test_collector_rejects_undeclared_metric_segment_and_target():
    document = _document(PrimarySourceKind.REGULATORY_FILING)
    with pytest.raises(ValueError, match="declared collector capability"):
        authorized_primary_source_collector(
            document=document,
            observations=(_observation("other_metric", 1),),
            allowed_metrics=("realized_revenue",),
            allowed_segments=("core",),
        )
    with pytest.raises(ValueError, match="declared collector scope"):
        authorized_primary_source_collector(
            document=document,
            observations=(_observation("realized_revenue", 1, segment="other"),),
            allowed_metrics=("realized_revenue",),
            allowed_segments=("core",),
        )

    collector = authorized_primary_source_collector(
        document=document,
        observations=(_observation("realized_revenue", 1),),
        allowed_metrics=("realized_revenue",),
        allowed_segments=("core",),
    )
    with pytest.raises(ValueError, match="target mismatch"):
        collect_primary_evidence(
            target_id="OTHER",
            required_metrics=("realized_revenue",),
            collectors=(collector,),
        )


def test_market_and_street_metric_names_are_rejected_even_with_innocent_source_url():
    document = _document(PrimarySourceKind.COMPANY_IR)
    for metric in (
        "market_price",
        "current_market_price",
        "target_price",
        "consensus_target",
        "consensus_eps",
        "target_market_cap",
        "target_multiple",
        "street_consensus_eps",
    ):
        with pytest.raises(ValueError, match="market/Street metric"):
            authorized_primary_source_collector(
                document=document,
                observations=(_observation(metric, 100),),
                allowed_metrics=(metric,),
                allowed_segments=("core",),
            )


def test_filing_cannot_claim_future_realized_metric_but_ir_and_policy_can():
    with pytest.raises(ValueError, match="effective date after publication"):
        authorized_primary_source_collector(
            document=_document(PrimarySourceKind.REGULATORY_FILING),
            observations=(_observation("revenue", 1, effective="2027-01-01"),),
            allowed_metrics=("revenue",),
            allowed_segments=("core",),
        )

    for kind in (PrimarySourceKind.COMPANY_IR, PrimarySourceKind.PRIMARY_REGULATOR):
        authorized_primary_source_collector(
            document=_document(kind),
            observations=(_observation("future_metric", 1, effective="2027-01-01"),),
            allowed_metrics=("future_metric",),
            allowed_segments=("core",),
        )


def test_source_contract_requires_exact_hash_authorized_access_and_no_target_market_ref():
    document = _document(PrimarySourceKind.REGULATORY_FILING)
    with pytest.raises(ValueError, match="SHA-256"):
        AuthorizedPrimaryDocument(
            **{**document.__dict__, "document_hash": "short"}
        ).validate()
    with pytest.raises(ValueError, match="access_basis"):
        AuthorizedPrimaryDocument(
            **{**document.__dict__, "access_basis": "scraped_login"}
        ).validate()
    with pytest.raises(ValueError, match="target-market"):
        _document(
            PrimarySourceKind.COMPANY_IR,
            source_ref="https://example.test/target_price",
        ).validate()
