from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from valuation_engine.investor_report import (
    _broker_number,
    append_report_detail_links,
    InvestorReportBrief,
    compact_investor_report,
    investor_report_brief_from_dict,
    load_investor_report_profile,
    render_broker_review_status,
    render_broker_review_summary,
    render_investor_report,
    render_investor_report_documents,
)
from valuation_engine.records import MarketObservation
from valuation_engine.sotp import ScenarioEquityAggregation
from valuation_engine.valuation_execution import (
    GenericValuationResult,
    IntrinsicValuationScope,
    ScenarioPerShareValue,
    UnvaluedSegment,
)


ROOT = Path(__file__).resolve().parents[1]


def profile(run="koreazinc-010130"):
    return load_investor_report_profile(ROOT / "runs" / run / "declarations/investor_report.yaml")


def data():
    values = (("Down", "-20"), ("Base", "100"), ("Bull", "150"))
    valuation = GenericValuationResult(
        scenarios=tuple(
            ScenarioPerShareValue(name, Decimal(value) * 10, "KRW", Decimal(10),
                                  Decimal(value), name, (name,))
            for name, value in values
        ),
        equity_aggregation=ScenarioEquityAggregation((), None, False),
        expected_value_per_share=None, reporting_unit="KRW", valuation_hash="test-value",
    )
    return {
        "company": "합성 검토 회사",
        "generic_valuation_result": valuation,
        "market_observation": MarketObservation(200, "2026-10-26T13:06:46+09:00", "https://example.org/quote"),
        "bound_scenario_set": SimpleNamespace(scenarios=(), numeric_weighting_allowed=False),
    }


@pytest.mark.parametrize("run", ["koreazinc-010130", "daehansteel-084010", "kisco-104700"])
def test_shared_package_keeps_finance_sources_and_conditions_for_different_profiles(run):
    inputs, config = data(), profile(run)
    original_valuation = inputs["generic_valuation_result"]
    documents = render_investor_report_documents(inputs, config, detail_filename="상세_근거.md")
    assert documents.detail_report == append_report_detail_links(render_investor_report(inputs, config), has_broker_review=False)
    assert documents.main_report == compact_investor_report(documents.detail_report, detail_filename="상세_근거.md")
    assert len(documents.main_report) < len(documents.detail_report)
    assert config.conclusion in documents.main_report
    assert config.valuation_exclusions in documents.main_report
    assert config.major_assumptions in documents.main_report
    assert all(risk in documents.main_report for risk in config.risks)
    assert config.actionable_condition in documents.main_report
    assert "2026-10-26T13:06:46+09:00" in documents.main_report
    assert "**Down · 0원**" in documents.main_report
    assert "**Base · 100원**" in documents.main_report
    assert "**Bull · 150원**" in documents.main_report
    assert "보정된 시나리오 확률과 확률가중 기대값이 없습니다" in documents.main_report
    assert "음수 잔여가치" in documents.main_report
    assert "[상세 근거·계산·원문 전체](상세_근거.md)" in documents.main_report
    assert all(url in documents.detail_report for _, _, url in config.sources)
    assert inputs["generic_valuation_result"] is original_valuation


def test_long_late_caveats_are_never_cut_or_reclassified():
    config = replace(
        profile(),
        conclusion=("근거를 개별 검토한 informed 재평가입니다. " * 25)
        + "현재 거래주식과 조건부 납입·희석 이후 권리는 다릅니다.",
        valuation_exclusions="미산정인 신사업은 가치가 0이라는 뜻이 아닙니다. " * 15,
    )
    docs = render_investor_report_documents(data(), config, detail_filename="details.md")
    assert config.conclusion in docs.main_report
    assert config.valuation_exclusions in docs.main_report
    assert "권리는 다릅니다." in docs.main_report
    assert "…" not in docs.main_report


