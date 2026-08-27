from hashlib import sha256

from valuation_engine.live_company_artifact import _audit_hash_preimage
from valuation_engine.records import AuditFinding, AuditReport
from valuation_engine.required_company_live import (
    AcceptanceCompanySpec,
    _underwriting_observation,
)
import pytest


def test_live_artifact_audit_proof_replays_capacity_guardrail_hash():
    capacity_hash = "a" * 64
    report = AuditReport(
        (
            AuditFinding(
                "capacity_double_count",
                True,
                True,
                "capacity guardrail passed",
            ),
        )
    )
    preimage = _audit_hash_preimage(
        run_id="RUN",
        ledger_snapshot_hash="L" * 64,
        assumption_set_hash="A" * 64,
        scenario_set_hash="S" * 64,
        valuation_hash="V" * 64,
        external_guardrail_hashes=(capacity_hash,),
        report=report,
    )

    expected = "\n".join(
        (
            "RUN",
            "L" * 64,
            "A" * 64,
            "S" * 64,
            "V" * 64,
            capacity_hash,
            "capacity_double_count|True|True|capacity guardrail passed",
        )
    )
    assert preimage == expected
    assert sha256(preimage.encode("utf-8")).hexdigest() != sha256(
        preimage.replace(f"\n{capacity_hash}\n", "\n").encode("utf-8")
    ).hexdigest()


def test_acceptance_underwriting_forbids_implicit_placeholder_values():
    spec = AcceptanceCompanySpec(
        company_id="ORACLE",
        payload={
            "legal_name": "Oracle Corporation",
            "ticker": "ORCL",
            "jurisdiction": "US",
            "target_id": "US:SEC:0001341439",
            "official_source_id": "US_SEC_EDGAR",
            "official_source_ref": "https://www.sec.gov/",
            "underwriting_source_ref": "https://example.com/spec",
            "official_document_id": "DOC",
            "as_of": "2026-08-27",
            "segment_id": "core",
            "archetype": "contracted_backlog",
            "method": "normalized_ebitda",
            "currency": "USD",
            "external_ids": {},
            "normalized_ebitda": 1,
            "normalized_multiple": 1,
            "ownership": 1,
            "ev_adjustment": 0,
            "diluted_shares": 1,
        },
        official_document_hash="a" * 64,
        underwriting_document_hash="b" * 64,
        market_price=1,
        market_as_of="2026-08-27",
    )
    with pytest.raises(ValueError, match="implicit placeholder values are forbidden"):
        _underwriting_observation("orders", spec)
