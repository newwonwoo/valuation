"""Industry series become Evidence only through the definition gate."""

from __future__ import annotations

import json

import pytest

from valuation_engine.evidence_collection import EvidenceCollectionRequest
from valuation_engine.industry_series_collector import (
    IndustrySeriesError,
    IndustrySeriesSpec,
    industry_series_collector_providers,
    load_industry_series_registry,
    request_scoped_industry_series_collector,
)
from valuation_engine.records import EvidenceSourceLayer


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
        verified=True,
    )
    defaults.update(overrides)
    return IndustrySeriesSpec(**defaults)


ROWS = [
    {"PRD_DE": "202606", "DT": "101.5"},
    {"PRD_DE": "202607", "DT": "103.0"},
    {"PRD_DE": "202612", "DT": "999.0"},  # after the cutoff
]


def _collect(spec=None, rows=ROWS, metrics=("benchmark_price",)):
    spec = spec or _spec()

    def fetch(url: str) -> str:
        assert url == spec.url_template
        return json.dumps(rows)

    collector = request_scoped_industry_series_collector(
        fetch, source_id=spec.source_id, as_of=AS_OF, segment_id="core", series=(spec,)
    )
    return collector(EvidenceCollectionRequest(target_id=TARGET, required_metrics=metrics))


def test_latest_observation_within_the_cutoff_is_selected():
    batch = _collect()
    record = batch.records[0]
    assert record.metric == "benchmark_price"
    assert float(record.value) == 103.0
    assert record.effective_date == "2026-07-31"
    assert record.observed_date == AS_OF
    assert record.source_layer is EvidenceSourceLayer.AUTHORIZED_MARKET_DATA
    assert "definition_id=DEF_S1" in record.notes


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
    batch = _collect(rows=[{"PRD_DE": "202612", "DT": "999.0"}])
    assert not batch.records  # coverage names the metric downstream


def test_the_credential_never_reaches_the_evidence_ref(monkeypatch):
    monkeypatch.setenv("TEST_SERIES_KEY", "supersecret")
    spec = _spec(
        url_template="https://api.example/data?apiKey={api_key}&tbl=T1",
        api_key_env="TEST_SERIES_KEY",
    )

    def fetch(url: str) -> str:
        assert "supersecret" in url  # the fetch itself uses the real key
        return json.dumps(ROWS)

    collector = request_scoped_industry_series_collector(
        fetch, source_id=spec.source_id, as_of=AS_OF, segment_id="core", series=(spec,)
    )
    batch = collector(
        EvidenceCollectionRequest(target_id=TARGET, required_metrics=("benchmark_price",))
    )
    assert "supersecret" not in batch.records[0].source_ref
    assert "[CREDENTIAL]" in batch.records[0].source_ref


def test_a_missing_credential_fails_closed():
    spec = _spec(
        url_template="https://api.example/data?apiKey={api_key}",
        api_key_env="DEFINITELY_UNSET_KEY_XYZ",
    )
    with pytest.raises(IndustrySeriesError, match="requires credential"):
        _collect(spec=spec)


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
