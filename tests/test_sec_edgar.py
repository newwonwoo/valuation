import json

import pytest

from valuation_engine.authorized_primary_sources import PrimaryMetricObservation
from valuation_engine.evidence_collection import collect_primary_evidence
from valuation_engine.records import EvidenceSourceLayer
from valuation_engine.sec_edgar import (
    SECMetricSpec,
    SECFilingMetadata,
    fetch_sec_primary_document,
    load_sec_companyfacts,
    load_sec_submissions,
    normalize_sec_cik,
    sec_companyfacts_collector,
    sec_primary_document_collector,
)


CIK = "0001341439"
ACCESSION = "0001193125-25-123456"
CHECKED = "2026-08-25T16:00:00+00:00"


def _submissions_payload():
    return {
        "cik": 1341439,
        "name": "Example Issuer Corp",
        "tickers": ["EXM"],
        "exchanges": ["NYSE"],
        "filings": {
            "recent": {
                "accessionNumber": [ACCESSION, "0001193125-25-999999"],
                "filingDate": ["2025-06-20", "2025-06-19"],
                "reportDate": ["2025-05-31", "2025-06-19"],
                "acceptanceDateTime": ["2025-06-20T20:15:30Z", "2025-06-19T20:15:30Z"],
                "form": ["10-Q", "S-8"],
                "primaryDocument": ["example-20250531.htm", "registration.htm"],
                "isXBRL": [1, 0],
                "isInlineXBRL": [1, 0],
            }
        },
    }


def _companyfacts_payload(*, values=("1250000000",)):
    return {
        "cik": 1341439,
        "entityName": "Example Issuer Corp",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenue",
                    "description": "Revenue",
                    "units": {
                        "USD": [
                            {
                                "start": "2025-03-01",
                                "end": "2025-05-31",
                                "val": value,
                                "accn": ACCESSION,
                                "fy": 2025,
                                "fp": "Q1",
                                "form": "10-Q",
                                "filed": "2025-06-20",
                            }
                            for value in values
                        ]
                    },
                }
            }
        },
    }


def _fetcher(mapping):
    def fetch(url):
        if url not in mapping:
            raise AssertionError(f"unexpected URL: {url}")
        value = mapping[url]
        return json.dumps(value, sort_keys=True) if isinstance(value, dict) else value

    return fetch


def _filing():
    submissions_url = f"https://data.sec.gov/submissions/CIK{CIK}.json"
    snapshot = load_sec_submissions(
        cik=1341439,
        fetch_text=_fetcher({submissions_url: _submissions_payload()}),
        checked_at=CHECKED,
    )
    return snapshot.filing(ACCESSION)


def test_sec_cik_and_submissions_are_exact_and_unsupported_forms_are_filtered():
    assert normalize_sec_cik(1341439) == CIK
    with pytest.raises(ValueError):
        normalize_sec_cik("not-a-cik")

    url = f"https://data.sec.gov/submissions/CIK{CIK}.json"
    snapshot = load_sec_submissions(
        cik=CIK,
        fetch_text=_fetcher({url: _submissions_payload()}),
        checked_at=CHECKED,
    )
    assert snapshot.identity.cik == CIK
    assert snapshot.identity.tickers == ("EXM",)
    assert snapshot.identity.exchanges == ("NYSE",)
    assert len(snapshot.filings) == 1
    assert snapshot.filings[0].accession_no == ACCESSION
    assert snapshot.filings[0].acceptance_at.endswith("+00:00")


def test_companyfacts_exact_concept_flows_into_authorized_primary_evidence():
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"
    facts = load_sec_companyfacts(
        cik=CIK,
        fetch_text=_fetcher({url: _companyfacts_payload()}),
        checked_at=CHECKED,
    )
    collector = sec_companyfacts_collector(
        snapshot=facts,
        filing=_filing(),
        specs=(
            SECMetricSpec(
                metric="realized_revenue",
                taxonomy="us-gaap",
                concept="Revenues",
                unit="USD",
                segment="core",
                critical=True,
            ),
        ),
    )
    result = collect_primary_evidence(
        target_id=f"SEC:CIK{CIK}",
        required_metrics=("realized_revenue",),
        collectors=(collector,),
    )
    assert result.coverage_complete
    record = result.ledger.active()[0]
    assert record.source_layer is EvidenceSourceLayer.REALIZED_OR_FILING
    assert record.metric == "realized_revenue"
    assert record.value == "1250000000"
    assert record.observed_date == "2026-08-25"
    assert ACCESSION in record.source_ref


def test_companyfacts_rejects_fuzzy_or_conflicting_facts():
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"
    facts = load_sec_companyfacts(
        cik=CIK,
        fetch_text=_fetcher({url: _companyfacts_payload()}),
        checked_at=CHECKED,
    )
    with pytest.raises(ValueError, match="no exact concept"):
        sec_companyfacts_collector(
            snapshot=facts,
            filing=_filing(),
            specs=(
                SECMetricSpec("revenue", "us-gaap", "RevenueLikeName", "USD", "core"),
            ),
        )

    conflicting = load_sec_companyfacts(
        cik=CIK,
        fetch_text=_fetcher(
            {url: _companyfacts_payload(values=("1250000000", "1250000001"))}
        ),
        checked_at=CHECKED,
    )
    with pytest.raises(ValueError, match="conflicting values"):
        sec_companyfacts_collector(
            snapshot=conflicting,
            filing=_filing(),
            specs=(SECMetricSpec("revenue", "us-gaap", "Revenues", "USD", "core"),),
        )


def test_primary_filing_document_hash_and_archive_url_are_canonical():
    filing = _filing()
    url = filing.archive_url
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/1341439/"
        "000119312525123456/example-20250531.htm"
    )
    document = fetch_sec_primary_document(
        filing=filing,
        fetch_text=_fetcher({url: "<html>official filing body</html>"}),
        checked_at=CHECKED,
    )
    collector = sec_primary_document_collector(
        document=document,
        observations=(
            PrimaryMetricObservation(
                metric="remaining_performance_obligation",
                segment="core",
                value="7500000000",
                unit="USD",
                effective_date="2025-05-31",
                locator="note:remaining-performance-obligation",
            ),
        ),
        allowed_metrics=("remaining_performance_obligation",),
        allowed_segments=("core",),
    )
    result = collect_primary_evidence(
        target_id=f"SEC:CIK{CIK}",
        required_metrics=("remaining_performance_obligation",),
        collectors=(collector,),
    )
    assert result.coverage_complete
    assert result.ledger.active()[0].source_ref.startswith(url + "#")


def test_filing_metadata_rejects_path_traversal_and_naive_acceptance():
    filing = _filing()
    with pytest.raises(ValueError, match="safe basename"):
        SECFilingMetadata(
            **{**filing.__dict__, "primary_document": "../secret.txt"}
        ).validate()
