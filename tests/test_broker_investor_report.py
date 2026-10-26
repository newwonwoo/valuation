from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from valuation_engine.broker_comparison import (
    BrokerClaim, BrokerClaimType, BrokerMetric, BrokerMetricBasis,
    BrokerReference, BrokerReferenceBundle, FrozenOperatingMetric,
    build_broker_comparison,
)
from valuation_engine.broker_judgment import BrokerClaimReview, BrokerReviewReceipt
from valuation_engine.host_judgment import judgment_hash
from valuation_engine.investor_report import (
    _street_comparison_lines, _supplemental_source_lines, _validate_public_report,
    broker_reference_summary,
)
from valuation_engine.source_reporting import (
    broker_reference_source_links, build_source_link_index,
)
from valuation_engine.sotp import ScenarioEquityAggregation
from valuation_engine.valuation_execution import GenericValuationResult, ScenarioPerShareValue


def basis(**changes):
    fields = dict(metric="operating_profit", period_start="2027-01-01",
                  period_end="2027-12-31", frequency="ANNUAL", scope="CONSOLIDATED",
                  perimeter="GROUP_INCLUDING_SUBSIDIARIES", accounting_basis="IFRS_REPORTED",
                  unit="KRW_billion", currency="KRW")
    return BrokerMetricBasis(**(fields | changes))


def reference(**changes):
    metric = BrokerMetric("broker-op", basis(), value="3.3", source_locator="PDF p2")
    fields = dict(reference_id="report-1", reference_kind="NAMED_REPORT",
                  provider_name="키움 공개자료", broker="키움증권", rating="NR",
                  source_ref="https://example.com/report.pdf", content_sha256="a" * 64,
                  authorization_basis="public_access", observed_at="2026-10-26T05:00:00Z",
                  original_published_at="2026-05-14",
                  claims=(BrokerClaim("claim-op", BrokerClaimType.BROKER_FORECAST,
                                      "2027년 영업이익 전망이며 실현 실적이 아닙니다.", metric,
                                      source_locator="PDF p2"),))
    return BrokerReference(**(fields | changes))


def aggregate(**changes):
    fields = dict(reference_id="aggregate-1", reference_kind="AGGREGATE_SNAPSHOT",
                  provider_name="Investing", broker=None, rating="SELL",
                  source_ref="https://example.com/aggregate", content_sha256="c" * 64,
                  authorization_basis="public_access", observed_at="2026-10-26T05:00:00Z",
                  original_published_at=None, last_rating_at="2026-08-14",
                  target_price="9000", target_currency="KRW", aggregate_report_count=1,
                  aggregate_median_target_price="9000", aggregate_min_target_price="9000",
                  aggregate_max_target_price="9000")
    return BrokerReference(**(fields | changes))


def bundle(*refs):
    return BrokerReferenceBundle("broker-reference-bundle/v1", "KR:TEST",
                                 "2026-10-26T05:30:00Z", refs or (reference(),))


def own_metric(**changes):
    return FrozenOperatingMetric(
        metric=BrokerMetric("own-op", basis(**changes), value="-14", source_locator="frozen model"),
        scenario_id="Base", artifact_ref="operating_cases.json", artifact_sha256="b" * 64,
        json_pointer="/Base/2027/operating_profit", freeze_token_hash="f" * 64,
        valuation_hash="VALUE",
    )


def comparison(refs=None, metrics=None):
    valuation = GenericValuationResult(
        scenarios=tuple(ScenarioPerShareValue(name, Decimal(v) * 10, "KRW", Decimal(10),
                                              Decimal(v), name, (name,))
                        for name, v in (("Down", "-20"), ("Base", "100"), ("Bull", "150"))),
        equity_aggregation=ScenarioEquityAggregation((), None, False),
        expected_value_per_share=None, reporting_unit="KRW", valuation_hash="VALUE")
    return build_broker_comparison(refs or bundle(),
        frozen_metrics=(own_metric(),) if metrics is None else metrics,
        valuation=valuation, freeze_token_hash="f" * 64, intrinsic_as_of="2026-10-26")


def rendered(result, **data):
    return "\n".join(_street_comparison_lines({"broker_comparison": result, **data}))


