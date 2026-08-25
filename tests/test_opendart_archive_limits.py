from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

import valuation_engine.kr_opendart_provider as provider_module
from valuation_engine.cli_runtime import LiveAnalysisRequest
from valuation_engine.kr_opendart_provider import (
    KRLiveProviderExtensions,
    KRLiveRuntimeFactory,
    OpenDartFilingSelection,
    OpenDartNetwork,
)
from valuation_engine.scenario_binding import ScenarioBindingSpec


def _archive(
    *,
    xml_size: int = 128,
    extra_members: int = 0,
) -> bytes:
    xml = b"<result>" + (b"x" * xml_size) + b"</result>"
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("CORPCODE.xml", xml)
        for index in range(extra_members):
            archive.writestr(f"metadata-{index}.txt", b"metadata")
    return output.getvalue()


def _extensions() -> KRLiveProviderExtensions:
    noop = lambda *args, **kwargs: None
    return KRLiveProviderExtensions(
        industry_snapshot_loader=noop,
        freshness_loader=noop,
        segment_decomposer=noop,
        industry_dna_router=noop,
        scanner_runners={},
        intelligence_officer=noop,
        red_team_officer=noop,
        bridge_analyst=noop,
        evaluator_registry_loader=noop,
        valuation_plan_inputs_loader=noop,
    )


def _filing() -> OpenDartFilingSelection:
    return OpenDartFilingSelection(
        business_year="2025",
        report_code="11011",
        fiscal_period_end="2025-12-31",
        checked_at="2026-03-20",
    )


def _factory(
    payload: bytes,
    *,
    max_members: int = 8,
    max_uncompressed_bytes: int = 32_000_000,
) -> KRLiveRuntimeFactory:
    return KRLiveRuntimeFactory(
        network=OpenDartNetwork(
            fetch_text=lambda _: '{"status":"000","list":[]}',
            fetch_bytes=lambda _: payload,
            api_key="TEST-KEY",
            max_corp_archive_members=max_members,
            max_corp_archive_uncompressed_bytes=max_uncompressed_bytes,
        ),
        filing=_filing(),
        extensions=_extensions(),
        scenario_binding_spec=ScenarioBindingSpec(("Base",), ("revenue",)),
    )


def _request(tmp_path) -> LiveAnalysisRequest:
    return LiveAnalysisRequest(
        command="분석시작 삼성전자",
        company_query="삼성전자",
        state_root=tmp_path,
        run_id="ARCHIVE-LIMIT-1",
        jurisdiction="KR",
    )


def test_corp_archive_member_count_is_bounded_before_resolver_read(tmp_path):
    config = _factory(
        _archive(extra_members=3),
        max_members=2,
    )(_request(tmp_path))
    with pytest.raises(ValueError, match="member limit"):
        config.providers.company_resolver(config.company_request)


def test_member_count_preflight_runs_before_zipfile_construction(
    monkeypatch,
):
    payload = _archive(extra_members=3)
    network = _factory(payload, max_members=2).network

    def must_not_construct_zipfile(*args, **kwargs):
        raise AssertionError(
            "ZipFile must not be constructed before the EOCD member bound passes"
        )

    monkeypatch.setattr(
        provider_module,
        "ZipFile",
        must_not_construct_zipfile,
    )
    with pytest.raises(ValueError, match="member limit"):
        network.fetch_validated_corp_archive(
            "https://opendart.fss.or.kr/api/corpCode.xml"
        )


def test_corp_archive_uncompressed_size_is_bounded_before_resolver_read(
    tmp_path,
):
    payload = _archive(xml_size=4096)
    assert len(payload) < 1024
    config = _factory(
        payload,
        max_uncompressed_bytes=512,
    )(_request(tmp_path))
    with pytest.raises(ValueError, match="uncompressed-size limit"):
        config.providers.company_resolver(config.company_request)


def test_opendart_filing_source_id_is_pinned_to_canonical_registry_identity():
    selection = replace(_filing(), source_id="KR_KOSIS_API")
    with pytest.raises(ValueError, match="canonical source_id KR_OPENDART"):
        selection.validate()


def test_open_dart_network_validates_archive_limits():
    with pytest.raises(ValueError, match="max_corp_archive_members"):
        replace(
            _factory(_archive()).network,
            max_corp_archive_members=0,
        ).validate()
    with pytest.raises(ValueError, match="max_corp_archive_uncompressed_bytes"):
        replace(
            _factory(_archive()).network,
            max_corp_archive_uncompressed_bytes=0,
        ).validate()
