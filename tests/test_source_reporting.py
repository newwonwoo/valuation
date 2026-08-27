import pytest

from valuation_engine.ledger import EvidenceLedger
from valuation_engine.records import (
    EvidenceRecord,
    EvidenceSourceLayer,
    MarketObservation,
)
from valuation_engine.source_reporting import (
    build_source_link_index,
    canonical_verification_url,
    linked_evidence_ids,
    render_source_link_section,
)


def evidence(source_ref: str) -> EvidenceRecord:
    return EvidenceRecord(
        id="E-SOURCE",
        target="TARGET",
        metric="revenue",
        value=100,
        unit="KRW",
        source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
        effective_date="2026-06-30",
        observed_date="2026-08-01",
        source_name="Official filing",
        source_ref=source_ref,
        source_grade="A",
        confidence=1.0,
        segment="core",
    )


def test_direct_source_index_groups_claim_coverage_and_renders_clickable_link():
    url = "https://example.com/filing#revenue"
    data = {
        "evidence_ledger": EvidenceLedger((evidence(url),)),
        "market_observation": MarketObservation(
            100,
            "2026-08-01",
            "https://example.com/market",
        ),
    }

    links = build_source_link_index(data, require_all_http=True)
    rendered = "\n".join(render_source_link_section(links))

    assert links[0].url == "https://example.com/filing"
    assert any("근거 E-SOURCE: revenue" in item for item in links[0].coverage)
    assert "[원문 바로 열기](https://example.com/filing)" in rendered
    assert "근거 E-SOURCE" in rendered
    assert linked_evidence_ids(data, ("E-SOURCE",)) == (
        "[E-SOURCE](https://example.com/filing)"
    )


@pytest.mark.parametrize(
    "source_ref",
    (
        "fixture://filing/revenue",
        "https://example.com/data?api_key=SECRET",
        "https://example.com/data?X-Amz-Credential=ACCESS/20260827/region/s3/aws4_request",
        "https://example.com/data?X-Amz-Signature=SECRET",
        "https://example.com/data?sig=SECRET",
        "https://example.com/data?signature=SECRET",
        "https://user:password@example.com/data",
    ),
)
def test_live_source_contract_rejects_non_verifiable_or_sensitive_links(source_ref):
    with pytest.raises(ValueError, match="direct source-link contract failed"):
        build_source_link_index(
            {"evidence_ledger": EvidenceLedger((evidence(source_ref),))},
            require_all_http=True,
        )
    assert canonical_verification_url(source_ref) is None


def test_live_source_contract_requires_active_evidence():
    with pytest.raises(ValueError, match="NO_ACTIVE_EVIDENCE"):
        build_source_link_index(
            {"evidence_ledger": EvidenceLedger()},
            require_all_http=True,
        )


def test_compact_source_section_retains_every_grouped_evidence_id():
    records = tuple(
        EvidenceRecord(
            id=f"E-{index}",
            target="TARGET",
            metric=f"metric-{index}",
            value=index,
            unit="KRW",
            source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
            effective_date="2026-06-30",
            observed_date="2026-08-01",
            source_name="Official filing",
            source_ref="https://example.com/grouped",
            source_grade="A",
            confidence=1.0,
            segment="core",
        )
        for index in range(8)
    )
    links = build_source_link_index(
        {"evidence_ledger": EvidenceLedger(records)},
        require_all_http=True,
    )

    rendered = "\n".join(render_source_link_section(links))

    assert "이 원문에 연결된 근거 8개 보기" in rendered
    assert all(f"`E-{index}`" in rendered for index in range(8))
