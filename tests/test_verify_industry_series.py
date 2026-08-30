"""The operator verifier must produce the snapshot the live collector consumes."""

from __future__ import annotations

import json
import sys

import pytest

from scripts import verify_industry_series
from valuation_engine.industry_series_collector import (
    IndustrySeriesError,
    SERIES_SNAPSHOT_SCHEMA,
)


ROWS = [{"PRD_DE": "202607", "DT": "103.0"}]


def _snapshot(**overrides):
    values = dict(
        series_id="S1",
        source_id="KR_KOSIS_API",
        verification_url="https://kosis.example/catalog/S1",
        rows=ROWS,
        published_at="2026-08-08T00:00:00Z",
        captured_at="2026-08-10T09:00:00+09:00",
        existing=None,
    )
    values.update(overrides)
    return verify_industry_series.build_timestamped_snapshot(**values)


def test_first_capture_materializes_all_required_knowledge_timestamps():
    payload = _snapshot()
    row = payload["observations"][0]
    assert payload["schema_version"] == SERIES_SNAPSHOT_SCHEMA
    assert row == {
        "PRD_DE": "202607",
        "DT": "103.0",
        "PUBLISHED_AT": "2026-08-08T00:00:00+00:00",
        "FIRST_SEEN_AT": "2026-08-10T09:00:00+09:00",
        "REVISION_AT": "2026-08-08T00:00:00+00:00",
    }


def test_unchanged_refresh_preserves_first_seen_and_revision_times():
    first = _snapshot()
    refreshed = _snapshot(
        published_at="2026-09-08T00:00:00Z",
        captured_at="2026-09-10T00:00:00Z",
        existing=first,
    )
    assert refreshed["observations"] == first["observations"]


def test_changed_value_gets_a_new_first_seen_and_revision_cutoff():
    first = _snapshot()
    revised = _snapshot(
        rows=[{"PRD_DE": "202607", "DT": "104.5"}],
        published_at="2026-09-08T00:00:00Z",
        captured_at="2026-09-10T00:00:00Z",
        existing=first,
    )
    assert len(revised["observations"]) == 2
    assert revised["observations"][0] == first["observations"][0]
    row = revised["observations"][1]
    assert row["DT"] == "104.5"
    assert row["PUBLISHED_AT"] == "2026-09-08T00:00:00+00:00"
    assert row["FIRST_SEEN_AT"] == "2026-09-10T00:00:00+00:00"
    assert row["REVISION_AT"] == "2026-09-10T00:00:00+00:00"


def test_capture_cannot_be_backdated_before_publication():
    with pytest.raises(IndustrySeriesError, match="cannot precede"):
        _snapshot(
            published_at="2026-08-11T00:00:00Z",
            captured_at="2026-08-10T00:00:00Z",
        )


def test_cli_writes_snapshot_and_prints_the_snapshot_binding(
    tmp_path, monkeypatch, capsys
):
    output = tmp_path / "S1.json"

    class Response:
        text = json.dumps(ROWS)

    class Transport:
        def __init__(self, *, timeout_seconds):
            assert timeout_seconds == 20.0

        def get_text(self, url):
            assert "REAL_SECRET" in url
            return Response()

    monkeypatch.setenv("TEST_KOSIS_KEY", "REAL_SECRET")
    monkeypatch.setattr(verify_industry_series, "HttpTransport", Transport)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_industry_series.py",
            "--url",
            "https://api.example/data?apiKey=REAL_SECRET&tblId=T1",
            "--metric",
            "benchmark_price",
            "--unit",
            "dimensionless",
            "--series-id",
            "S1",
            "--definition-id",
            "DEF_S1",
            "--api-key-env",
            "TEST_KOSIS_KEY",
            "--published-at",
            "2026-08-08T00:00:00Z",
            "--captured-at",
            "2026-08-10T00:00:00Z",
            "--verification-url",
            "https://api.example/catalog?tblId=T1",
            "--snapshot-out",
            str(output),
        ],
    )

    assert verify_industry_series.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    rendered = capsys.readouterr().out
    assert payload["observations"][0]["FIRST_SEEN_AT"]
    assert f"snapshot_path: {output}" in rendered
    assert "verification_url: https://api.example/catalog?tblId=T1" in rendered
    assert "apiKey={api_key}" in rendered
    assert "REAL_SECRET" not in rendered
