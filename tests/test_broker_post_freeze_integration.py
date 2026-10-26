"""Synthetic broker references through the actual 33-stage canonical entrypoint.

No Lunit facts, live broker reports, network calls or LLM quality claims appear
here. The existing cold-start issuer supplies the intrinsic baseline. Its exact
saved compiler output is replay-bound as the own-model comparison input.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

import pytest

from valuation_engine.broker_comparison import (
    BrokerClaim, BrokerClaimType, BrokerMetric, BrokerMetricBasis,
    BrokerReference, BrokerReferenceBundle, canonical_broker_payload,
)
from valuation_engine.broker_judgment import broker_review_receipt_from_dict
from valuation_engine.cli_runtime import LiveAnalysisRequest
from valuation_engine.cold_start_probe import (
    PROBE_AS_OF, PROBE_COMPANY_NAME, PROBE_CORP_CODE,
    _staff_scripts, probe_network, probe_runtime_spec,
)
from valuation_engine.control_plane import StageStatus
from valuation_engine.generic_live_providers import build_generic_kr_runtime_factory
from valuation_engine.host_judgment import HostJudgmentRequest
from valuation_engine.kr_opendart_provider import KRLiveProviderExtensions
from valuation_engine.llm_transport import ScriptedTransport
from valuation_engine.official_market_data import load_broker_own_metric_manifest
from valuation_engine.strict_live_runtime import run_prism


TARGET_ID = f"KR:DART:{PROBE_CORP_CODE}"
RUN_ID = "SYNTHETIC-BROKER-INTEGRATION"
OBSERVED_AT = "2026-08-27T09:00:00Z"
COMPARISON_AS_OF = "2026-08-27T10:00:00Z"
SOURCE_NOTE = "Synthetic normalized mid-cycle estimate; not an observed annual IFRS result."


def _basis():
    # A normalized annual earnings assumption at the intrinsic cutoff, not a
    # made-up FY forecast: this is the actual cold-start evaluator's metric.
    return BrokerMetricBasis(
        metric="normalized_ebitda", period_start=PROBE_AS_OF,
        period_end=PROBE_AS_OF, frequency="NORMALIZED_ANNUAL", scope="SEGMENT:core",
        perimeter="CORE_OPERATING_SEGMENT", accounting_basis="NORMALIZED_MIDCYCLE_EBITDA",
        unit="KRW_billion", currency="KRW",
    )


def _references(*, aggregate=True):
    nr = BrokerReference(
        reference_id="synthetic-nr", reference_kind="NAMED_REPORT",
        provider_name="Synthetic public research archive", broker="Synthetic NR Research",
        source_ref="https://probe.invalid/broker/synthetic-nr",
        content_sha256=sha256(SOURCE_NOTE.encode()).hexdigest(),
        authorization_basis="explicit_permission", observed_at=OBSERVED_AT,
        original_published_at="2026-08-20", rating="NR",
        valuation_method="EV/EBITDA", valuation_base_year="Normalized mid-cycle",
        access_quality="Synthetic test source, no real investment evidence.",
        underlying_source_families=("synthetic-independent-cost-study",),
        claims=(BrokerClaim(
            "synthetic-normalized-ebitda", BrokerClaimType.BROKER_FORECAST, SOURCE_NOTE,
            BrokerMetric("synthetic-broker-ebitda", _basis(), value="980",
                         source_locator="Synthetic exhibit 2, normalized earnings row"),
            source_locator="Synthetic exhibit 2, normalized earnings row",
        ),),
    )
    refs = (nr,)
    if aggregate:
        refs += (BrokerReference(
            reference_id="synthetic-aggregate", reference_kind="AGGREGATE_SNAPSHOT",
            provider_name="Synthetic quote aggregator", broker=None,
            source_ref="https://probe.invalid/broker/synthetic-aggregate-snapshot",
            content_sha256=sha256(b"Synthetic 9000 aggregate; one unidentified contributor").hexdigest(),
            authorization_basis="explicit_permission", observed_at=OBSERVED_AT,
            original_published_at=None, last_rating_at="2026-08-14",
            rating="SELL", target_price="9000", target_currency="KRW",
            aggregate_report_count=1, aggregate_median_target_price="9000",
            aggregate_min_target_price="9000", aggregate_max_target_price="9000",
            access_quality="Synthetic observation; original report, broker and method unknown.",
        ),)
    return BrokerReferenceBundle(
        "broker-reference-bundle/v1", TARGET_ID, COMPARISON_AS_OF, refs,
        search_coverage="Synthetic control only; not an exhaustive real broker search.",
        limitations=("No real-company investment conclusion is supported by these fixtures.",),
    )


def _response(request: HostJudgmentRequest):
    """Actual B response contract, not a mocked receipt or stage outcome."""
    rows = []
    for claim in request.source_bindings[0]["claim_index"]:
        research = claim["claim_id"] == "synthetic-normalized-ebitda"
        rows.append({
            "claim_id": claim["claim_id"],
            "disposition": "CONDITIONAL_HYPOTHESIS" if research else "REFERENCE_ONLY",
            "rationale": ("원가와 정상화 기준의 차이를 재조사하되 기존 가치는 유지합니다."
                          if research else "개별 작성자와 산출법이 확인되지 않은 집계 관측치입니다."),
            "evidence_refs": [claim["reference_id"]],
            "support_relation": "UNKNOWN", "basis_status": "COMPARABLE" if research else "UNKNOWN",
            "uncertainty": "합성 입력이며 실제 기업의 전망이나 경제적 설득력을 검증하지 않습니다.",
            "missing_evidence": ["Independent current cost and normalization evidence"],
            "research_question": "Which normalized costs explain the earnings discrepancy?" if research else "",
            "proposed_metric": "normalized_ebitda" if research else "",
            "independent_derivation_plan": "Rebuild normalized earnings from costs and sales, including contrary cases, for a new run." if research else "",
            "falsifier": "Observed cost structure contradicts the proposed normalized earnings mechanism." if research else "",
            "normalization_requirements": ["Normalized period, core perimeter, currency and earnings basis"] if research else [],
            "source_dependence": "Synthetic known source family" if research else "Unknown original contributor",
            "impact_scope": "Core normalized earnings, not a committed value increase" if research else "Reference only",
        })
    return {
        "schema_version": "host-judgment-response/v1", "request_hash": request.request_hash,
        "target_id": request.gap.target_id, "as_of": request.gap.as_of,
        "decision": "REVIEW_CLAIMS", "proposal": {"decisions": rows},
        "rationale": "정상화 수익 차이는 후속 검증 대상으로 남기고 동결된 기존 가치를 바꾸지 않습니다.",
        "counterevidence": [], "uncertainty": "합성 통합 검사이며 실제 전망의 신뢰성 판단은 입증하지 않습니다.",
    }


def _run(root, *, references=None, metrics_loader=None, officer=None):
    factory = build_generic_kr_runtime_factory(
        network=probe_network(), transport=ScriptedTransport(_staff_scripts()),
        spec=probe_runtime_spec(),
    )
    assert isinstance(factory.extensions, KRLiveProviderExtensions)
    if references is not None:
        factory = replace(factory, extensions=replace(
            factory.extensions, broker_reference_loader=lambda: references,
            broker_own_metrics_loader=metrics_loader, broker_review_officer=officer,
        ))
    request = LiveAnalysisRequest(
        command=f"분석시작 {PROBE_COMPANY_NAME}", company_query=PROBE_COMPANY_NAME,
        state_root=str(root), run_id=RUN_ID, jurisdiction="KR",
    )
    return run_prism(factory(request))


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    run = _run(tmp_path_factory.mktemp("synthetic-old-street"))
    assert run.result.completed, run.result.blocked_reasons
    run.validate_canonical()
    return run


@pytest.fixture(scope="module")
def own_metrics_loader(baseline, tmp_path_factory):
    """Use the real saved compiler artifact, never an unbound typed forecast."""
    result = baseline.result
    artifact = Path(result.data["saved_run_dir"]) / "compiled_assumptions.json"
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    index, assumption = next((i, row) for i, row in enumerate(payload["assumptions"])
                             if row["key"] == "normalized_ebitda")
    token = result.freeze_token
    manifest = {
        "schema_version": "broker-own-metrics/v1",
        "expected_valuation_hash": token.valuation_hash,
        "expected_assumption_set_hash": token.assumption_set_hash,
        "metrics": [{
            "metric": canonical_broker_payload(BrokerMetric(
                "synthetic-frozen-ebitda", _basis(), value=assumption["measure"]["amount"],
                source_locator=f"compiled_assumptions.json#/assumptions/{index}/measure/amount")),
            "scenario_id": assumption["scenario_id"], "artifact_ref": str(artifact),
            "artifact_sha256": sha256(artifact.read_bytes()).hexdigest(),
            "json_pointer": f"/assumptions/{index}/measure/amount",
        }],
    }
    path = tmp_path_factory.mktemp("synthetic-own-metric-replay") / "own_metrics.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    def loader(freeze_hash, valuation_hash, assumption_hash):
        return load_broker_own_metric_manifest(
            path, freeze_token_hash=freeze_hash, valuation_hash=valuation_hash,
            assumption_set_hash=assumption_hash,
        )
    return loader


@pytest.fixture(scope="module")
def baseline_artifact_hashes(baseline):
    directory = Path(baseline.result.data["saved_run_dir"])
    return {path: sha256(path.read_bytes()).hexdigest() for path in directory.iterdir() if path.is_file()}


@pytest.fixture(scope="module")
def rich_run(tmp_path_factory, own_metrics_loader, baseline_artifact_hashes):
    requests = []

    def officer(request):
        requests.append(request)
        return _response(request)

    run = _run(tmp_path_factory.mktemp("synthetic-rich-street"), references=_references(),
               metrics_loader=own_metrics_loader, officer=officer)
    return run, requests


def test_nr_and_aggregate_reach_both_street_stages_and_real_review(rich_run):
    run, requests = rich_run
    assert run.result.completed, run.result.blocked_reasons
    assert run.canonical_live_result
    run.validate_canonical()
    stages = {trace.stage: trace for trace in run.result.stage_traces}
    assert len(stages) == 33  # Not a claim that every method-specific stage ran.
    for stage in ("STREET_REFERENCE_LOAD", "STREET_GAP_ANALYZER", "SAVE_STATE", "FINAL_REPORT"):
        assert stages[stage].status is StageStatus.PASS, stages[stage]
    # Existing normalized-multiple route has no single DCF to reverse; that
    # honest pre-existing WARNING must not be relabelled PASS for this test.
    assert stages["MARKET_COMPARE"].status is StageStatus.WARNING
    assert "broker_review_receipt" in stages["MARKET_COMPARE"].output_keys
    assert len(requests) == 1
    request = requests[0]
    assert request.gap.blocked_stage == "MARKET_COMPARE"
    binding = request.source_bindings[0]
    assert binding["freeze_token_hash"] == run.result.freeze_token.token_hash
    combined = binding["comparison_payload"]
    assert Decimal(str(combined["market_comparison"]["observation"]["price"])) == Decimal("61000")
    context = combined["frozen_model_context"]
    assert context["compiled_assumptions"]["assumptions"]
    assert context["hypotheses"] and context["red_team_proposal"] and context["evidence"]
    original = binding["references_payload"]["references"]
    assert original[0]["claims"][0]["summary"] == SOURCE_NOTE
    assert original[0]["claims"][0]["source_locator"].startswith("Synthetic exhibit 2")
    assert original[0]["access_quality"].startswith("Synthetic test source")
    assert original[1]["broker"] is None and original[1]["original_published_at"] is None
    assert original[1]["last_rating_at"] == "2026-08-14"
    assert original[1]["observed_at"] == OBSERVED_AT
    comparison = run.result.data["broker_comparison"]
    assert comparison.research_comparison_status == "COMPARED"
    assert comparison.metric_comparisons[0].status == "COMPARABLE"
    assert comparison.metric_comparisons[0].difference == Decimal("-40")
    assert comparison.target_comparisons[0].status == "NOT_APPLICABLE_NO_NUMERIC_TARGET"
    aggregate = comparison.target_comparisons[1]
    assert aggregate.status == "COMPARED_WITH_LIMITATIONS"
    assert aggregate.reference_as_of == OBSERVED_AT
    assert aggregate.reference_date_basis == "OBSERVED_AT"


def test_completed_rich_run_persists_review_comparison_and_report(rich_run):
    run, _ = rich_run
    assert run.result.completed, run.result.blocked_reasons
    directory = Path(run.result.data["saved_run_dir"])
    for name in ("broker_reference.json", "broker_compare.json", "broker_review.json",
                 "broker_revision_work_order.json", "execution_attestation.json", "final_report.md"):
        assert (directory / name).is_file(), name
    saved = json.loads((directory / "broker_compare.json").read_text())
    assert saved["comparison_hash"] == run.result.data["broker_comparison"].comparison_hash
    receipt = broker_review_receipt_from_dict(json.loads((directory / "broker_review.json").read_text()))
    receipt.validate()
    assert not receipt.value_commit_allowed and not receipt.same_run_mutation_allowed
    order = json.loads((directory / "broker_revision_work_order.json").read_text())
    assert order["new_canonical_run_required"] and len(order["tasks"]) == 1
    report = (directory / "final_report.md").read_text(encoding="utf-8")
    assert report == run.result.data["final_report"]
    assert "Synthetic NR Research" in report
    assert "숫자 목표가: 미제시·미확인" in report
    assert "Synthetic quote aggregator" in report
    assert "원문 투자의견: NR" in report
    assert "-40 십억원" in report
    assert "https://probe.invalid/broker/synthetic-nr" in report
    assert len(list(directory.glob("*.svg"))) == 2


def test_post_freeze_research_preserves_identical_baseline_values_and_sources(baseline, rich_run, baseline_artifact_hashes):
    run, _ = rich_run
    assert run.result.completed, run.result.blocked_reasons
    before, after = baseline.result, run.result
    for name in ("valuation_hash", "assumption_set_hash", "ledger_snapshot_hash", "audit_hash"):
        assert before.data[name] == after.data[name], name
    assert asdict(before.data["generic_valuation_result"]) == asdict(after.data["generic_valuation_result"])
    assert before.data["compiled_assumption_set"] == after.data["compiled_assumption_set"]
    # Each factory snapshots a fresh synthetic industry registry file, so the
    # source snapshot/token identity may differ; financial lineage must not.
    for name in ("ledger_snapshot_hash", "assumption_set_hash", "valuation_hash", "audit_hash"):
        assert getattr(before.freeze_token, name) == getattr(after.freeze_token, name)
    assert after.data["generic_valuation_result"].scenarios[0].value_per_share != Decimal("9000")
    assert after.data["generic_valuation_result"].expected_value_per_share is None
    for key in ("valuation.json", "compiled_assumptions.json"):
        assert (Path(before.data["saved_run_dir"]) / key).read_bytes() == (
            Path(after.data["saved_run_dir"]) / key).read_bytes()
    assert all(sha256(path.read_bytes()).hexdigest() == digest
               for path, digest in baseline_artifact_hashes.items())


def test_missing_officer_requires_recovery_not_silent_comparison_completion(tmp_path, own_metrics_loader):
    run = _run(tmp_path, references=_references(), metrics_loader=own_metrics_loader)
    assert not run.result.completed
    stages = {trace.stage: trace for trace in run.result.stage_traces}
    assert stages["STREET_REFERENCE_LOAD"].status is StageStatus.PASS
    assert stages["STREET_GAP_ANALYZER"].status is StageStatus.PASS
    assert stages["MARKET_COMPARE"].status is StageStatus.RECOVERY_REQUIRED
    assert "SAVE_STATE" not in stages
    assert run.result.data["broker_review_request"]["gap"]["blocked_stage"] == "MARKET_COMPARE"
    assert "saved_run_dir" not in run.result.data
    assert not list(tmp_path.rglob("final_report.md"))


def test_mutated_source_binding_cannot_be_approved_with_rehashed_typed_response(tmp_path, own_metrics_loader):
    def tampering_officer(request):
        # A valid-shaped response with the now-recomputed request hash still
        # cannot authorize mutation of the source binding's retained input hash.
        request.source_bindings[0]["references_payload"]["references"][0]["claims"][0]["summary"] = "Tampered forecast rationale"
        return _response(request)

    run = _run(tmp_path, references=_references(), metrics_loader=own_metrics_loader,
               officer=tampering_officer)
    assert not run.result.completed
    stages = {trace.stage: trace for trace in run.result.stage_traces}
    assert stages["MARKET_COMPARE"].status is StageStatus.BLOCKED
    assert "binding mismatch" in stages["MARKET_COMPARE"].rationale
    assert "broker_review_receipt" not in run.result.data
    assert not list(tmp_path.rglob("final_report.md"))


def test_nr_only_target_na_does_not_skip_forecast_computation(tmp_path, own_metrics_loader):
    run = _run(tmp_path, references=_references(aggregate=False),
               metrics_loader=own_metrics_loader, officer=_response)
    assert run.result.completed, run.result.blocked_reasons
    comparison = run.result.data["broker_comparison"]
    assert comparison.numeric_target_status == "NOT_APPLICABLE_NO_NUMERIC_TARGET"
    assert comparison.research_comparison_status == "COMPARED"
    assert comparison.metric_comparisons[0].difference == Decimal("-40")
    stages = {trace.stage: trace for trace in run.result.stage_traces}
    assert stages["STREET_REFERENCE_LOAD"].status is StageStatus.PASS
    assert stages["STREET_GAP_ANALYZER"].status is StageStatus.PASS
    report = run.result.data["final_report"]
    assert "숫자 목표가: 미제시·미확인" in report
    assert "-40 십억원" in report
    assert "비교 가능한 증권사 자료를 확보하지 못했습니다" not in report
