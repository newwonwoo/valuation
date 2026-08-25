from hashlib import sha256

import pytest

from valuation_engine.authorized_primary_sources import (
    AuthorizedPrimaryDocument,
    PrimarySourceKind,
    authorized_primary_source_collector,
)
from valuation_engine.canonical_company_kpis import (
    ExactDocumentMetricCandidate,
    compile_exact_document_observation,
)
from valuation_engine.evidence_collection import collect_primary_evidence
from valuation_engine.records import EvidenceSourceLayer


def test_canonical_sec_document_candidate_rejects_other_issuer_cik():
    with pytest.raises(ValueError, match="SEC source CIK must match registered issuer"):
        compile_exact_document_observation(
            ExactDocumentMetricCandidate(
                company_id="ORACLE",
                metric="remaining_performance_obligations",
                source_ref="https://www.sec.gov/Archives/edgar/data/1996810/0000000000/oracle.htm",
                locator="Remaining Performance Obligations from Contracts with Customers",
                value=100,
                unit="USD",
                effective_date="2026-05-31",
            )
        )


def test_company_plan_role_survives_sec_filing_without_rewriting_source_provenance():
    source_ref = "https://www.sec.gov/Archives/edgar/data/1996810/000199681026000001/gev.htm"
    observation = compile_exact_document_observation(
        ExactDocumentMetricCandidate(
            company_id="GE_VERNOVA",
            metric="gas_power_equipment_backlog_and_slot_reservations",
            source_ref=source_ref,
            locator="Gas Power equipment backlog and slot reservation agreements",
            value=80,
            unit="GW",
            effective_date="2027-12-31",
        )
    )
    assert observation.evidence_role == "company_plan"
    assert observation.source_ref == source_ref
    assert observation.target_id == "SEC:CIK0001996810"

    document = AuthorizedPrimaryDocument(
        source_id="SEC_EDGAR_PRIMARY_DOCUMENT",
        target_id="SEC:CIK0001996810",
        kind=PrimarySourceKind.REGULATORY_FILING,
        document_id="0001996810-26-000001",
        document_hash=sha256(b"gev filing").hexdigest(),
        source_ref=source_ref,
        published_at="2026-08-20T09:00:00+00:00",
        checked_at="2026-08-25T09:00:00+00:00",
        access_basis="public",
    )
    collector = authorized_primary_source_collector(
        document=document,
        observations=(observation,),
        allowed_metrics=("gas_power_equipment_backlog_and_slot_reservations",),
        allowed_segments=("power",),
    )
    result = collect_primary_evidence(
        target_id="SEC:CIK0001996810",
        required_metrics=("gas_power_equipment_backlog_and_slot_reservations",),
        collectors=(collector,),
    )
    record = result.ledger.active()[0]
    assert record.evidence_role == "company_plan"
    assert record.source_layer is EvidenceSourceLayer.REALIZED_OR_FILING
    assert source_ref in record.source_ref


def test_observation_target_binding_must_match_authorized_document():
    source_ref = "https://www.sec.gov/Archives/edgar/data/1341439/000134143926000001/orcl.htm"
    observation = compile_exact_document_observation(
        ExactDocumentMetricCandidate(
            company_id="ORACLE",
            metric="cloud_infrastructure_revenue",
            source_ref=source_ref,
            locator="Cloud infrastructure",
            value=100,
            unit="USD",
            effective_date="2026-05-31",
        )
    )
    wrong_target_document = AuthorizedPrimaryDocument(
        source_id="SEC_EDGAR_PRIMARY_DOCUMENT",
        target_id="SEC:CIK0001996810",
        kind=PrimarySourceKind.REGULATORY_FILING,
        document_id="0001341439-26-000001",
        document_hash=sha256(b"oracle filing under wrong target").hexdigest(),
        source_ref=source_ref,
        published_at="2026-08-20T09:00:00+00:00",
        checked_at="2026-08-25T09:00:00+00:00",
        access_basis="public",
    )
    with pytest.raises(ValueError, match="target_id must match authorized document target"):
        authorized_primary_source_collector(
            document=wrong_target_document,
            observations=(observation,),
            allowed_metrics=("cloud_infrastructure_revenue",),
            allowed_segments=("cloud_and_software",),
        )


def test_observation_source_binding_must_match_authorized_document():
    observation = compile_exact_document_observation(
        ExactDocumentMetricCandidate(
            company_id="ORACLE",
            metric="cloud_infrastructure_revenue",
            source_ref="https://www.sec.gov/Archives/edgar/data/1341439/000134143926000001/orcl.htm",
            locator="Cloud infrastructure",
            value=100,
            unit="USD",
            effective_date="2026-05-31",
        )
    )
    wrong_document = AuthorizedPrimaryDocument(
        source_id="SEC_EDGAR_PRIMARY_DOCUMENT",
        target_id="SEC:CIK0001341439",
        kind=PrimarySourceKind.REGULATORY_FILING,
        document_id="0001341439-26-000002",
        document_hash=sha256(b"different oracle filing").hexdigest(),
        source_ref="https://www.sec.gov/Archives/edgar/data/1341439/000134143926000002/orcl2.htm",
        published_at="2026-08-20T09:00:00+00:00",
        checked_at="2026-08-25T09:00:00+00:00",
        access_basis="public",
    )
    with pytest.raises(ValueError, match="source_ref must match authorized document"):
        authorized_primary_source_collector(
            document=wrong_document,
            observations=(observation,),
            allowed_metrics=("cloud_infrastructure_revenue",),
            allowed_segments=("cloud_and_software",),
        )
