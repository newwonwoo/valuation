"""'분석시작 <회사>' one-liner: environment in, validated canonical config out."""

from __future__ import annotations

import json

import pytest

from valuation_engine.cli_runtime import (
    LiveAnalysisRequest,
    load_live_runtime_config_factory,
)
from valuation_engine.generic_kr_cli import GenericCLIConfigError, factory
from valuation_engine.generic_underwriting import DeclaredUnderwritingError
from valuation_engine.live_runtime import LivePrimaryRuntimeConfig


def _request(tmp_path) -> LiveAnalysisRequest:
    return LiveAnalysisRequest(
        command="분석시작 한빛제강",
        company_query="한빛제강",
        state_root=tmp_path,
        run_id="CLI-TEST",
        jurisdiction="KR",
    )


def _base_env(monkeypatch, tmp_path):
    uw = tmp_path / "uw.yaml"
    uw.write_text(
        "target_id: KR:DART:00999902\n"
        "as_of: \"2026-08-27\"\n"
        "source_ref: https://example.test/underwriting-memo\n"
        "declarations:\n"
        "  normalized_ebitda:\n"
        "    value: 940\n"
        "    unit: KRW_billion\n"
        "    rationale: mid-cycle EBITDA normalized from filing history for the test run.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DART_API_KEY", "TESTKEY")
    monkeypatch.setenv(
        "VALUATION_LLM_TRANSPORT",
        "valuation_engine.llm_transport:empty_scripted_transport",
    )
    monkeypatch.setenv("VALUATION_METHOD", "commodity_price_taker/normalized_multiple")
    monkeypatch.setenv("VALUATION_AS_OF", "2026-08-27")
    monkeypatch.setenv("VALUATION_UNDERWRITING_PATH", str(uw))


def test_the_cli_factory_builds_a_validated_config(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    config = factory(_request(tmp_path))
    assert isinstance(config, LivePrimaryRuntimeConfig)
    config.validate()
    assert config.company_request.query == "한빛제강"
    assert config.method_choices[0].archetype == "commodity_price_taker"
    collector_ids = {
        item.capability.collector_id for item in config.providers.collectors
    }
    assert "kr-opendart-core-financials" in collector_ids
    assert "kr-dart-filing-kpi" in collector_ids
    assert "operator-declared-underwriting" in collector_ids


def test_the_factory_is_loadable_through_the_cli_spec_contract(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    loaded = load_live_runtime_config_factory("valuation_engine.generic_kr_cli:factory")
    config = loaded(_request(tmp_path))
    assert isinstance(config, LivePrimaryRuntimeConfig)


def test_a_missing_model_transport_fails_closed_with_the_reason(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.delenv("VALUATION_LLM_TRANSPORT")
    with pytest.raises(GenericCLIConfigError, match="no model binding"):
        factory(_request(tmp_path))


def test_a_missing_method_declaration_fails_closed(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.delenv("VALUATION_METHOD")
    with pytest.raises(GenericCLIConfigError, match="analyst intent"):
        factory(_request(tmp_path))


def test_without_underwriting_the_config_still_builds(monkeypatch, tmp_path):
    """No judgments declared: the run will fail closed later at evidence
    coverage with named metrics — the config layer must not demand them."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.delenv("VALUATION_UNDERWRITING_PATH")
    config = factory(_request(tmp_path))
    collector_ids = {
        item.capability.collector_id for item in config.providers.collectors
    }
    assert "operator-declared-underwriting" not in collector_ids


def test_future_underwriting_is_rejected_before_provider_registration(
    monkeypatch, tmp_path
):
    _base_env(monkeypatch, tmp_path)
    path = tmp_path / "uw.yaml"
    text = path.read_text(encoding="utf-8").replace(
        'as_of: "2026-08-27"', 'as_of: "2026-08-28"'
    )
    path.write_text(text, encoding="utf-8")

    with pytest.raises(DeclaredUnderwritingError, match="after run cutoff"):
        factory(_request(tmp_path))
