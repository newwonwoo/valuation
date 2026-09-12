from pathlib import Path

import pytest

from valuation_engine.declared_broker_research import (
    DeclaredBrokerResearchError,
    declared_broker_research_loader,
)
from valuation_engine.declared_segment_evidence import (
    declared_segment_evidence_provider,
)
from valuation_engine.declared_segments import load_declared_segments
from valuation_engine.evidence_collection import EvidenceCollectionRequest
from valuation_engine.records import EvidenceSourceLayer


ROOT = Path(__file__).resolve().parents[1]
KOREAZINC_DECLARATIONS = ROOT / "runs" / "koreazinc-010130" / "declarations"


def test_koreazinc_broker_discovery_is_replayable_and_prefreeze_safe():
    loader = declared_broker_research_loader(
        KOREAZINC_DECLARATIONS / "broker_research.yaml",
        run_as_of="2026-09-04",
    )

    batch = loader(None)

    assert len(batch.observations) == 2
    assert all(item.claim.target_company_specific for item in batch.observations)
    assert all(item.verification_metrics for item in batch.observations)


def test_declared_broker_discovery_rejects_locked_street_fields(tmp_path):
    path = tmp_path / "broker_research.yaml"
    path.write_text(
        """checked_at: '2026-09-04'
source_refs: [https://example.com/report]
observations:
  - claim_id: B:LOCKED
    source_id: BROKER
    broker_family: Broker
    report_type: company_update
    field_class: target_price
    industry_node: test
    statement: locked target field
    target_company_specific: true
    report_date: '2026-09-01'
    segment_id: core
    source_ref: https://example.com/report
    verification_metrics: [revenue]
""",
        encoding="utf-8",
    )

    with pytest.raises(DeclaredBrokerResearchError, match="target.*must not be loaded"):
        declared_broker_research_loader(path, run_as_of="2026-09-04")


def test_llm_reviewed_segment_cells_enter_ledger_as_company_primary():
    declaration = load_declared_segments(
        KOREAZINC_DECLARATIONS / "segments.yaml"
    )
    provider = declared_segment_evidence_provider(
        declaration,
        effective_date="2026-06-30",
        checked_at="2026-09-04",
    )
    batch = provider.collector(
        EvidenceCollectionRequest(
            declaration.target_id,
            (
                "recycling_reported_segment_revenue",
                "recycling_reported_segment_operating_income",
            ),
        )
    )

    assert {item.segment for item in batch.records} == {"recycling"}
    assert {item.value for item in batch.records} == {413925299, -13599032}
    assert all(
        item.source_layer is EvidenceSourceLayer.REALIZED_OR_FILING
        for item in batch.records
    )
