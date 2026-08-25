from __future__ import annotations

import pytest

from valuation_engine.cli_runtime import LiveAnalysisRequest
from valuation_engine.kr_opendart_provider import (
    KRLiveProviderExtensions,
    KRLiveRuntimeFactory,
    OpenDartFilingSelection,
    OpenDartNetwork,
)
from valuation_engine.live_primary_adapters import SegmentDescriptor
from valuation_engine.scenario_binding import ScenarioBindingSpec


def _segment(segment_id: str) -> SegmentDescriptor:
    return SegmentDescriptor(
        segment_id=segment_id,
        name=f"Segment {segment_id}",
        revenue_recognition="delivery",
        price_formation="contract",
        asset_ownership="company",
        capital_intensity="medium",
        regulation_intensity="low",
        customer_structure="diversified",
        reinvestment_model="maintenance and growth capex",
        cashflow_duration="multi-year",
        evidence_ids=(f"E:{segment_id}",),
    )


def _factory(segment_decomposer) -> KRLiveRuntimeFactory:
    noop = lambda *args, **kwargs: None
    extensions = KRLiveProviderExtensions(
        industry_snapshot_loader=noop,
        freshness_loader=noop,
        segment_decomposer=segment_decomposer,
        industry_dna_router=noop,
        scanner_runners={},
        intelligence_officer=noop,
        red_team_officer=noop,
        bridge_analyst=noop,
        evaluator_registry_loader=noop,
        valuation_plan_inputs_loader=noop,
    )
    return KRLiveRuntimeFactory(
        network=OpenDartNetwork(
            fetch_text=lambda _: '{"status":"000","list":[]}',
            fetch_bytes=lambda _: b"unused-until-company-resolution",
            api_key="TEST-KEY",
        ),
        filing=OpenDartFilingSelection(
            business_year="2025",
            report_code="11011",
            fiscal_period_end="2025-12-31",
            checked_at="2026-03-20",
            segment_id="company",
        ),
        extensions=extensions,
        scenario_binding_spec=ScenarioBindingSpec(("Base",), ("revenue",)),
    )


def _request(tmp_path) -> LiveAnalysisRequest:
    return LiveAnalysisRequest(
        command="분석시작 테스트기업",
        company_query="테스트기업",
        state_root=tmp_path,
        run_id="SEGMENT-SCOPE-1",
        jurisdiction="KR",
    )


def test_matching_single_segment_is_preserved(tmp_path):
    config = _factory(
        lambda *_: (_segment("company"),)
    )(_request(tmp_path))
    segments = config.providers.segment_decomposer(None, None)
    assert tuple(item.segment_id for item in segments) == ("company",)


def test_mismatched_segment_is_rejected_before_collection_planning(tmp_path):
    config = _factory(
        lambda *_: (_segment("semiconductor"),)
    )(_request(tmp_path))
    with pytest.raises(ValueError, match="filing collector scope"):
        config.providers.segment_decomposer(None, None)


def test_multi_segment_output_is_rejected_before_collection_planning(tmp_path):
    config = _factory(
        lambda *_: (
            _segment("company"),
            _segment("other"),
        )
    )(_request(tmp_path))
    with pytest.raises(ValueError, match="exactly one segment"):
        config.providers.segment_decomposer(None, None)


def test_non_descriptor_output_is_rejected(tmp_path):
    config = _factory(
        lambda *_: ("company",)
    )(_request(tmp_path))
    with pytest.raises(TypeError, match="SegmentDescriptor"):
        config.providers.segment_decomposer(None, None)