def review_receipt(result, rationale):
    decision = BrokerClaimReview(
        claim_id="claim-op", reference_id="report-1", claim_type="BROKER_FORECAST",
        reference_kind="NAMED_REPORT", disposition="CONDITIONAL_HYPOTHESIS",
        rationale=rationale, evidence_refs=("report-1",), support_relation="UNKNOWN",
        basis_status="UNKNOWN", uncertainty="계약과 원가의 추가 검증이 필요합니다.",
        missing_evidence=("계약 전환율",), research_question="원가와 계약이 시사하는 범위는?",
        proposed_metric="operating_profit", independent_derivation_plan="원가와 계약을 따로 검증합니다.",
        falsifier="계약 전환 지연", normalization_requirements=("회계 기준",),
        underlying_source_families=(), source_ref="https://example.com/report.pdf",
        original_published_at="2026-05-14", reference_content_sha256="a" * 64,
        claim_payload_hash="d" * 64, claim_basis_json="{}", source_dependence="UNKNOWN",
        impact_scope="operating profit", assumption_candidate=None,
        new_evidence_refs=(), interpretation_correction_json="null",
    )
    fields = dict(schema_version="broker-review-receipt/v1", request_hash="1" * 64,
                  response_hash="2" * 64, run_id="test-frozen", target_id=result.target_id,
                  intrinsic_as_of=result.intrinsic_as_of, comparison_as_of=result.comparison_as_of,
                  freeze_token_hash=result.freeze_token_hash, reference_hash=result.reference_hash,
                  comparison_hash=result.comparison_hash, decision="REVIEW_CLAIMS",
                  decisions=(decision,), rationale=rationale, uncertainty="추가 확인",
                  counterevidence_json="[]", source_families=(),
                  authority="POST_FREEZE_RESEARCH_PROPOSAL_ONLY",
                  same_run_mutation_allowed=False, value_commit_allowed=False)
    provisional = BrokerReviewReceipt(**fields, receipt_hash="")
    payload = asdict(provisional)
    payload.pop("receipt_hash")
    return replace(provisional, receipt_hash=judgment_hash(payload))


def test_nr_numeric_forecast_comparison_is_not_replaced_by_target_na():
    text = rendered(comparison())
    assert "숫자 목표가: 미제시·미확인" in text
    assert "원문 투자의견: NR" in text
    assert "영업이익 · 2027-01-01~2027-12-31 · 연간" in text
    assert "| 3.3 십억원 | Base · -14 십억원 | -17.3 십억원 |" in text
    assert "회계 기준: 공시 IFRS" in text
    assert "비교 가능한 증권사 자료를 확보하지 못했습니다" not in text
    assert "목표가 0" not in text
    assert "2026-05-14 · 자료 확인시각: 2026-10-26T05:00:00Z" in text
    _validate_public_report(text)


def test_basis_mismatch_cannot_render_a_false_delta_even_if_field_is_present():
    result = comparison(metrics=(own_metric(accounting_basis="LEASE_ADJUSTED_WITH_ECONOMIC_SBC"),))
    row = replace(result.metric_comparisons[0], difference=Decimal("123456.789"))
    text = rendered(replace(result, metric_comparisons=(row,)))
    assert "기준 차이·미확인으로 미산출" in text
    assert "회계 기준" in text
    assert "123,456.789" not in text
    assert "-17.3 십억원" not in text
    assert "3.3 십억원" in text and "-14 십억원" in text


def test_interval_comparison_never_invents_midpoint():
    ref = reference()
    claim = ref.claims[0]
    metric = replace(claim.metric, value=None, low="35", high="50")
    result = comparison(bundle(replace(ref, claims=(replace(claim, metric=metric),))))
    text = rendered(result)
    assert "35 십억원 ~ 50 십억원" in text
    assert "-64 십억원 ~ -49 십억원" in text
    assert "42.5" not in text


def test_aggregate_preserves_unknown_original_date_and_conditional_math():
    result = comparison(bundle(aggregate()), metrics=())
    text = rendered(result)
    assert "#### Investing 공개 집계" in text
    assert "원 증권사 미확인" in text
    assert "원 보고서 발행일: 미확인 · 자료 확인시각: 2026-10-26T05:00:00Z" in text
    assert "마지막 투자의견 시점: 2026-08-14" in text
    assert "원 보고서 발행일: 2026-08-14" not in text
    assert "원 보고서 발행일: 2026-10-26" not in text
    assert "공개 집계 목표가: 9,000 원" in text
    assert "집계 표본: 1건" in text
    assert "평가방법·기준연도: 미확인" in text
    assert "주식 수·자본변동 조정 기준이 미확인" in text
    assert "Base: -8,900 원 (-98.9%)" in text
    assert "Down: -9,000 원 (-100.0%)" in text
    assert "동결 잔여가치 -20 원 그대로 보존" in text
    assert "비교용 값만 0 원" in text
    assert "확률가중 목표가 차이는 산출하지 않았습니다" in text


