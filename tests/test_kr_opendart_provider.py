from __future__ import annotations

from io import BytesIO
import json
from urllib.parse import parse_qs, urlparse
from zipfile import ZipFile

import pytest

from valuation_engine.cli_runtime import LiveAnalysisRequest
from valuation_engine.dart_facts import DEFAULT_CORE_FACT_SPECS
from valuation_engine.evidence_collection import EvidenceCollectionRequest
from valuation_engine.kr_opendart_provider import (
    KRLiveProviderExtensions,
    KRLiveRuntimeFactory,
    OpenDartFilingSelection,
    OpenDartNetwork,
    opendart_corp_code_from_target_id,
    request_scoped_opendart_fact_collector,
)
from valuation_engine.live_runtime import LiveCollectorProvider
from valuation_engine.collection_plan import CollectorCapability
from valuation_engine.scenario_binding import ScenarioBindingSpec


def corp_archive() -> bytes:
    xml = """<?xml version='1.0' encoding='UTF-8'?>
<result>
  <list>
    <corp_code>00126380</corp_code>
    <corp_name>삼성전자</corp_name>
    <stock_code>005930</stock_code>
    <modify_date>20260301</modify_date>
  </list>
</result>""".encode("utf-8")
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("CORPCODE.xml", xml)
    return output.getvalue()


def dart_payload() -> str:
    rows = [
        {
            "corp_code": "00126380",
            "bsns_year": "2025",
            "reprt_code": "11011",
            "fs_div": "CFS",
            "sj_div": "IS",
            "account_id": "ifrs-full_Revenue",
            "thstrm_amount": "1000000000",
            "currency": "KRW",
            "rcept_no": "20260315001234",
        },
        {
            "corp_code": "00126380",
            "bsns_year": "2025",
            "reprt_code": "11011",
            "fs_div": "CFS",
            "sj_div": "BS",
            "account_id": "ifrs-full_Assets",
            "thstrm_amount": "5000000000",
            "currency": "KRW",
            "rcept_no": "20260315001234",
        },
    ]
    return json.dumps({"status": "000", "list": rows}, ensure_ascii=False)


def network(seen: list[str] | None = None) -> OpenDartNetwork:
    calls = seen if seen is not None else []

    def fetch_text(url: str) -> str:
        calls.append(url)
        return dart_payload()

    def fetch_bytes(url: str) -> bytes:
        calls.append(url)
        return corp_archive()

    return OpenDartNetwork(
        fetch_text=fetch_text,
        fetch_bytes=fetch_bytes,
        api_key="TEST-KEY",
    )


def filing(*, segment_id: str = "company") -> OpenDartFilingSelection:
    return OpenDartFilingSelection(
        business_year="2025",
        report_code="11011",
        fiscal_period_end="2025-12-31",
        checked_at="2026-03-20",
        segment_id=segment_id,
    )


def extensions(
    *,
    additional_collectors: tuple[LiveCollectorProvider, ...] = (),
) -> KRLiveProviderExtensions:
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
        additional_collectors=additional_collectors,
    )


def request(tmp_path, *, jurisdiction: str | None = "KR") -> LiveAnalysisRequest:
    return LiveAnalysisRequest(
        command="분석시작 삼성전자",
        company_query="삼성전자",
        state_root=tmp_path,
        run_id="KR-LIVE-1",
        jurisdiction=jurisdiction,
    )


def test_opendart_target_id_parser_is_exact():
    assert opendart_corp_code_from_target_id("KR:DART:00126380") == "00126380"
    for invalid in (
        "US:SEC:00126380",
        "KR:DART:123",
        "KR:DART:ABCDEFGH",
        "",
    ):
        with pytest.raises(ValueError):
            opendart_corp_code_from_target_id(invalid)


def test_request_scoped_collector_emits_only_task_metrics():
    seen: list[str] = []
    collector = request_scoped_opendart_fact_collector(
        network(seen),
        filing(segment_id="core"),
    )
    batch = collector(
        EvidenceCollectionRequest(
            "KR:DART:00126380",
            ("revenue",),
        )
    )
    assert batch.source_id == "KR_OPENDART"
    assert tuple(record.metric for record in batch.records) == ("revenue",)
    assert batch.records[0].segment == "core"
    assert batch.records[0].value == 1_000_000_000
    assert "TEST-KEY" not in batch.records[0].source_ref
    query = parse_qs(urlparse(seen[-1]).query)
    assert query["corp_code"] == ["00126380"]
    assert query["crtfc_key"] == ["TEST-KEY"]


