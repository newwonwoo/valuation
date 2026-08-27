from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from valuation_engine.brokerage_html import render_sanil_brokerage_html
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
    / "SANIL_062040_LIVE_PRIMARY_REPORT.html"
)
DEFAULT_MARKDOWN_OUTPUT = (
    ROOT
    / "examples"
    / "report_forms"
    / "SANIL_062040_LIVE_PRIMARY_REPORT.md"
)

DART_UHV_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260826000660"
COMPANY_DISCLOSURE_LIST_URL = "https://www.sanil.co.kr/kr/sub/reference/announce.php"
KOREA_INVESTMENT_REPORT_URL = (
    "https://securities.koreainvestment.com/main/research/research/StrategyDetail.jsp"
    "?id=158730&jkGubun=6"
)
HALF_YEAR_REPORT_URL = (
    "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814003544"
)
Q2_IR_URL = (
    "https://www.sanil.co.kr/kr/sub/reference/ir.php?bid=1&idx=1002"
    "&mode=view&page=1&s_cate=&s_keyword=&s_type="
)
LS_CAPACITY_REPORT_URL = (
    "https://file.alphasquare.co.kr/media/pdfs/company-report/"
    "_%EC%82%B0%EC%9D%BC%EC%A0%84%EA%B8%B0_2Q25%20Review_250807%E2%98%86_"
    "%EC%84%B1%EC%A2%85%ED%99%94_1976_Online%20report%20_%206_10p_"
    "%EC%82%B0%EC%9D%BC%EC%A0%84%EA%B8%B0.pdf"
)
TRUMP_GRID_SECURITY_ORDER_URL = (
    "https://www.whitehouse.gov/presidential-actions/2026/08/"
    "declaring-a-national-emergency-to-secure-the-united-states-bulk-power-system/"
)


def _core_terminal_value_share(compiled: object, wacc: Decimal) -> Decimal:
    scenario = "Core"
    flows = [
        compiled.get(f"fcff_year_{year}", scenario).measure.amount
        + compiled.get(f"uhv_fcff_year_{year}", scenario).measure.amount
        for year in range(1, 6)
    ]
    flows[1] -= compiled.get("expansion_capex", scenario).measure.amount
    flows[1] -= compiled.get("uhv_property_capex", scenario).measure.amount
    flows[2] -= compiled.get("uhv_equipment_capex", scenario).measure.amount
    growth = compiled.get("terminal_growth", scenario).measure.amount
    one = Decimal("1")
    explicit_pv = sum(
        (flow / (one + wacc) ** year for year, flow in enumerate(flows, start=1)),
        Decimal("0"),
    )
    terminal_pv = (
        flows[-1]
        * (one + growth)
        / (wacc - growth)
        / (one + wacc) ** 5
    )
    return terminal_pv / (explicit_pv + terminal_pv)


def _fcff_connection_table(compiled: object) -> str:
    lines = [
        "### 현금흐름 계산 연결",
        "",
        "| 시나리오 | 5년차 기존사업 FCFF | 신규 부지 증분 | DCF·영구가치 사용 합계 |",
        "| --- | ---: | ---: | ---: |",
    ]
    labels = {"Down": "하방", "Core": "기준", "Bull": "상방"}
    for scenario in ("Down", "Core", "Bull"):
        base = compiled.get("fcff_year_5", scenario).measure.amount
        incremental = compiled.get("uhv_fcff_year_5", scenario).measure.amount
        lines.append(
            f"| {labels[scenario]} | {base * 10:,.0f}억원 | "
            f"{incremental * 10:,.0f}억원 | {(base + incremental) * 10:,.0f}억원 |"
        )
    lines.extend(
        (
            "",
            "- 신규 부지 증분 FCFF는 기존제품 확장과 초고압을 합친 뒤, 연간 증분매출에 대해서만 운전자본을 차감합니다.",
            "- 현재 계산은 제2공장 2027년 이후 공시 잔여액 93.7억원, 초고압 부동산 692.5억원, 생산·시험설비 분석가 가정을 별도 현금유출로 차감합니다.",
        )
    )
    return "\n".join(lines)