def test_broker_source_links_are_collected_without_evidence_and_deduplicated():
    second = reference(reference_id="report-2", broker="두번째 증권사",
                       source_ref="https://example.com/report.pdf#p2",
                       claims=(replace(reference().claims[0], claim_id="claim-2"),))
    data = {"broker_reference_bundle": bundle(reference(), second, aggregate())}
    links = broker_reference_source_links(data)
    assert len(links) == 2
    assert len(build_source_link_index(data, require_all_http=False)) == 2
    pdf = next(x for x in links if x.url.endswith("report.pdf"))
    assert pdf.labels == ("증권사: 두번째 증권사", "증권사: 키움증권")
    agg = next(x for x in links if x.url.endswith("aggregate"))
    assert "원 보고서 발행일 미확인" in agg.coverage[0]
    lines = _supplemental_source_lines(data, linked={"https://example.com/report.pdf"})
    assert len(lines) == 1 and "공개 집계: Investing" in lines[0]


@pytest.mark.parametrize("url", ["file:///report.pdf", "https://user:pass@example.com/report",
                                 "https://example.com/report?api_key=SECRET"])
def test_broker_source_helper_rejects_private_or_credential_links(url):
    data = {"broker_reference_bundle": SimpleNamespace(references=(SimpleNamespace(source_ref=url),))}
    with pytest.raises(ValueError, match="credential-free"):
        broker_reference_source_links(data)


def test_review_summary_is_separate_bounded_and_directionally_neutral():
    result = comparison()
    receipt = review_receipt(result, "계약 전환 및 비용 구조를 재검토해야 합니다. " * 100)
    text = rendered(result, broker_review_receipt=receipt)
    computation, interpretation = text.split("### 괴리 해석·재검토\n")
    assert "-17.3 십억원" in computation
    assert "조건부 가설로 재조사" in interpretation
    assert "현재 동결가치·가정은 변경하지 않았습니다" in interpretation
    assert "상향·하향·불변" in interpretation
    assert len(interpretation) <= 1000
    _validate_public_report(text)


def test_source_text_cannot_inject_html_or_table_structure():
    ref = reference()
    malicious = replace(ref.claims[0], summary="<script>alert(1)</script> | fake row\n## bogus [click](https://example.org/fake)")
    text = rendered(comparison(bundle(replace(ref, claims=(malicious,)))))
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "\\| fake row ## bogus" in text
    assert "\n## bogus" not in text
    assert "[click](" not in text


def test_legacy_fallback_and_new_typed_contract():
    assert _street_comparison_lines({}) == ["- 비교 가능한 증권사 자료를 확보하지 못했습니다."]
    with pytest.raises(ValueError, match="typed result"):
        _street_comparison_lines({"broker_comparison": {"status": "COMPARED"}})
    with pytest.raises(ValueError, match="typed review receipt"):
        rendered(comparison(), broker_review_receipt={"summary": "승인"})


def test_review_from_a_different_frozen_run_cannot_be_displayed():
    result = comparison()
    receipt = replace(review_receipt(result, "재검토"), freeze_token_hash="0" * 64)
    with pytest.raises(ValueError, match="frozen comparison"):
        rendered(result, broker_review_receipt=receipt)


def test_tiny_reported_metric_is_not_rounded_into_a_fabricated_zero():
    ref = reference()
    claim = ref.claims[0]
    metric = replace(claim.metric, value="0.00000001")
    text = rendered(comparison(bundle(replace(ref, claims=(replace(claim, metric=metric),)))))
    assert "0.00000001 십억원" in text


def test_unknown_basis_and_metadata_only_references_remain_honest_references():
    result = comparison(metrics=(own_metric(accounting_basis="UNKNOWN"),))
    text = rendered(result)
    assert "기준 차이·미확인으로 미산출" in text
    assert "회계 기준: 미확인" in text
    assert "3.3 십억원" in text and "-14 십억원" in text
    metadata = rendered(comparison(bundle(reference(claims=())), metrics=()))
    assert "실적·논리 항목이 구조화되지 않은 자료" in metadata
    assert "원문 투자의견: NR" in metadata


def test_distributional_target_gap_direction_and_no_fabricated_mean():
    from tests.test_canonical_distributional_runtime import _ambiguity_spec
    from valuation_engine.distributional_runtime import execute_distributional_apv
    from valuation_engine.investor_report import _broker_quantity

    frozen = execute_distributional_apv(_ambiguity_spec())
    result = build_broker_comparison(bundle(aggregate()), valuation=frozen,
        freeze_token_hash="f" * 64, intrinsic_as_of="2026-10-26")
    row = result.target_comparisons[0]
    text = rendered(result)
    assert "목표가 − 동결 범위 하단: " + _broker_quantity(row.target_gap_to_low, "KRW") in text
    assert "목표가 − 동결 범위 상단: " + _broker_quantity(row.target_gap_to_high, "KRW") in text
    assert "단일 확률가중 값으로 바꾸지 않았습니다" in text
    assert "확률가중 차이:" not in text