def test_request_scoped_collector_rejects_undeclared_metric():
    collector = request_scoped_opendart_fact_collector(network(), filing())
    with pytest.raises(ValueError, match="outside its declared capability"):
        collector(
            EvidenceCollectionRequest(
                "KR:DART:00126380",
                ("backlog",),
            )
        )


def test_factory_builds_official_resolver_and_core_fact_provider(tmp_path):
    seen: list[str] = []
    factory = KRLiveRuntimeFactory(
        network=network(seen),
        filing=filing(segment_id="core"),
        extensions=extensions(),
        scenario_binding_spec=ScenarioBindingSpec(("Base",), ("revenue",)),
    )
    config = factory(request(tmp_path, jurisdiction="KOR"))
    assert config.run_id == "KR-LIVE-1"
    assert config.company_request.jurisdiction == "KR"
    assert config.state_root == tmp_path
    assert len(config.providers.collectors) == 1

    identity = config.providers.company_resolver(config.company_request)
    assert identity.target_id == "KR:DART:00126380"
    assert identity.ticker == "005930"
    assert identity.jurisdiction == "KR"

    provider = config.providers.collectors[0]
    assert provider.capability.source_id == "KR_OPENDART"
    assert provider.capability.supported_metrics == tuple(
        spec.metric for spec in DEFAULT_CORE_FACT_SPECS
    )
    batch = provider.collector(
        EvidenceCollectionRequest(identity.target_id, ("total_assets",))
    )
    assert tuple(record.metric for record in batch.records) == ("total_assets",)


def test_factory_rejects_non_korean_request(tmp_path):
    factory = KRLiveRuntimeFactory(
        network=network(),
        filing=filing(),
        extensions=extensions(),
        scenario_binding_spec=ScenarioBindingSpec(("Base",), ("revenue",)),
    )
    with pytest.raises(ValueError, match="Korean companies only"):
        factory(request(tmp_path, jurisdiction="US"))


def test_factory_keeps_additional_collectors_and_rejects_duplicate_ids(tmp_path):
    extra = LiveCollectorProvider(
        CollectorCapability(
            collector_id="extra-primary",
            source_id="KR_KOSIS_API",
            supported_metrics=("utilization",),
            jurisdictions=("KR",),
            implementation_ref="tests.extra",
        ),
        lambda request: None,
    )
    factory = KRLiveRuntimeFactory(
        network=network(),
        filing=filing(),
        extensions=extensions(additional_collectors=(extra,)),
        scenario_binding_spec=ScenarioBindingSpec(("Base",), ("revenue",)),
    )
    config = factory(request(tmp_path))
    assert tuple(
        item.capability.collector_id for item in config.providers.collectors
    ) == ("kr-opendart-core-financials", "extra-primary")

    duplicate = LiveCollectorProvider(
        CollectorCapability(
            collector_id="kr-opendart-core-financials",
            source_id="KR_KOSIS_API",
            supported_metrics=("utilization",),
            jurisdictions=("KR",),
            implementation_ref="tests.duplicate",
        ),
        lambda request: None,
    )
    broken = KRLiveRuntimeFactory(
        network=network(),
        filing=filing(),
        extensions=extensions(additional_collectors=(duplicate,)),
        scenario_binding_spec=ScenarioBindingSpec(("Base",), ("revenue",)),
    )
    with pytest.raises(ValueError, match="collector provider IDs must be unique"):
        broken(request(tmp_path))


def test_factory_does_not_require_dart_credential_until_source_call(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    no_key_network = OpenDartNetwork(
        fetch_text=lambda _: dart_payload(),
        fetch_bytes=lambda _: corp_archive(),
    )
    factory = KRLiveRuntimeFactory(
        network=no_key_network,
        filing=filing(),
        extensions=extensions(),
        scenario_binding_spec=ScenarioBindingSpec(("Base",), ("revenue",)),
    )
    config = factory(request(tmp_path))
    with pytest.raises(Exception, match="DART_API_KEY"):
        config.providers.company_resolver(config.company_request)
