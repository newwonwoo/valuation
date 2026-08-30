"""Industry series become Evidence only through the definition gate."""

from __future__ import annotations

import json

import pytest

from valuation_engine.evidence_collection import EvidenceCollectionRequest
from valuation_engine.industry_series_collector import (
    SERIES_SNAPSHOT_SCHEMA,
    IndustrySeriesError,
    IndustrySeriesSpec,
    credential_free_verification_url,
    industry_series_collector_providers,
    load_industry_series_registry,
    request_scoped_industry_series_collector,
)
from valuation_engine.records import EvidenceSourceLayer
from valuation_engine.source_reporting import canonical_verification_url


AS_OF = "2026-08-27"
TARGET = "KR:DART:00999902"


def _spec(**overrides) -> IndustrySeriesSpec:
    defaults = dict(
        series_id="S1",
        source_id="KR_KOSIS_API",
        metric="benchmark_price",
        layer="authorized_market_data",
        unit="dimensionless",
        geography="KR",
        definition_id="DEF_S1",
        definition="Synthetic producer-price benchmark index for tests.",
        url_template="https://probe.invalid/kosis/S1.json",
        api_key_env="",
        snapshot_path="/frozen/S1.json",
        verification_url="https://probe.invalid/catalog/S1",
        verified=True,
    )
    defaults.update(overrides)
    return IndustrySeriesSpec(**defaults)


ROWS = [
    {
        "PRD_DE": "202606",
        "DT": "101.5",
        "PUBLISHED_AT": "2026-07-08T00:00:00Z",
        "FIRST_SEEN_AT": "2026-07-10T09:00:00+09:00",
        "REVISION_AT": "2026-07-08T00:00:00Z",
    },
    {
        "PRD_DE": "202607",
        "DT": "103.0",
        "PUBLISHED_AT": "2026-08-08T00:00:00Z",
        "FIRST_SEEN_AT": "2026-08-10T09:00:00+09:00",
        "REVISION_AT": "2026-08-08T00:00:00Z",
    },
    {
        "PRD_DE": "202612",
        "DT": "999.0",
        "PUBLISHED_AT": "2027-01-08T00:00:00Z",
        "FIRST_SEEN_AT": "2027-01-10T09:00:00+09:00",
        "REVISION_AT": "2027-01-08T00:00:00Z",
    },  # after the cutoff
]


def _collect(spec=None, rows=ROWS, metrics=("benchmark_price",), as_of=AS_OF):
    spec = spec or _spec()

    def fetch(url: str) -> str:
        raise AssertionError(f"live source must not be fetched by collector: {url}")

    def snapshot_text(path: str) -> str:
        assert path == spec.snapshot_path
        return json.dumps(
            {
                "schema_version": SERIES_SNAPSHOT_SCHEMA,
                "series_id": spec.series_id,
                "source_id": spec.source_id,
                "verification_url": spec.verification_url,
                "captured_at": "2026-08-27T00:00:00+00:00",
                "observations": rows,
            }
        )

    collector = request_scoped_industry_series_collector(
        fetch,
        source_id=spec.source_id,
        as_of=as_of,
        segment_id="core",
        series=(spec,),
        snapshot_text=snapshot_text,
    )
    return collector(EvidenceCollectionRequest(target_id=TARGET, required_metrics=metrics))


def test_latest_observation_within_the_cutoff_is_selected():
    batch = _collect()
    record = batch.records[0]
    assert record.metric == "benchmark_price"
    assert float(record.value) == 103.0
    assert record.effective_date == "2026-07-31"
    assert record.observed_date == "2026-08-10"
    assert record.source_layer is EvidenceSourceLayer.AUTHORIZED_MARKET_DATA
    assert "definition_id=DEF_S1" in record.notes
    assert "published_at=2026-08-08T00:00:00+00:00" in record.notes
    assert "first_seen_at=2026-08-10T09:00:00+09:00" in record.notes


@pytest.mark.parametrize("late_field", ["PUBLISHED_AT", "FIRST_SEEN_AT", "REVISION_AT"])
def test_post_cutoff_knowledge_timestamp_cannot_leak_a_revision(late_field):
    rows = [dict(row) for row in ROWS[:2]]
    rows[1][late_field] = "2026-08-28T00:00:00Z"
    if late_field == "PUBLISHED_AT":
        rows[1]["REVISION_AT"] = "2026-08-28T00:00:00Z"
        rows[1]["FIRST_SEEN_AT"] = "2026-08-29T00:00:00Z"
    elif late_field == "REVISION_AT":
        rows[1]["FIRST_SEEN_AT"] = "2026-08-29T00:00:00Z"
    batch = _collect(rows=rows)
    record = batch.records[0]
    assert float(record.value) == 101.5
    assert record.effective_date == "2026-06-30"