def test_sole_material_condition_in_assumptions_or_point_falsifier_survives():
    original = profile()
    title, content, evidence, link, _ = original.investment_points[0]
    config = replace(
        original,
        major_assumptions="제시값은 신주 납입 이후 권리이며 납입일 이전 주식과 직접 비교하지 않습니다.",
        investment_points=((title, content, evidence, link,
            "독점계약이 해지되면 사업가치의 전제를 재검토해야 합니다."),),
    )
    docs = render_investor_report_documents(data(), config, detail_filename="details.md")
    assert config.major_assumptions in docs.main_report
    assert config.investment_points[0][4] in docs.main_report


def test_partial_scope_and_named_unvalued_business_stay_on_main():
    inputs = data()
    inputs["generic_valuation_result"] = replace(
        inputs["generic_valuation_result"], scope=IntrinsicValuationScope.PARTIAL_INTRINSIC,
        unvalued_segments=(UnvaluedSegment("other", "기타부문", "INPUT_GAP", "분리 가능한 현금흐름이 미확인입니다."),),
    )
    docs = render_investor_report_documents(inputs, profile(), detail_filename="details.md")
    assert "부분 사업가치 평가" in docs.main_report
    assert "미평가 사업부는 0원으로 처리하지 않았습니다" in docs.main_report
    assert "기타부문" in docs.main_report and "분리 가능한 현금흐름이 미확인입니다" in docs.main_report
    assert "**Down · -20원**" in docs.main_report
    assert "전체 기업가치가 아니라" in docs.main_report


def test_broker_bulk_moves_whole_to_detail_not_repeated_in_main():
    from tests.test_broker_investor_report import comparison, review_receipt

    result = comparison()
    inputs = {**data(), "broker_comparison": result,
              "broker_review_receipt": review_receipt(result, "비교 기준을 확인한 뒤 추가 조사 여부를 판단합니다.")}
    docs = render_investor_report_documents(inputs, profile(), detail_filename="details.md")
    assert "NR 원문 1건 근거 비교" in docs.main_report
    assert "조건부 가설로 재조사 1건" in docs.main_report
    assert "원 보고서 발행일: 2026-05-14" in docs.detail_report
    assert "원 보고서 발행일:" not in docs.main_report
    assert "https://example.com/report.pdf" in docs.detail_report
    assert "동일기준 차이" in docs.detail_report
    assert "| 지표·기간 | 증권사 제시값 | 동결 모형 | 동일기준 차이 |" not in docs.main_report
    assert result.comparison_hash == inputs["broker_comparison"].comparison_hash


def test_distributional_route_uses_same_document_contract_without_probability_promotion():
    from tests.test_canonical_distributional_runtime import _ambiguity_spec
    from valuation_engine.distributional_runtime import execute_distributional_apv
    from valuation_engine.investor_report import render_distributional_investor_report

    frozen = execute_distributional_apv(_ambiguity_spec())
    inputs = {"company": "합성 분포 회사", "distributional_primary_result": frozen}
    config = profile("korean-air-003490")
    docs = render_investor_report_documents(inputs, config, detail_filename="details.md")
    assert docs.detail_report == append_report_detail_links(render_distributional_investor_report(inputs, config), has_broker_review=False)
    assert "허용 가정 조합 범위" in docs.main_report
    assert "단일 확률가중 목표가: 미산출" in docs.main_report
    assert "보정 성공확률: 미산출" in docs.main_report
    assert "보수적 진입 상한" in docs.main_report


@pytest.mark.parametrize("filename", ["../details.md", "https://example.org/details.md", "a b.md", "[x].md", "x.md\nspoof"])
def test_detail_links_cannot_escape_or_inject(filename):
    with pytest.raises(ValueError, match="safe local"):
        compact_investor_report(render_investor_report(data(), profile()), detail_filename=filename)


def test_unknown_report_shape_fails_instead_of_dropping_new_content():
    full = render_investor_report(data(), profile())
    for changed in (full + "\n## 8. 새 주요 제약\n", full.replace("## 4. 사업부별 평가", "## 4. 알 수 없는 내용")):
        with pytest.raises(ValueError, match="seven-section"):
            compact_investor_report(changed, detail_filename="details.md")
    with pytest.raises(ValueError, match="all three"):
        compact_investor_report(full.replace("| Bull |", "| 다른 경로 |"), detail_filename="details.md")


