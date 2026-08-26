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


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT
    / "examples"
    / "report_forms"
    / "SANIL_062040_LIVE_PRIMARY_REPORT.md"
)
STREET_SOURCE_REF = "https://www.yna.co.kr/amp/view/AKR20260811028700008"


def render_report(state_root: Path) -> str:
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
        f"- Street 참고 목표가(Freeze 후 로드): **{float(street_target):,.0f}원**\n"
        if street_target is not None
        else "- Street 참고 목표가: **미확보**\n"
    )

    header = f"""# 산일전기(062040) PRISM LIVE_PRIMARY 보고서

- 데이터 기준일: **{snapshot.cutoff}**
- 검증 상태: **VERIFIED_FROZEN**
- 투자검토 상태: **Preliminary source-backed underwrite**
- 현재가(Freeze 후 로드): **{float(current_price):,.0f}원**
{street_line}- Down / Core / Bull: **{values['Down']:,.0f}원 / {values['Core']:,.0f}원 / {values['Bull']:,.0f}원**
- Hierarchical Beta: **{beta.target_levered_beta:.3f}**
- WACC: **{wacc.wacc_result.wacc:.3%}**
- Core 반영 Capacity 프로젝트: **{', '.join(assessment.core_inclusion_required_projects)}**

## PM 결론

산일전기는 수요 검증 단계를 넘어 생산능력과 ramp가 가치의 핵심 병목이 된 회사입니다. 이번 run은 부지 통제·확정 CAPEX·ramp Evidence를 Core에서 누락하지 않고, 동일 프로젝트의 Capacity·CAPEX·ramp 경로를 Scenario와 DCF가 실제 소비한 뒤 Beta·WACC, Audit, Freeze를 통과했습니다.

현재가는 확률가중 기대값이 아니라 개별 Down/Core/Bull 세계관과 비교해야 합니다. 역사적 calibration cohort가 아직 충분하지 않아 Expected Value는 의도적으로 산출하지 않았습니다. 이 보고서의 FCFF 경로는 회사 가이던스가 아니라 2025 사업보고서와 2026년 2분기 IR을 기반으로 한 **PRISM analyst underwrite**입니다.

## Evidence Confidence / Underwriting Status

- 회사 실적·수주·Capacity·부지·CAPEX: 회사 공시·IR 기반, **높은 증거 신뢰도**
- Beta peer 관측: 실제 상장회사와 공개 `Beta (5Y)` 자료 기반, **중간 증거 신뢰도**
- Beta 공급자는 benchmark·빈도·표준오차를 공개하지 않아 `beta_standard_error`를 임의 생성하지 않았습니다.
- WACC 거시입력과 country-risk lambda: 출처가 명시된 외부 시장자료 및 PRISM 판단값, **중간 신뢰도**
- Down/Core/Bull FCFF: 공시 사실에서 파생한 분석가 가정이며 회사 가이던스가 아닙니다.
- 공식 KRX 수익률 회귀 provider가 가용해지면 현재 외부 Beta 스냅샷을 교체하는 것이 다음 품질개선 항목입니다.

## Source Register

- 2025 사업보고서: {snapshot.sources['annual_report']['source_ref']}
- 2026년 2분기 IR: {snapshot.sources['q2_ir']['source_ref']}
- 실제 peer Beta·WACC 원장: {snapshot.sources['risk_snapshot']['source_ref']}
- PRISM underwriting assumptions: {snapshot.sources['underwriting']['source_ref']}
- Street 참고자료: {STREET_SOURCE_REF}
- 현재가: {market_snapshot.source_ref}

---

"""
    return header + controlled


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--state-root", type=Path)
    args = parser.parse_args()

    if args.state_root is not None:
        args.state_root.mkdir(parents=True, exist_ok=True)
        expected = render_report(args.state_root)
    else:
        with TemporaryDirectory(prefix="sanil-prism-") as temporary:
            expected = render_report(Path(temporary))

    target = args.output
    if args.check:
        if not target.exists():
            raise SystemExit(f"Sanil report is missing: {target}")
        if target.read_text(encoding="utf-8") != expected:
            raise SystemExit(f"Sanil report is stale: {target}")
        print(f"Sanil report synchronized: {target}")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(expected, encoding="utf-8")
    print(f"Sanil report written: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