def render_report(state_root: Path) -> tuple[str, str, tuple]:
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
    probability_assessment = result.data["scenario_probability_assessment"]
    probability_labels = {"Down": "하방", "Core": "기준", "Bull": "상방"}
    probability_summary = " · ".join(
        f"{probability_labels[item.scenario_id]} {item.displayed_probability * 100:.0f}%"
        for item in probability_assessment.rows
    )
    market_snapshot = load_sanil_market_snapshot()
    market = result.data.get("market_comparison")
    street = result.data.get("street_comparison")
    current_price = (
        market.observation.price
        if market is not None
        else market_snapshot.price
    )
    market_date = (
        market.observation.as_of
        if market is not None
        else market_snapshot.as_of
    )
    street_target = (
        street.consensus.mean_target_price
        if street is not None
        else None
    )
    street_reference = (
        f"{float(street_target):,.0f}원 ({street.consensus.report_count}건, 가치평가 확정 후 참고)"
        if street_target is not None and street is not None
        else "미확보"
    )

    valuation_marker = "## 가치평가"
    if valuation_marker not in controlled:
        raise RuntimeError("Sanil report is missing the investor-facing valuation section")
    controlled_body = controlled[controlled.index(valuation_marker):]
    compiled = result.data["compiled_assumption_set"]
    controlled_body = controlled_body.replace(
        "## 가치평가\n",
        "## 가치평가\n\n" + _fcff_connection_table(compiled) + "\n\n",
        1,
    )
    evidence_note = """## 핵심 가정과 위험
- **근거 신뢰도:** 회사 실적·수주·생산능력·부지·자본적지출은 회사 공시·기업설명자료에 기반해 신뢰도가 높습니다.
- **분석가 추정:** 하방·기준·상방 기업잉여현금흐름은 회사 가이던스가 아니라 공시 사실에서 파생한 분석가 가정입니다.
- **생산능력 불확실성:** 초고압 부동산 계약은 부지 통제와 692.5억원 매매대금을 확정하지만 정확한 생산능력·설비투자비·양산시점은 미공시입니다. 기존제품 CAPA는 기준 50%, 상방 100%만 인정합니다.
- **누락 비용 위험:** 부가가치세·세금·수수료와 향후 건설비는 별도일 수 있습니다. 생산·시험설비는 기준 600억원의 분석가 가정을 반영했습니다.
- **지급시점 단순화:** 공시된 계약금 69.25억원·중도금 207.75억원·잔금 415.5억원을 현재 모델은 2년차 일괄 현금유출로 처리해, 지급일별 현재가치 계산은 후속 보완이 필요합니다.
"""
    controlled_body = controlled_body.replace(
        "## 핵심 가정과 위험\n",
        evidence_note,
        1,
    )
    market_gaps = {
        item.scenario_id: item.gap_pct_of_reference
        for item in market.envelope.scenario_gaps
    } if market is not None else {}
    core_gap = market_gaps.get("Core", 0)
    terminal_share = _core_terminal_value_share(
        compiled,
        Decimal(str(wacc.wacc_result.wacc)),
    )
    capacity = result.data["sanil_capacity_economics"]
    physical = capacity["physical"]
    capacity_scenarios = {
        row["scenario_id"]: row for row in capacity["scenarios"]
    }
    core_mature = capacity_scenarios["Core"]["years"][-1]
    bull_mature = capacity_scenarios["Bull"]["years"][-1]
    operating = result.data["sanil_operating_facts"]
    h1_operating_profit = Decimal(
        str(operating["operating_profit_h1_2026_krw_billion"])
    )
    h1_operating_cash_flow = Decimal(
        str(operating["operating_cash_flow_h1_2026_krw_billion"])
    )
    h1_simple_fcff = (
        h1_operating_cash_flow
        - Decimal(str(operating["ppe_capex_h1_2026_krw_billion"]))
        - Decimal(str(operating["intangible_capex_h1_2026_krw_billion"]))
    )
    product_rows = (
        (
            "특수변압기",
            physical["specialty_transformer_effective_capacity"],
        ),
        (
            "일반 전력망 변압기",
            physical["grid_transformer_effective_capacity"],
        ),
        ("기타 기존제품", physical["other_product_effective_capacity"]),
        ("154kV 초고압", physical["uhv_effective_capacity"]),
    )
    product_table = "\n".join(
        f"| {label} | {value * 10:,.0f}억원 | "
        f"{value / physical['total_effective_capacity']:.1%} |"
        for label, value in product_rows
    )
    checkpoint_table = "\n".join(
        f"| {int(row['year'])}년 {row['label']} | "
        f"{row['existing_capacity_realization']:.0%} | "
        f"{row['total_revenue'] * 10:,.0f}억원 | "
        f"{row['operating_profit'] * 10:,.0f}억원 | "
        f"{row['operating_margin']:.1%} | "
        f"{row['fcff'] * 10:,.0f}억원 | "
        f"{row['normalized_fcff'] * 10:,.0f}억원 |"
        for row in capacity["checkpoints"]
    )
    investment_view = "매수" if core_gap >= 0.15 else "관망"
    header = f"""# 트럼프가 전력망을 국가안보로 묶었다 — 산일전기(062040)

## 투자 요약

### 위험 공급자 배제 → 검증된 공급망 → 5,026억원 생산 슬롯

| 핵심 판단 항목 | 내용 |
| --- | --- |
| **투자판단** | {investment_view} — 기준 목표가까지 상승여력 {core_gap:.1%}, 하방 시나리오 병행 관리 |
| **현재가** | {float(current_price):,.0f}원 ({market_date}) |
| **기준 내재가치** | {values['Core']:,.0f}원 · 현재가 대비 {core_gap:+.1%} |
| **가치평가 범위** | 하방 {values['Down']:,.0f}원 · 기준 {values['Core']:,.0f}원 · 상방 {values['Bull']:,.0f}원 |
| **시나리오 가능성** | {probability_summary} · 미보정 분석가 사전확률, 기대값 미적용 |
| **증권사 참고값** | {street_reference} |
| **보고서 성격** | 8월 26일 공시와 8월 27일 시장·증권사 후속 해석을 분리 반영한 투자분석 |

### 한 문장 결론

트럼프 행정부가 전력망 장비를 국가안보 심사 대상으로 올리면서 공급자 자격과 납품 슬롯의 희소성이 커졌고, 산일전기는 87.2% 가동률의 병목을 풀 신규 부지를 확보했습니다. 다만 정책 프리미엄을 목표가에 별도로 더하지 않고 기존제품 순증분을 기준 50%만 인정한 내재가치는 {values['Core']:,.0f}원입니다.

### 트럼프 행정명령이 바꾼 것

- **확인된 사실:** 8월 26일 행정명령은 미국 대용량 전력망의 외국 공급 위험을 국가비상사태로 규정하고, 69kV 이상 계통의 변전소 변압기를 심사 범위에 포함했습니다. 위험 공급자 거래 제한·사전적격 공급자 체계와 120일 이내 세부 규칙 마련도 지시했습니다. [백악관 행정명령 원문]({TRUMP_GRID_SECURITY_ORDER_URL})
- **산일전기 연결:** 수요의 단순 증가보다 미국 고객이 선택할 수 있는 공급자 자격과 검증된 생산 슬롯이 중요해지는 변화입니다. 산일전기가 향후 공급자 자격을 확보한다면, 이미 높은 가동률을 풀 신규 5,026억원 슬롯의 경제적 가치가 커지는 방향입니다.
- **해석의 한계:** 모든 외국산 장비를 일괄 금지한 명령은 아니며 산일전기는 한국 생산 수출기업입니다. 직접 수혜 여부는 향후 미국 에너지부 규칙과 사전적격 공급자 인정에 달려 있어, 이번 정책을 목표가 산식의 별도 프리미엄으로 반영하지 않았습니다.

### 투자포인트

- **가치동인:** 신규 부지 유효 연매출 CAPA는 기존제품 {physical['existing_product_effective_capacity'] * 10:,.0f}억원 + 초고압 {physical['uhv_effective_capacity'] * 10:,.0f}억원 = {physical['total_effective_capacity'] * 10:,.0f}억원입니다.
- **가치평가:** 현금흐름할인법 기준 하방–상방 범위는 {values['Down']:,.0f}–{values['Bull']:,.0f}원이며, 계층형 베타 {beta.target_levered_beta:.3f} · 가중평균자본비용 {wacc.wacc_result.wacc:.3%}를 적용했습니다.
- **현금창출력:** 성숙기 기준은 매출 {core_mature['total_revenue'] * 10:,.0f}억원 · 영업이익 {core_mature['total_operating_profit'] * 10:,.0f}억원 · 잉여현금흐름 {core_mature['total_fcff'] * 10:,.0f}억원, 전량가동 상방은 매출 {bull_mature['total_revenue'] * 10:,.0f}억원 · 잉여현금흐름 {bull_mature['total_fcff'] * 10:,.0f}억원입니다.
- **남은 제약:** 기준 DCF 기업가치의 {terminal_share:.1%}가 영구가치에 있어 가동·마진 정상화 지연에 민감하며, {probability_summary}는 실제 해결 이력으로 보정되지 않아 기대값에는 적용하지 않았습니다.

### 공시 → 제품 구성 → 영업이익 → 현금흐름 연결

- **현재 현금전환:** 2026년 상반기 영업현금흐름 {h1_operating_cash_flow * 10:,.0f}억원, 단순 잉여현금흐름 {h1_simple_fcff * 10:,.0f}억원으로 영업이익 대비 각각 {h1_operating_cash_flow / h1_operating_profit:.1%}, {h1_simple_fcff / h1_operating_profit:.1%}입니다. 매출채권+재고−매입채무 증감은 {operating['net_working_capital_change_h1_2026_krw_billion'] * 10:,.1f}억원으로 사실상 제자리입니다. [2026년 반기보고서]({HALF_YEAR_REPORT_URL})
- **물리 CAPA 출발점:** 기존제품 명목 3,090억원에 95% 가동을 적용한 2,936억원과 초고압 2,090억원을 합쳐 5,026억원입니다. [부지 취득 공시]({DART_UHV_URL}) · [LS증권 CAPA 참고자료]({LS_CAPACITY_REPORT_URL}) · [회사 2분기 IR]({Q2_IR_URL})

| 신규공장 제품 | 유효 연매출 CAPA | 신규공장 믹스 |
| --- | ---: | ---: |
{product_table}
| **합계** | **{physical['total_effective_capacity'] * 10:,.0f}억원** | **100.0%** |

| 연도·단계 | 기존품목 CAPA 인정 | 매출 | 영업이익 | 영업이익률 | 보수적 FCF | 정상화 FCF |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
{checkpoint_table}

보수적 FCF는 세후영업이익 + 감가상각 1.0% − 유지투자 1.5% − 신규공장 증분매출의 운전자본 5%입니다. 정상화 FCF는 램프업 이후 추가 운전자본 투입이 멈춘 상태이며, 이 표는 회사 가이던스가 아니라 공시와 증권사 CAPA를 연결한 분석가 추론입니다.

### 판단 변경 조건

- **상방 확인:** 회사가 초고압 2,000억원 이상과 기존제품 약 3,000억원의 순증 CAPA, 생산·시험설비, 고객 인증 일정을 확인할 때.
- **하방 훼손:** 증설 지연·취소, 수주잔고 또는 신규수주 감소, 출하 전환 전 마진 둔화가 확인될 때.
- **행동 가능 조건:** 실제 해결 전망 이력이 누적되어 시나리오 확률을 보정하고 별도 진입 규칙이 승인될 때.

### 8월 27일 자료 반영

- **신규 공시 여부:** [회사 공시목록]({COMPANY_DISCLOSURE_LIST_URL}) 기준 8월 27일 신규·정정 공시는 없습니다. 최신 원문은 8월 26일 [유형자산 양수결정]({DART_UHV_URL})입니다.
- **회사 확정 사실:** 안산 토지·건물 692.5억원, 자기자금 지급, 초고압 변압기 생산시설과 기존 제품 생산능력 확대 목적입니다.
- **아직 미공시:** 순증 생산능력, 생산·시험설비 총액, 고객 인증·수주, 상업 생산시점, 매출·마진·현금흐름 기여입니다.
- **증권사 해석:** [한국투자증권 8월 27일 보고서]({KOREA_INVESTMENT_REPORT_URL})의 2028년 양산·2029년 추가 매출 2,000억원 이상·목표가 270,000원은 회사 확정치가 아니라 증권사 추정치입니다.
- **모델 처리:** 부지 취득은 수요 대응의 경제적 실질이 있어 DCF에 포함했습니다. 다만 한국투자증권 성장률 전망이 신규공장 효과를 일부 포함할 수 있어 기존제품 CAPA는 기준 50%, 전량은 상방으로 분리했습니다.

"""
    markdown_report = header + controlled_body
    visuals = render_report_visuals(result.data)
    html_report = render_sanil_brokerage_html(
        result.data,
        visuals=visuals,
        terminal_value_share=terminal_share,
        markdown_filename=DEFAULT_MARKDOWN_OUTPUT.name,
    )
    return markdown_report, html_report, visuals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=DEFAULT_MARKDOWN_OUTPUT,
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--state-root", type=Path)
    args = parser.parse_args()

    if args.state_root is not None:
        args.state_root.mkdir(parents=True, exist_ok=True)
        expected_markdown, expected_html, visuals = render_report(args.state_root)
    else:
        with TemporaryDirectory(prefix="sanil-prism-") as temporary:
            expected_markdown, expected_html, visuals = render_report(Path(temporary))

    target = args.output
    markdown_target = args.markdown_output
    if args.check:
        if not target.exists():
            raise SystemExit(f"Sanil report is missing: {target}")
        if target.read_text(encoding="utf-8") != expected_html:
            raise SystemExit(f"Sanil report is stale: {target}")
        if not markdown_target.exists():
            raise SystemExit(f"Sanil report appendix is missing: {markdown_target}")
        if markdown_target.read_text(encoding="utf-8") != expected_markdown:
            raise SystemExit(f"Sanil report appendix is stale: {markdown_target}")
        for visual in visuals:
            visual_target = target.parent / visual.filename
            if not visual_target.exists() or visual_target.read_text(encoding="utf-8") != visual.svg:
                raise SystemExit(f"Sanil report visual is stale: {visual_target}")
        print(f"Sanil report synchronized: {target}")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    markdown_target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(expected_html, encoding="utf-8")
    markdown_target.write_text(expected_markdown, encoding="utf-8")
    for visual in visuals:
        (target.parent / visual.filename).write_text(visual.svg, encoding="utf-8")
    print(f"Sanil brokerage report written: {target}")
    print(f"Sanil audit appendix written: {markdown_target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