def test_summary_budget_preserves_complete_sentence_after_html_escaping():
    from tests.test_broker_investor_report import comparison, review_receipt

    receipt = review_receipt(comparison(), "S&M 비용과 <원문> 설명을 검토합니다. " * 80)
    rendered = render_broker_review_summary(receipt, max_chars=500)
    assert len(rendered) <= 500
    assert rendered.endswith("필요합니다.")
    assert "조건부 가설로 재조사" in rendered
    assert "나머지 개별 판단" in rendered
    assert "…" not in rendered
    assert render_broker_review_status(receipt) == "조건부 가설로 재조사 1건."


def test_metric_display_rounding_is_not_false_zero_or_financial_mutation():
    number = Decimal("-25.6596188382294549250660076180225174434")
    assert _broker_number(number) == "-25.6596"
    assert number == Decimal("-25.6596188382294549250660076180225174434")
    assert _broker_number("0.00000001") == "0.00000001"
    assert _broker_number("-0.00000001") == "-0.00000001"
    assert _broker_number("0.0011") == "0.0011"


def brief_for(full):
    return InvestorReportBrief(
        source_report_sha256=sha256(full.encode()).hexdigest(),
        conclusion="원자료를 검토한 informed 조건부 재평가이며 확정된 투자 결과가 아닙니다.",
        key_assumptions=("수주와 비용을 함께 반영하되 기존 할인 조건을 유지했습니다.",),
        key_evidence=("기존 공시와 후속 사업 자료를 같은 사업 범위로 대사했습니다.",),
        change_reason="사업 출발값의 범위 교정을 반영했고 가격을 맞추기 위해 수정하지 않았습니다.",
        risks=("추가 인력과 자금조달 조건에 따라 결과가 달라질 수 있습니다.",),
        scope_limits=("현재 거래주식과 납입·희석 이후 권리는 다르며 미산정 신사업은 가치 0이 아닙니다.",),
    )


def test_source_bound_host_brief_preserves_frozen_numbers_and_price_time():
    inputs, config = data(), profile()
    full = append_report_detail_links(render_investor_report(inputs, config), has_broker_review=False)
    brief = brief_for(full)
    documents = render_investor_report_documents(
        inputs, replace(config, decision_brief=brief), detail_filename="details.md",
    )
    assert documents.detail_report == full
    assert documents.main_report == compact_investor_report(full, detail_filename="details.md", brief=brief)
    assert "**Down · 0원**" in documents.main_report
    assert "**Base · 100원**" in documents.main_report
    assert "**Bull · 150원**" in documents.main_report
    assert "2026-10-26T13:06:46+09:00" in documents.main_report
    assert "시나리오 확률·확률가중 기대값 미산정" in documents.main_report
    assert "부채 차감 후 잔여가치: Down -20원" in documents.main_report
    assert "현재 거래주식과 납입·희석 이후 권리는 다르며" in documents.main_report
    assert "원자료를 검토한 informed" in documents.main_report
    assert len(documents.main_report) < len(compact_investor_report(full, detail_filename="details.md"))


def test_stale_brief_and_financial_override_fields_fail_closed():
    from dataclasses import asdict

    full = render_investor_report(data(), profile())
    brief = brief_for(full)
    with pytest.raises(ValueError, match="stale"):
        compact_investor_report(full + "\n", detail_filename="details.md", brief=brief)
    with pytest.raises(ValueError, match="financial override"):
        investor_report_brief_from_dict({**asdict(brief), "scenario_values": {"Base": 999999}})
    with pytest.raises(ValueError, match="complete single-line"):
        investor_report_brief_from_dict({**asdict(brief), "conclusion": "문장 중간"})
    assert investor_report_brief_from_dict(asdict(brief)) == brief


