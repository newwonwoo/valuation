import pytest

from valuation_engine.evidence_collection import EvidenceCollectionBatch
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer


def record(layer: EvidenceSourceLayer) -> EvidenceRecord:
    return EvidenceRecord(
        id=f"E:{layer.value}",
        target="TARGET",
        metric="metric",
        value=1,
        unit="count",
        source_layer=layer,
        effective_date="2026-08-25",
        observed_date="2026-08-25",
        source_name="source",
        source_ref="https://example.com/source",
        source_grade="C",
        confidence=0.6,
        segment="core",
    )


def batch(layer: EvidenceSourceLayer) -> EvidenceCollectionBatch:
    return EvidenceCollectionBatch(
        source_id="SOURCE",
        checked_at="2026-08-25",
        records=(record(layer),),
        source_fingerprint="FINGERPRINT",
    )


def test_authorized_market_data_can_enter_a_typed_live_risk_path():
    batch(EvidenceSourceLayer.AUTHORIZED_MARKET_DATA).validate()


def test_analyst_underwriting_can_enter_the_compiler_with_explicit_label():
    batch(EvidenceSourceLayer.ANALYST_UNDERWRITING).validate()


def test_uncontrolled_external_reference_remains_outside_primary_collection():
    with pytest.raises(ValueError, match="non-primary intrinsic layer"):
        batch(EvidenceSourceLayer.EXTERNAL_REFERENCE).validate()
