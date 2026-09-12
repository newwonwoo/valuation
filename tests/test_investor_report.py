from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_kr_live import (  # noqa: E402
    RunbookError,
    execute_run,
    publish_report_bundle,
    reuse_published_report_bundle,
)
from valuation_engine.investor_report import (  # noqa: E402
    load_investor_report_profile,
    render_investor_report,
)
from valuation_engine.valuation_execution import (  # noqa: E402
    IntrinsicValuationScope,
    UnvaluedSegment,
)


RUN_DIR = ROOT / "runs" / "koreazinc-010130"
PROFILE_PATH = RUN_DIR / "declarations" / "investor_report.yaml"


@pytest.fixture(scope="module")
def koreazinc_result(tmp_path_factory):
    state_root = tmp_path_factory.mktemp("koreazinc-report") / "state"
    reached, stop_stage, stop_reason, result = execute_run(
        RUN_DIR,
        state_root=str(state_root),
    )
    assert stop_stage is None, stop_reason
    assert len(reached) == 33
    return result


def test_koreazinc_investor_report_is_clean_and_decision_ready(koreazinc_result):
    profile = load_investor_report_profile(PROFILE_PATH)
    report = render_investor_report(koreazinc_result.data, profile)

    headings = tuple(
        line for line in report.splitlines() if line.startswith("## ")
    )
    assert headings == (
        "## 1. 투자판단 요약",
        "## 2. 핵심 투자포인트",
        "## 3. 가치평가",
        "## 4. 사업부별 평가",
        "## 5. 리스크와 확인 필요 사항",
        "## 6. 판단 변경 조건",
        "## 7. 참고자료",
    )
    for expected in (
        "- 투자의견: 비중축소",
        "- 현재가: 1,222,000원 (2026-09-04)",
        "- 평가 기준가: 688,109원",
        "| 주당가치 | 304,347원 | 688,109원 | 1,129,698원 |",
        "확률가중 기대값은 948,269원입니다. 적용 확률은 하방 5.3%, 기준 31.2%, 상방 63.5%입니다.",
        "이전 평가 완료 사업부 소계 680,874원 → 수정 688,109원, 주당 7,235원 증가",
        "| 기타부문 | 평가 | 공시 유형자산 1,476억원을 집계 NAV로 반영 |",
    ):
        assert expected in report

    for forbidden in (
        "<details",
        "<summary",
        "33단계",
        "근거 ID",
        "operator declared",
        "인공지능 인사이트",
        "자동 오류 점검",
        "모듈 점검",
        "해시",
        "run_id",
        "DART_SEGMENT",
        "valuation_hash",
        "audit_hash",
    ):
        assert forbidden.casefold() not in report.casefold()


def test_partial_report_names_scope_and_does_not_zero_fill(koreazinc_result):
    valuation = koreazinc_result.data["generic_valuation_result"]
    partial = replace(
        valuation,
        scope=IntrinsicValuationScope.PARTIAL_INTRINSIC,
        unvalued_segments=(
            UnvaluedSegment(
                asset_id="recycling",
                segment_id="기타부문",
                resolution_status="ASSUMPTION_GAP",
                rationale="독립 현금흐름 자료가 부족합니다.",
            ),
        ),
    )
    data = {**koreazinc_result.data, "generic_valuation_result": partial}
    report = render_investor_report(
        data,
        load_investor_report_profile(PROFILE_PATH),
    )

    assert "- 투자의견: 판단 유보" in report
    assert "- 부분 내재가치: 688,109원" in report
    assert "전체 기업가치가 아니라 평가 완료 사업부 기준" in report
    assert "| 기타부문 | 미평가, 추가 확인 필요 |" in report
    assert "독립 현금흐름 자료가 부족합니다." in report
    assert "부분 평가이므로 현재가와의 상승여력은 비교하지 않았습니다." in report
    assert "| 기타부문 | 평가 |" not in report


def test_market_price_cannot_replace_missing_probability_weight(koreazinc_result):
    valuation = replace(
        koreazinc_result.data["generic_valuation_result"],
        expected_value_per_share=None,
    )
    report = render_investor_report(
        {**koreazinc_result.data, "generic_valuation_result": valuation},
        load_investor_report_profile(PROFILE_PATH),
    )

    assert "- 투자의견: 판단 유보" in report
    assert "보정된 시나리오 확률과 확률가중 기대값이 없습니다." in report
    assert "현재가는 확률 생성에 사용하지 않으며" in report


def test_publisher_exposes_clean_report_and_reuses_the_same_alias(
    koreazinc_result,
    tmp_path,
):
    output = tmp_path / "published"
    alias = tmp_path / "고려아연_투자보고서.md"
    published = publish_report_bundle(
        RUN_DIR,
        koreazinc_result,
        output_dir=output,
        report_alias=alias,
    )
    versioned = Path(published["versioned_report_path"])
    report = versioned.read_text(encoding="utf-8")

    assert versioned.name == "010130_20260904_투자보고서.md"
    assert alias.read_text(encoding="utf-8") == report
    assert published["artifact_id"] not in report

    reused_alias = tmp_path / "reused.md"
    reused = reuse_published_report_bundle(
        RUN_DIR,
        output_dir=output,
        report_alias=reused_alias,
    )
    assert reused is not None
    assert reused_alias.read_text(encoding="utf-8") == report


def test_publisher_refuses_to_fall_back_to_the_developer_report(
    koreazinc_result,
    tmp_path,
):
    run_copy = tmp_path / "run-without-public-profile"
    shutil.copytree(RUN_DIR, run_copy, ignore=shutil.ignore_patterns("out"))
    (run_copy / "declarations" / "investor_report.yaml").unlink()

    with pytest.raises(RunbookError, match="refusing to expose"):
        publish_report_bundle(
            run_copy,
            koreazinc_result,
            output_dir=tmp_path / "published-without-profile",
        )


@pytest.mark.parametrize(
    "run_name",
    (
        "daehansteel-084010",
        "kisco-104700",
        "koreazinc-010130",
        "shinhanalpha-293940",
    ),
)
def test_every_committed_run_declares_a_valid_public_report_profile(run_name):
    path = ROOT / "runs" / run_name / "declarations" / "investor_report.yaml"
    assert path.is_file()
    load_investor_report_profile(path)