def test_brief_cannot_drop_partial_scope_or_unvalued_business():
    inputs = data()
    inputs["generic_valuation_result"] = replace(
        inputs["generic_valuation_result"], scope=IntrinsicValuationScope.PARTIAL_INTRINSIC,
        unvalued_segments=(UnvaluedSegment("other", "기타부문", "INPUT_GAP", "현금흐름 자료 미확인입니다."),),
    )
    full = render_investor_report(inputs, profile())
    main = compact_investor_report(full, detail_filename="details.md", brief=brief_for(full))
    assert "부분 사업가치 평가" in main
    assert "전체 기업가치가 아니라" in main
    assert "기타부문" in main and "현금흐름 자료 미확인입니다" in main
    assert "**Down · -20원**" in main


def test_optional_profile_brief_loads_without_changing_detailed_report(tmp_path):
    import yaml
    from dataclasses import asdict

    original_path = ROOT / "runs/koreazinc-010130/declarations/investor_report.yaml"
    payload = yaml.safe_load(original_path.read_text())
    original = profile()
    full = append_report_detail_links(render_investor_report(data(), original), has_broker_review=False)
    payload["decision_brief"] = asdict(brief_for(full))
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True))
    loaded = load_investor_report_profile(path)
    assert loaded.decision_brief == brief_for(full)
    assert append_report_detail_links(render_investor_report(data(), loaded), has_broker_review=False) == full


def test_brief_uses_value_table_not_same_named_sensitivity_rows():
    full = render_investor_report(data(), profile())
    full = full.replace(
        "### 계산된 가치 민감도\n",
        "### 계산된 가치 민감도\n\n| 시나리오 | 대상 | 변수 변화 | 주당가치 영향 |\n"
        "|---|---|---:|---:|\n| Down | 전체 | WACC | 9% |\n"
        "| Base | 전체 | WACC | 5% |\n| Bull | 전체 | WACC | 3% |\n",
    )
    compact = compact_investor_report(full, detail_filename="details.md", brief=brief_for(full))
    assert compact.count("**Down ·") == 1
    assert "**Down · 0원**" in compact
    assert "WACC | 9%" not in compact


def test_detail_record_links_are_explicit_and_idempotent():
    original = render_investor_report(data(), profile())
    linked = append_report_detail_links(original, has_broker_review=True)
    assert linked.startswith(original.rstrip())
    assert append_report_detail_links(linked, has_broker_review=True) == linked
    for filename in ("final_report.md", "compiled_assumptions.json", "control_plane_trace.json", "broker_review.json"):
        assert f"]({filename})" in linked
    no_review = append_report_detail_links(original, has_broker_review=False)
    assert "](broker_review.json)" not in no_review
    # The editorial binding includes all detail records' links, not the earlier
    # superficially similar report text.
    with pytest.raises(ValueError, match="stale"):
        compact_investor_report(linked, detail_filename="details.md", brief=brief_for(original))


def test_semantic_brief_keeps_short_probability_status_not_market_performance_inference():
    from hashlib import sha256
    from valuation_engine.investor_report import InvestorReportBrief, compact_investor_report
    detailed = render_investor_report(data(), profile())
    brief = InvestorReportBrief(sha256(detailed.encode()).hexdigest(), '조건부 권리에 관한 결론입니다.', ('추가 납입이 필요한 조건입니다.',), ('원공시 근거를 확인했습니다.',), '표시만 간결하게 바꿉니다.', ('계약 실패 위험이 있습니다.',), ('확률가중 매수가가 아닙니다.',))
    main = compact_investor_report(detailed, detail_filename='detail.md', brief=brief)
    assert '비교 시세(관측시각): 200원 (2026-10-26T13:06:46+09:00)' in main
    assert main.count('시나리오 확률·확률가중 기대값 미산정') == 1
    assert '넘어서는 실적이 필요' not in main
    assert '넘어서는 실적이 필요' in detailed