@pytest.mark.parametrize("missing_field", ["PUBLISHED_AT", "FIRST_SEEN_AT", "REVISION_AT"])
def test_verified_eligible_rows_require_all_knowledge_timestamps(missing_field):
    row = dict(ROWS[0])
    del row[missing_field]
    with pytest.raises(IndustrySeriesError, match=f"missing required.*{missing_field}"):
        _collect(rows=[row])


def test_duplicate_eligible_periods_fail_closed():
    with pytest.raises(IndustrySeriesError, match="duplicate eligible revision identity"):
        _collect(rows=[dict(ROWS[0]), dict(ROWS[0])])


def test_revision_history_replays_the_value_known_at_each_cutoff():
    old = dict(ROWS[1])
    revised = {
        "PRD_DE": "202607",
        "DT": "104.5",
        "PUBLISHED_AT": "2026-09-08T00:00:00Z",
        "FIRST_SEEN_AT": "2026-09-10T00:00:00Z",
        "REVISION_AT": "2026-09-10T00:00:00Z",
    }
    historical = _collect(rows=[old, revised], as_of="2026-08-27")
    current = _collect(rows=[old, revised], as_of="2026-09-30")
    assert float(historical.records[0].value) == 103.0
    assert float(current.records[0].value) == 104.5
    assert historical.records[0].observed_date == "2026-08-10"
    assert current.records[0].observed_date == "2026-09-10"


def test_a_company_realized_metric_is_refused_by_the_definition_gate():
    with pytest.raises(IndustrySeriesError, match="company-realized"):
        _spec(metric="realized_price").validate()
    with pytest.raises(IndustrySeriesError, match="company-realized"):
        _spec(metric="production").validate()


def test_an_unverified_series_never_collects():
    with pytest.raises(IndustrySeriesError, match="no verified series"):
        request_scoped_industry_series_collector(
            lambda u: "[]", source_id="KR_KOSIS_API", as_of=AS_OF,
            segment_id="core", series=(_spec(verified=False),),
        )


def test_no_observation_inside_the_cutoff_is_a_gap_not_a_zero():
    batch = _collect(rows=[dict(ROWS[2])])
    assert not batch.records  # coverage names the metric downstream


def test_the_credential_parameter_never_reaches_the_evidence_ref(monkeypatch):
    monkeypatch.setenv("TEST_SERIES_KEY", "supersecret")
    spec = _spec(
        url_template="https://api.example/data?apiKey={api_key}&tbl=T1",
        api_key_env="TEST_SERIES_KEY",
        verification_url="https://api.example/catalog?tbl=T1",
    )
    assert "supersecret" in spec.fetch_url()  # verifier-only upstream fetch
    batch = _collect(spec=spec)
    assert "supersecret" not in batch.records[0].source_ref
    assert "apiKey" not in batch.records[0].source_ref
    assert canonical_verification_url(batch.records[0].source_ref) is not None


def test_credential_free_url_removes_the_sensitive_query_key():
    url = credential_free_verification_url(
        "https://api.example/data?apiKey={api_key}&tblId=T1&format=json"
    )
    assert url == "https://api.example/data?tblId=T1&format=json"
    assert canonical_verification_url(url) == url


def test_a_missing_credential_fails_closed():
    spec = _spec(
        url_template="https://api.example/data?apiKey={api_key}",
        api_key_env="DEFINITELY_UNSET_KEY_XYZ",
    )
    with pytest.raises(IndustrySeriesError, match="requires credential"):
        spec.fetch_url()


def test_a_verified_registry_row_requires_a_snapshot_and_safe_verification_url():
    with pytest.raises(IndustrySeriesError, match="snapshot_path"):
        _spec(snapshot_path="").validate()
    with pytest.raises(IndustrySeriesError, match="credential-free"):
        _spec(verification_url="https://api.example/data?apiKey=REDACTED").validate()


def test_two_verified_series_for_one_metric_is_a_conflict_not_an_average(tmp_path):
    registry = tmp_path / "registry.yaml"
    row = """
  - series_id: {sid}
    source_id: KR_KOSIS_API
    metric: benchmark_price
    layer: authorized_market_data
    unit: dimensionless
    geography: KR
    definition_id: DEF_{sid}
    definition: Synthetic producer-price benchmark index for tests.
    url_template: https://probe.invalid/{sid}.json
    api_key_env: ""
    snapshot_path: snapshots/{sid}.json
    verification_url: https://probe.invalid/catalog/{sid}
    verified: true"""
    registry.write_text(
        "series:" + row.format(sid="A1") + row.format(sid="B1") + "\n",
        encoding="utf-8",
    )
    with pytest.raises(IndustrySeriesError, match="scoped-split.*never an average"):
        load_industry_series_registry(registry)


def test_the_default_registry_ships_with_zero_verified_rows():
    specs = load_industry_series_registry()
    assert not [item for item in specs if item.verified]
    providers = industry_series_collector_providers(
        lambda u: "[]", as_of=AS_OF, segment_id="core"
    )
    assert providers == ()


def test_a_label_is_not_a_definition():
    with pytest.raises(IndustrySeriesError, match="real definition"):
        _spec(definition="PPI").validate()