def test_complete_distributional_report_preserves_headline_and_adds_original_sources():
    from tests.test_canonical_distributional_runtime import _ambiguity_spec
    from valuation_engine.distributional_runtime import execute_distributional_apv
    from valuation_engine.investor_report import (
        load_investor_report_profile, render_distributional_investor_report,
    )

    frozen = execute_distributional_apv(_ambiguity_spec())
    refs = bundle(reference(), aggregate())
    result = build_broker_comparison(refs, valuation=frozen,
        freeze_token_hash="f" * 64, intrinsic_as_of="2026-10-26")
    profile = load_investor_report_profile(Path(__file__).resolve().parents[1]
        / "runs/korean-air-003490/declarations/investor_report.yaml")
    data = {"company": "테스트 회사", "distributional_primary_result": frozen,
            "street_comparison": SimpleNamespace(mean_target_price=Decimal(700),
                min_target_price=Decimal(600), max_target_price=Decimal(800), report_count=2)}
    old = render_distributional_investor_report(data, profile)
    new = render_distributional_investor_report({**data, "broker_comparison": result,
        "broker_reference_bundle": refs}, profile)
    new_headline = "\n".join(line for line in new.split("## 6.")[0].splitlines()
                             if not line.startswith("- 증권사 참고자료:")) + "\n"
    assert old.split("## 6.")[0] == new_headline
    assert "- 증권사 참고자료: 확인된 개별 목표가 없음 · NR 원문 1건 근거 비교" in new
    assert "Investing 공개 집계 9,000원(표본 1건, 기준 미확인·조건부 참고)" in new
    assert "증권사 목표가 평균 700원" in old
    assert "증권사 목표가 평균 700원" not in new
    assert "#### 키움증권" in new and "#### Investing 공개 집계" in new
    sources = new.split("## 7. 원문 자료")[1]
    assert "https://example.com/report.pdf" in sources
    assert "https://example.com/aggregate" in sources
    assert new.count("\n## ") == old.count("\n## ")


def test_summary_distinguishes_nr_only_aggregate_and_legacy_availability():
    assert broker_reference_summary({}) is None
    nr_only = broker_reference_summary({"broker_comparison": comparison()})
    assert nr_only == "확인된 개별 목표가 없음 · NR 원문 1건 근거 비교"
    agg = broker_reference_summary({"broker_comparison": comparison(bundle(aggregate()), metrics=())})
    assert "Investing 공개 집계 9,000원(표본 1건, 기준 미확인·조건부 참고)" in agg
    assert "증권사 1" not in agg and "평균" not in agg and "미확보" not in agg
    metadata = broker_reference_summary({"broker_comparison": comparison(bundle(reference(claims=())), metrics=())})
    assert "NR 원문 1건 확인(메타데이터)" in metadata
    not_nr = broker_reference_summary({"broker_comparison": comparison(bundle(reference(rating=None)))})
    assert "NR" not in not_nr and "목표가 없는 원문 1건 확인" in not_nr


def test_summary_does_not_count_nr_as_absent_target_or_multiple_independent_brokers():
    first = reference(target_price="90", target_currency="KRW")
    second = reference(reference_id="second-vintage", claims=(replace(first.claims[0], claim_id="other-claim"),))
    text = broker_reference_summary({"broker_comparison": comparison(bundle(first, second))})
    assert "개별 목표가 원문 1건" in text
    assert "NR 원문 2건 근거 비교" in text
    assert "확인된 개별 목표가 없음" not in text
    assert "증권사 2" not in text


def test_generic_profile_summary_shows_rich_reference_availability_without_value_changes():
    from valuation_engine.investor_report import load_investor_report_profile, render_investor_report

    valuation = GenericValuationResult(
        scenarios=tuple(ScenarioPerShareValue(name, Decimal(v) * 10, "KRW", Decimal(10),
                                              Decimal(v), name, (name,))
                        for name, v in (("Down", "-20"), ("Base", "100"), ("Bull", "150"))),
        equity_aggregation=ScenarioEquityAggregation((), None, False),
        expected_value_per_share=None, reporting_unit="KRW", valuation_hash="VALUE")
    profile = load_investor_report_profile(Path(__file__).resolve().parents[1]
        / "runs/korean-air-003490/declarations/investor_report.yaml")
    data = {"company": "테스트 회사", "generic_valuation_result": valuation}
    old = render_investor_report(data, profile)
    new = render_investor_report({**data, "broker_comparison": comparison()}, profile)
    headline = new.split("## 2.")[0]
    assert "- 증권사 참고자료: 확인된 개별 목표가 없음 · NR 원문 1건 근거 비교" in headline
    assert "- 평가 기준가: 100원" in headline
    without_summary = "\n".join(line for line in new.split("## 6.")[0].splitlines()
                                 if not line.startswith("- 증권사 참고자료:")) + "\n"
    assert old.split("## 6.")[0] == without_summary
