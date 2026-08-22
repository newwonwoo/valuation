from datetime import date

from valuation_engine.source_index import (
    DocumentIndexRecord,
    fact_hash_from_records,
    parse_iea_data_product_metadata,
    parse_kiet_release_listing,
    plan_incremental_index,
    schema_hash_from_records,
    snapshot_hashes_from_json_rows,
)


def test_kiet_listing_parser_extracts_release_date():
    text = "산업경기 전문가 서베이조사결과(`26년 7월 현황과 8월 전망) 2026.07.26"
    rows = parse_kiet_release_listing(text)
    assert len(rows) == 1
    assert rows[0].published_at == date(2026,7,26)


def test_iea_metadata_parser_detects_schema_transition():
    text = """# Monthly Electricity Statistics\nLast updated July 2026\nNext release 17th August 2026\n20/07/2026 Latest\nStarting In March 2026 SDMX is available and the legacy CSV will discontinue after August 2026."""
    meta = parse_iea_data_product_metadata(text)
    assert meta.next_release == date(2026,8,17)
    assert meta.schema_transition_note is not None


def test_json_fact_and_schema_hash_are_separate():
    rows = [{"period":"2026M01","value":1.0}]
    fact, schema = snapshot_hashes_from_json_rows(rows)
    rows2 = [{"period":"2026M01","value":2.0}]
    fact2, schema2 = snapshot_hashes_from_json_rows(rows2)
    assert fact != fact2
    assert schema == schema2


def test_incremental_plan_separates_new_changed_unchanged():
    old = (
        DocumentIndexRecord("s","a","A",date(2026,1,1),"u","x",content_fingerprint="h1"),
        DocumentIndexRecord("s","b","B",date(2026,1,1),"u","x",content_fingerprint="h1"),
    )
    new = (
        DocumentIndexRecord("s","a","A",date(2026,1,1),"u","x",content_fingerprint="h1"),
        DocumentIndexRecord("s","b","B",date(2026,1,1),"u","x",content_fingerprint="h2"),
        DocumentIndexRecord("s","c","C",date(2026,1,1),"u","x",content_fingerprint="h1"),
    )
    plan = plan_incremental_index(old,new)
    assert plan.unchanged_document_ids == ("a",)
    assert plan.changed_document_ids == ("b",)
    assert plan.new_document_ids == ("c",)
