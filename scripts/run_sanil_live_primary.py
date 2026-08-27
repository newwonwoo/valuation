from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from valuation_engine.report_form import attest_controlled_run, render_controlled_run_report
from valuation_engine.report_localization import identifier_label_ko
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
    street_reference = (
        f"{float(street_target):,.0f}원 ({street.consensus.report_count}건, 내재가치 고정 후 비교)"
        if street_target is not None and street is not None
        else "미확보"
    )

    valuation_marker = "## 가치평가"
    if valuation_marker not in controlled:
        raise RuntimeError("Sanil report is missing the investor-facing valuation section")
    controlled_body = controlled[controlled.index(valuation_marker):]
    evidence_note = """## 핵심 가정과 위험
- **근거 신뢰도:** 회사 실적·수주·생산능력·부지·자본적지출은 회사 공시·기업설명자료에 기반해 신뢰도가 높습니다.
- **분석가 추정:** 하방·기준·상방 기업잉여현금흐름은 회사 가이던스가 아니라 공시 사실에서 파생한 분석가 가정입니다.
- **생산능력 불확실성:** 초고압 부동산 계약은 부지 통제와 692.5억원 현금유출을 확정하지만 정확한 생산능력은 미공시입니다.
"""
    controlled_body = controlled_body.replace(
        "## 핵심 가정과 위험\n",
        evidence_note,
        1,
    )
    project_names = ", ".join(
        identifier_label_ko(item)
        for item in assessment.core_inclusion_required_projects
    )
    market_gaps = {
        item.scenario_id: item.gap_pct_of_reference
        for item in market.envelope.scenario_gaps
    } if market is not None else {}
    core_gap = market_gaps.get("Core", 0)
    bull_gap = market_gaps.get("Bull", 0)
    header = f"""# 산일전기(062040) 투자보고서

## 투자 요약

### 생산능력 확장이 잉여현금흐름으로 전환되는지가 핵심

| 핵심 판단 항목 | 내용 |
| --- | --- |
| **투자판단** | 판단 유보 — 확률 보정과 진입 규칙이 없어 구체 매수가는 산출하지 않음 |
| **현재가** | {float(current_price):,.0f}원 ({snapshot.cutoff}) |
| **기준 내재가치** | {values['Core']:,.0f}원 · 현재가 대비 {core_gap:+.1%} |
| **가치평가 범위** | 하방 {values['Down']:,.0f}원 · 기준 {values['Core']:,.0f}원 · 상방 {values['Bull']:,.0f}원 |
| **증권사 참고값** | {street_reference} |
| **보고서 성격** | 출처 검증 기반 예비 투자분석 |

### 한 문장 결론

산일전기의 핵심은 수요의 존재보다 제2공장과 초고압 변압기 부지가 실제 출하·마진·잉여현금흐름으로 전환되는 속도이며, 기준 가치는 현재가 대비 {core_gap:+.1%}이고 상방 가치는 {bull_gap:+.1%}인 만큼 지금은 상승여력보다 전환 증거를 먼저 확인할 구간입니다.

### 투자포인트

- **가치동인:** {project_names}을 각각 생산능력·자본적지출·가동 정상화 경로로 반영했습니다.
- **가치평가:** 현금흐름할인법 기준 하방–상방 범위는 {values['Down']:,.0f}–{values['Bull']:,.0f}원이며, 계층형 베타 {beta.target_levered_beta:.3f} · 가중평균자본비용 {wacc.wacc_result.wacc:.3%}를 적용했습니다.
- **남은 제약:** 기업잉여현금흐름은 공시에서 파생한 PRISM 분석가 추정이며, 실제 해결 이력 기반 확률 보정이 없어 기대값은 산출하지 않았습니다.

### 판단 변경 조건

- **상방 확인:** 제2공장·초고압 설비의 일정 준수, 가동률 정상화, 수주잔고의 매출 전환이 공시로 확인될 때.
- **하방 훼손:** 증설 지연·취소, 수주잔고 또는 신규수주 감소, 출하 전환 전 마진 둔화가 확인될 때.
- **행동 가능 조건:** 실제 해결 전망 이력이 누적되어 시나리오 확률을 보정하고 별도 진입 규칙이 승인될 때.

"""
    return header + controlled_body, render_report_visuals(result.data)


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
