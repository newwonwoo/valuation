from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from valuation_engine.report_form import attest_controlled_run, render_controlled_run_report
from valuation_engine.sanil_live_primary import (
    load_sanil_market_snapshot,
    load_sanil_snapshot,
    run_sanil_live_primary,
)
from valuation_engine.visual_reporting import render_report_visuals


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT
    / "examples"
    / "report_forms"
    / "SANIL_062040_LIVE_PRIMARY_REPORT.md"
)
STREET_SOURCE_REF = "https://www.yna.co.kr/amp/view/AKR20260811028700008"


def render_report(state_root: Path) -> tuple[str, tuple]:
    snapshot = load_sanil_snapshot()
    result = run_sanil_live_primary(state_root)
    attestation = attest_controlled_run(result)
    if not attestation.passed:
        failures = tuple(
            item.check_id for item in attestation.checks if not item.passed
        )
        blockers = tuple(result.blocked_reasons)
        detail = ", ".join((*failures, *blockers)) or "unknown failure"
        raise RuntimeError(f"Sanil LIVE_PRIMARY report is not verified: {detail}")

    controlled = render_controlled_run_report(result)
    valuation = result.data["generic_valuation_result"]
    values = {
        item.scenario_id: item.value_per_share
        for item in valuation.scenarios
    }
    beta = result.data["live_beta_result"]
    wacc = result.data["live_wacc_result"]
    assessment = result.data["capacity_commitment_assessment"]
    market_snapshot = load_sanil_market_snapshot()
    market = result.data.get("market_comparison")
    street = result.data.get("street_comparison")
    current_price = (
        market.observation.price
        if market is not None
        else market_snapshot.price
    )
    street_target = (
        street.consensus.mean_target_price
        if street is not None
        else None
    )
    street_line = (
        f"- 증권사 참고 목표가(내재가치 고정 후 로드): **{float(street_target):,.0f}원**\n"
        if street_target is not None
        else "- 증권사 참고 목표가: **미확보**\n"
    )

    header = f"""# 산일전기(062040) PRISM 실데이터 통제 실행 보고서

- 데이터 기준일: **{snapshot.cutoff}**
- 검증 상태: **검증·고정 완료** (`VERIFIED_FROZEN`)
- 투자검토 상태: **출처 검증 기반 예비 분석**
- 현재가(내재가치 고정 후 로드): **{float(current_price):,.0f}원**
{street_line}- 하방 / 기준 / 상방: **{values['Down']:,.0f}원 / {values['Core']:,.0f}원 / {values['Bull']:,.0f}원**
- 계층형 베타: **{beta.target_levered_beta:.3f}**
- 가중평균자본비용: **{wacc.wacc_result.wacc:.3%}**
- 기준 시나리오 반영 생산능력 프로젝트: **{', '.join(assessment.core_inclusion_required_projects)}**

## PM 결론

산일전기는 수요 검증 단계를 넘어 생산능력과 가동 정상화가 가치의 핵심 병목이 된 회사입니다. 이번 실행은 기존 제2공장뿐 아니라 2026년 8월 26일 체결된 초고압 변압기 생산용 부동산 양수계약을 별도 기준 시나리오 프로젝트로 분리했습니다. 두 프로젝트의 생산능력·자본적지출·가동 정상화 경로를 시나리오와 현금흐름할인법이 실제 반영한 뒤 베타·가중평균자본비용·감사·내재가치 고정을 통과했습니다.

현재가는 확률가중 기대값이 아니라 개별 하방·기준·상방 시나리오와 비교해야 합니다. 실제 해결 이력으로 구성된 확률 보정 코호트가 아직 충분하지 않아 확률가중 기대값은 의도적으로 산출하지 않았습니다. 이 보고서의 기업 잉여현금흐름 경로는 회사 가이던스가 아니라 2025년 사업보고서와 2026년 2분기 기업설명자료를 기반으로 한 **PRISM 분석가 추정**입니다.

## 증거 신뢰도·분석가 추정 상태

- 회사 실적·수주·생산능력·부지·자본적지출: 회사 공시·기업설명자료 기반, **높은 증거 신뢰도**
- 베타 비교기업 관측: 동일 코스피 기준지수·동일 기간·주간 수익률 최소제곱회귀 기반이며 회귀 표준오차와 시계열 해시를 보존, **중간~높은 증거 신뢰도**
- 일간 최소제곱회귀는 비동시거래·빈도 민감도 진단값으로 별도 보존하며 주간 베타와 임의 평균하지 않습니다.
- 가중평균자본비용의 거시 입력과 국가위험 노출계수: 출처가 명시된 외부 시장자료 및 PRISM 판단값, **중간 신뢰도**
- 하방·기준·상방 기업 잉여현금흐름: 공시 사실에서 파생한 분석가 가정이며 회사 가이던스가 아닙니다.
- 초고압 부동산 계약은 부지 통제와 692.5억원 현금유출을 공식 확정하지만, 정확한 생산능력은 미공시이므로 증분 잉여현금흐름은 보수적 범위 추정입니다.

---

"""
    return header + controlled, render_report_visuals(result.data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--state-root", type=Path)
    args = parser.parse_args()

    if args.state_root is not None:
        args.state_root.mkdir(parents=True, exist_ok=True)
        expected, visuals = render_report(args.state_root)
    else:
        with TemporaryDirectory(prefix="sanil-prism-") as temporary:
            expected, visuals = render_report(Path(temporary))

    target = args.output
    if args.check:
        if not target.exists():
            raise SystemExit(f"Sanil report is missing: {target}")
        if target.read_text(encoding="utf-8") != expected:
            raise SystemExit(f"Sanil report is stale: {target}")
        for visual in visuals:
            visual_target = target.parent / visual.filename
            if not visual_target.exists() or visual_target.read_text(encoding="utf-8") != visual.svg:
                raise SystemExit(f"Sanil report visual is stale: {visual_target}")
        print(f"Sanil report synchronized: {target}")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(expected, encoding="utf-8")
    for visual in visuals:
        (target.parent / visual.filename).write_text(visual.svg, encoding="utf-8")
    print(f"Sanil report written: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
