from hashlib import sha256

from valuation_engine.live_company_artifact import _audit_hash_preimage
from valuation_engine.records import AuditFinding, AuditReport
from valuation_engine.required_company_live import (
    AcceptanceCompanySpec,
    _underwriting_observation,
    load_acceptance_specs,
    validate_official_document_evidence,
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


def _official_payload():
    return {
        "official_document_identity": ["Example Corporation", "period ended June 30, 2026"],
        "official_metrics": {
            "revenue": ["1065.365", "USD_million"],
            "backlog": ["NOT_DISCLOSED", "status"],
        },
        "official_metric_locators": {
            "revenue": {
                "label": "Total revenue",
                "source_text": "1,065,365",
                "source_value": "1065365",
                "source_multiplier": "0.001",
                "unit": "USD_million",
            }
        },
    }


def _official_document():
    return b"""<html><body><h1>Example Corporation</h1>
    <p>For the period ended June 30, 2026</p>
    <table><tr><th>Total revenue</th><td>1,065,365</td></tr></table>
    </body></html>"""


def test_official_fixture_metrics_must_be_locally_bound_to_source_content():
    validate_official_document_evidence(
        "EXAMPLE",
        _official_payload(),
        _official_document(),
    )

    unrelated = _official_document().replace(b"Example Corporation", b"Unrelated Company")
    with pytest.raises(ValueError, match="identity anchor is missing"):
        validate_official_document_evidence("EXAMPLE", _official_payload(), unrelated)


def test_official_fixture_rejects_unlocated_or_mismatched_numeric_claims():
    payload = _official_payload()
    payload["official_metric_locators"] = {}
    with pytest.raises(ValueError, match="requires a source locator"):
        validate_official_document_evidence("EXAMPLE", payload, _official_document())

    payload = _official_payload()
    payload["official_metrics"]["revenue"] = ["999", "USD_million"]
    with pytest.raises(ValueError, match="source locator value mismatch"):
        validate_official_document_evidence("EXAMPLE", payload, _official_document())


def test_official_fixture_rejects_distant_label_value_coincidence():
    distant = (
        b"<html><body>Example Corporation period ended June 30, 2026 "
        b"Total revenue "
        + b"x" * 600
        + b" 1,065,365</body></html>"
    )
    with pytest.raises(ValueError, match="not locally bound"):
        validate_official_document_evidence("EXAMPLE", _official_payload(), distant)


def test_current_official_numeric_claims_all_have_consistent_locators():
    for company_id, payload in load_acceptance_specs().items():
        identity = " ".join(payload["official_document_identity"])
        locator_text = " ".join(
            f"{locator['label']} {locator['source_text']}"
            for locator in payload.get("official_metric_locators", {}).values()
        )
        document = f"<html><body>{identity} {locator_text}</body></html>".encode()
        validate_official_document_evidence(company_id, payload, document)
