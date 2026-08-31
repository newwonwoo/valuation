from __future__ import annotations

import pytest

from valuation_engine.evidence_collection import EvidenceCollectionRequest
from valuation_engine.generic_underwriting import (
    DeclaredUnderwritingError,
    declared_underwriting_collector,
    load_declared_underwriting,
)
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.source_reporting import build_source_link_index


def _write_underwriting(tmp_path, source_block: str):
    path = tmp_path / "underwriting.yaml"
    path.write_text(
        "target_id: KR:DART:TEST\n"
        'as_of: "2026-08-27"\n'
        "source_ref: https://example.test/default-filing\n"
        "declarations:\n"
        "  normalized_ebitda:\n"
        "    value: 100\n"
        "    unit: KRW_billion\n"
        "    rationale: normalized from the annual and interim filing history.\n"
        f"{source_block}",
        encoding="utf-8",
    )
    return path


def test_declaration_retains_multiple_original_source_links(tmp_path):
    path = _write_underwriting(
        tmp_path,
        "    source_refs:\n"
        "      - https://example.test/annual-filing\n"
        "      - https://example.test/interim-filing\n",
    )
    payload = load_declared_underwriting(path)
    assert payload["declarations"]["normalized_ebitda"][0]["source_refs"] == (
        "https://example.test/annual-filing",
        "https://example.test/interim-filing",
    )

    batch = declared_underwriting_collector(path)(
        EvidenceCollectionRequest(
            target_id="KR:DART:TEST", required_metrics=("normalized_ebitda",)
        )
    )
    record = batch.records[0]
    assert record.source_ref == "https://example.test/annual-filing"
    assert record.source_refs == (
        "https://example.test/annual-filing",
        "https://example.test/interim-filing",
    )
    links = build_source_link_index(
        {"evidence_ledger": EvidenceLedger(batch.records)}, require_all_http=True
    )
    assert {link.url for link in links} == {
        "https://example.test/annual-filing",
        "https://example.test/interim-filing",
    }
    assert all("UW:KR:DART:TEST:normalized_ebitda" in link.coverage[0] for link in links)


def test_declaration_rejects_non_http_additional_source(tmp_path):
    path = _write_underwriting(
        tmp_path,
        "    source_refs:\n"
        "      - https://example.test/annual-filing\n"
        "      - fixture://interim\n",
    )
    with pytest.raises(DeclaredUnderwritingError, match="credential-free HTTP"):
        load_declared_underwriting(path)
