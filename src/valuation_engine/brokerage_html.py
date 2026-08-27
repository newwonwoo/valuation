from __future__ import annotations

from decimal import Decimal
from html import escape
from typing import Any, Iterable

from .report_localization import identifier_label_ko
from .source_reporting import build_source_link_index
from .street import StreetResearchReport
from .visual_reporting import ReportVisual


def _money(value: object) -> str:
    return f"{Decimal(str(value)):,.0f}원"


def _billion(value: object) -> str:
    return f"{Decimal(str(value)) * 10:,.0f}억원"


def _pct(value: object, *, signed: bool = False) -> str:
    number = Decimal(str(value)) * 100
    return f"{number:+.1f}%" if signed else f"{number:.1f}%"


def _scenario_label(scenario_id: str) -> str:
    return {"Down": "하방", "Core": "기준", "Bull": "상방"}.get(
        scenario_id, scenario_id
    )


def _broker_label(broker: str) -> str:
    return identifier_label_ko(broker)


def _source_anchor(url: str, label: str) -> str:
    return (
        f'<a href="{escape(url, quote=True)}" target="_blank" '
        f'rel="noreferrer">{escape(label)}</a>'
    )


def _scenario_comment(scenario_id: str) -> str:
    return {
        "Down": "기존품목 CAPA 25%·초고압 CAPA 70%만 매출화",
        "Core": "기존품목 CAPA 50%·초고압 CAPA 100% 매출화",
        "Bull": "기존품목·초고압 물리 CAPA를 전부 매출화",
    }.get(scenario_id, "분석가 시나리오")


def _method_label(method: str) -> str:
    replacements = {
        "PER-based target framework": "주가수익비율(PER) 기반",
        "broker target-price framework": "증권사 목표가 산식",
        "2027E PER 35x": "2027년 예상 PER 35배",
        "2027E EPS × PER 29x": "2027년 예상 EPS × PER 29배",
    }
    return replacements.get(method, method)


def _source_register(data: dict[str, Any]) -> str:
    rows: list[str] = []
    for index, item in enumerate(
        build_source_link_index(data, require_all_http=True), start=1
    ):
        labels = " · ".join(identifier_label_ko(label) for label in item.labels)
        evidence_count = sum(
            1 for coverage in item.coverage if coverage.startswith("근거 ")
        )
        other = tuple(
            coverage
            for coverage in item.coverage
            if not coverage.startswith("근거 ")
        )
        coverage_text = (
            f"연결 근거 {evidence_count}개"
            if evidence_count
            else (" · ".join(other) or "원문 확인")
        )
        rows.append(
            "<li>"
            f'<span class="source-no">{index:02d}</span>'
            '<span class="source-copy">'
            f"<strong>{escape(labels)}</strong>"
            f"<small>{escape(coverage_text)}</small>"
            "</span>"
            f'{_source_anchor(item.url, "원문 열기 ↗")}'
            "</li>"
        )
    return "".join(rows)


def _visual_cards(visuals: Iterable[ReportVisual]) -> str:
    figures: list[str] = []
    for visual in visuals:
        figures.append(
            "<figure>"
            f'<a href="{escape(visual.filename, quote=True)}" target="_blank">'
            f'<img src="{escape(visual.filename, quote=True)}" '
            f'alt="{escape(visual.alt_text, quote=True)}">'
            "</a>"
            f"<figcaption>{escape(visual.alt_text)} · 클릭하면 크게 열립니다.</figcaption>"
            "</figure>"
        )
    return "".join(figures)


def render_sanil_brokerage_html(
    data: dict[str, Any],
    *,
    visuals: tuple[ReportVisual, ...],
    terminal_value_share: Decimal,
    markdown_filename: str,
) -> str:
    if len(visuals) != 2:
        raise ValueError("증권사형 최종보고서는 요약 이미지 2장이 필요합니다")
    valuation = data["generic_valuation_result"]
    scenarios = {item.scenario_id: item for item in valuation.scenarios}
    compiled = data["compiled_assumption_set"]
    market = data["market_comparison"]
    market_price = Decimal(str(market.observation.price))
    market_as_of = market.observation.as_of
    market_gaps = {
        item.scenario_id: Decimal(str(item.gap_pct_of_reference))
        for item in market.envelope.scenario_gaps
    }
    probability = {
        item.scenario_id: Decimal(str(item.displayed_probability))
        for item in data["scenario_probability_assessment"].rows
    }
    street = data["street_comparison"]
    beta = data["live_beta_result"].target_levered_beta
    wacc = data["live_wacc_result"].wacc_result.wacc
    capacity = data["sanil_capacity_economics"]
    physical = capacity["physical"]
    capacity_scenarios = {
        row["scenario_id"]: row for row in capacity["scenarios"]
    }
    operating = data["sanil_operating_facts"]

    scenario_rows: list[str] = []
    fcff_rows: list[str] = []
    for scenario_id in ("Down", "Core", "Bull"):
        scenario = scenarios[scenario_id]
        base_fcff = compiled.get("fcff_year_5", scenario_id).measure.amount
        uhv_fcff = compiled.get("uhv_fcff_year_5", scenario_id).measure.amount
        scenario_rows.append(
            "<tr>"
            f"<th>{_scenario_label(scenario_id)}</th>"
            f"<td>{_money(scenario.value_per_share)}</td>"
            f'<td class="{("positive" if market_gaps[scenario_id] >= 0 else "negative")}">'
            f"{_pct(market_gaps[scenario_id], signed=True)}</td>"
            f"<td>{_pct(probability[scenario_id])}</td>"
            f"<td>{escape(_scenario_comment(scenario_id))}</td>"
            "</tr>"
        )
        fcff_rows.append(
            "<tr>"
            f"<th>{_scenario_label(scenario_id)}</th>"
            f"<td>{_billion(base_fcff)}</td>"
            f"<td>{_billion(uhv_fcff)}</td>"
            f"<td><strong>{_billion(base_fcff + uhv_fcff)}</strong></td>"
            "</tr>"
        )

    broker_rows: list[str] = []
    for report in data["street_reports"]:
        if not isinstance(report, StreetResearchReport):
            continue
        gap = Decimal(str(report.target_price)) / Decimal(
            str(scenarios["Core"].value_per_share)
        ) - Decimal("1")
        broker_rows.append(
            "<tr>"
            f"<th>{_source_anchor(report.source_ref, _broker_label(report.broker))}</th>"
            f"<td>{_money(report.target_price)}</td>"
            f"<td>{escape(report.base_year)}년 · {escape(_method_label(report.valuation_method))}</td>"
            f"<td>{_pct(gap, signed=True)}</td>"
            "</tr>"
        )

    core_value = Decimal(str(scenarios["Core"].value_per_share))
    bull_value = Decimal(str(scenarios["Bull"].value_per_share))
    down_value = Decimal(str(scenarios["Down"].value_per_share))
    street_mean = Decimal(str(street.consensus.mean_target_price))
    investment_view = (
        "매수" if market_gaps["Core"] >= Decimal("0.15") else "관망"
    )
    core_mature = capacity_scenarios["Core"]["years"][-1]
    bull_mature = capacity_scenarios["Bull"]["years"][-1]
    core_project_capex = (
        compiled.get("uhv_property_capex", "Core").measure.amount
        + compiled.get("uhv_equipment_capex", "Core").measure.amount
    )
    core_incremental_nopat = Decimal(
        str(core_mature["incremental_operating_profit"])
    ) * Decimal("0.771")
    core_payback = core_project_capex / core_incremental_nopat
    physical_total = Decimal(str(physical["total_effective_capacity"]))
    physical_existing = Decimal(
        str(physical["existing_product_effective_capacity"])
    )
    physical_uhv = Decimal(str(physical["uhv_effective_capacity"]))
    margin_rows: list[str] = []
    bull_base_op = Decimal(str(bull_mature["base_operating_profit"]))
    bull_existing_revenue = Decimal(
        str(bull_mature["existing_product_incremental_revenue"])
    )
    bull_base_margin = bull_base_op / Decimal(str(bull_mature["base_revenue"]))
    for margin in (Decimal("0.30"), Decimal("0.35"), Decimal("0.40")):
        op = (
            bull_base_op
            + bull_existing_revenue * bull_base_margin
            + physical_uhv * margin
        )
        total_revenue = Decimal(str(bull_mature["total_revenue"]))
        margin_rows.append(
            "<tr>"
            f"<th>{_pct(margin)}</th><td>{_billion(op)}</td>"
            f"<td>{_pct(op / total_revenue)}</td>"
            "</tr>"
        )
    capacity_rows: list[str] = []
    for scenario_id in ("Down", "Core", "Bull"):
        row = capacity_scenarios[scenario_id]
        mature = row["years"][-1]
        total_revenue = Decimal(str(mature["total_revenue"]))
        total_op = Decimal(str(mature["total_operating_profit"]))
        capacity_rows.append(
            "<tr>"
            f"<th>{_scenario_label(scenario_id)}</th>"
            f"<td>{_pct(row['existing_capacity_realization'])}</td>"
            f"<td>{_billion(total_revenue)}</td>"
            f"<td>{_billion(total_op)}</td>"
            f"<td>{_pct(total_op / total_revenue)}</td>"
            f"<td><strong>{_billion(mature['total_fcff'])}</strong></td>"
            "</tr>"
        )
    price_low = min(down_value, core_value, bull_value, market_price) * Decimal("0.95")
    price_high = max(down_value, core_value, bull_value, market_price) * Decimal("1.05")

    def price_position(value: Decimal) -> str:
        return f"{(value - price_low) / (price_high - price_low) * 100:.1f}%"

    ai_insight = (
        "현재 가동률 87.2%와 수주잔고를 함께 보면 신규 부지의 1차 의미는 "
        "수요 창출보다 고마진 생산 슬롯 확보입니다. 초고압 초기 마진이 기존 "
        "특수변압기보다 낮아도 전사 마진은 크게 훼손되지 않고 절대 영업이익과 "
        "현금흐름은 늘어날 수 있습니다. 반대로 총투자비 대비 회수기간이 약 "
        f"{core_payback:.1f}년으로 너무 짧게 계산되는 점은 낙관의 증거가 아니라 "
        "이전·창고·시험동·고객인증과 매출 중복을 다시 확인하라는 경고입니다. "
        "따라서 부지는 DCF에서 제외하지 않되 기존품목 CAPA는 기준 50%, "
        "전량 인정은 상방으로 분리했습니다. 이 문단은 인공지능의 연결 인사이트입니다."
    )
    if len(ai_insight) > 1000:
        raise ValueError("인공지능 인사이트는 1,000자를 초과할 수 없습니다")

    disclosure_link = _source_anchor(
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260826000660",
        "DART 유형자산 양수결정 원문",
    )
    market_link = _source_anchor(market.observation.source_ref, "현재가 원문")
    kis_link = _source_anchor(
        "https://securities.koreainvestment.com/main/research/research/StrategyDetail.jsp?id=158730&jkGubun=6",
        "한국투자증권 8월 27일 보고서",
    )
    kis_aug12_link = _source_anchor(
        "https://securities.koreainvestment.com/main/research/research/StrategyDetail.jsp?id=158233&jkGubun=6",
        "한국투자증권 8월 12일 보고서",
    )
    half_year_link = _source_anchor(
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814003544",
        "2026년 반기보고서",
    )
    q2_ir_link = _source_anchor(
        "https://www.sanil.co.kr/kr/sub/reference/ir.php?bid=1&idx=1002&mode=view&page=1&s_cate=&s_keyword=&s_type=",
        "회사 2분기 IR",
    )
    ls_link = _source_anchor(
        "https://file.alphasquare.co.kr/media/pdfs/company-report/_%EC%82%B0%EC%9D%BC%EC%A0%84%EA%B8%B0_2Q25%20Review_250807%E2%98%86_%EC%84%B1%EC%A2%85%ED%99%94_1976_Online%20report%20_%206_10p_%EC%82%B0%EC%9D%BC%EC%A0%84%EA%B8%B0.pdf",
        "LS증권 CAPA 참고자료",
    )

    return f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>산일전기(062040) 기업분석 — 2026.08.27</title>
<style>
:root {{
  --navy:#112a46; --blue:#1f5f9b; --sky:#eaf3fb; --ink:#152033;
  --muted:#667487; --line:#d9e1e8; --paper:#fff; --bg:#eef1f4;
  --red:#b42318; --green:#087a55; --gold:#b88719;
}}
* {{ box-sizing:border-box; }}
html {{ background:var(--bg); color:var(--ink); font-family:Pretendard,"Noto Sans KR","Apple SD Gothic Neo","Malgun Gothic",sans-serif; }}
body {{ margin:0; font-size:13pt; line-height:1.6; word-break:keep-all; }}
a {{ color:var(--blue); text-decoration:none; border-bottom:1px solid rgba(31,95,155,.35); }}
.report {{ padding:28px 0 60px; }}
.page {{ width:min(210mm, calc(100vw - 28px)); min-height:297mm; margin:0 auto 22px; padding:15mm 16mm 14mm; background:var(--paper); box-shadow:0 10px 36px rgba(17,42,70,.12); position:relative; overflow:hidden; }}
.page::after {{ content:attr(data-page); position:absolute; right:16mm; bottom:8mm; color:#8b97a6; font-size:9pt; letter-spacing:.08em; }}
.masthead {{ display:flex; justify-content:space-between; gap:20px; align-items:flex-start; padding-bottom:10px; border-bottom:3px solid var(--navy); color:var(--navy); font-size:10pt; font-weight:800; letter-spacing:.08em; }}
.eyebrow {{ color:var(--blue); font-weight:800; font-size:10pt; letter-spacing:.08em; text-transform:uppercase; }}
h1 {{ margin:22px 0 8px; max-width:670px; font-size:29pt; line-height:1.22; letter-spacing:-.045em; color:var(--navy); }}
h2 {{ margin:0 0 12px; font-size:21pt; line-height:1.28; letter-spacing:-.035em; color:var(--navy); }}
h3 {{ margin:0 0 7px; font-size:15pt; line-height:1.35; color:var(--navy); }}
p {{ margin:0 0 10px; }}
.subtitle {{ margin:0 0 20px; color:var(--muted); font-size:12pt; }}
.hero-grid {{ display:grid; grid-template-columns:minmax(0,1.45fr) minmax(230px,.72fr); gap:26px; align-items:start; }}
.lead {{ margin:18px 0 20px; font-size:15pt; line-height:1.62; font-weight:650; letter-spacing:-.018em; }}
.stance {{ border:1px solid var(--line); border-top:5px solid var(--blue); padding:14px 16px; background:#fbfdff; }}
.stance .label {{ color:var(--muted); font-size:9pt; font-weight:800; letter-spacing:.08em; }}
.stance .view {{ margin:3px 0 10px; font-size:22pt; line-height:1.2; font-weight:850; color:var(--navy); }}
.metric {{ display:flex; justify-content:space-between; gap:12px; padding:8px 0; border-top:1px solid var(--line); font-size:10.5pt; }}
.metric strong {{ color:var(--navy); }}
.metric .negative {{ color:var(--red); }}
.scenario-strip {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin:18px 0 20px; }}
.scenario {{ padding:12px 14px; border:1px solid var(--line); border-radius:3px; }}
.scenario.core {{ background:var(--sky); border-color:#a9c9e5; }}
.scenario small {{ color:var(--muted); font-weight:750; }}
.scenario strong {{ display:block; margin:2px 0; font-size:17pt; color:var(--navy); }}
.scenario span {{ font-size:10pt; }}
.pill {{ display:inline-block; padding:3px 8px; border-radius:20px; background:var(--sky); color:var(--blue); font-size:9pt; font-weight:800; }}
.thesis-list {{ display:grid; gap:10px; margin-top:11px; }}
.thesis {{ display:grid; grid-template-columns:28px minmax(0,1fr); gap:10px; padding-top:10px; border-top:1px solid var(--line); }}
.thesis b {{ color:var(--blue); }}
.thesis p {{ font-size:11pt; line-height:1.5; }}
.section-head {{ display:flex; justify-content:space-between; gap:16px; align-items:end; margin-bottom:13px; padding-bottom:8px; border-bottom:2px solid var(--navy); }}
.section-head p {{ color:var(--muted); font-size:10pt; text-align:right; }}
.two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
.box {{ padding:14px 16px; background:#f7f9fb; border-left:4px solid var(--blue); }}
.box.warn {{ background:#fff8eb; border-color:var(--gold); }}
.box.risk {{ background:#fff4f2; border-color:var(--red); }}
.box p,.box li {{ font-size:10.5pt; line-height:1.52; }}
.box ul {{ margin:5px 0 0; padding-left:18px; }}
table {{ width:100%; border-collapse:collapse; margin:8px 0 16px; font-size:10pt; line-height:1.38; }}
th,td {{ padding:9px 8px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
thead th {{ background:var(--navy); color:white; font-weight:750; }}
tbody th {{ color:var(--navy); white-space:nowrap; }}
td:nth-child(2),td:nth-child(3),td:nth-child(4) {{ font-variant-numeric:tabular-nums; }}
.positive {{ color:var(--green); font-weight:750; }} .negative {{ color:var(--red); font-weight:750; }}
.price-line {{ position:relative; height:74px; margin:15px 8px 9px; border-top:5px solid #d8e1e8; }}
.price-mark {{ position:absolute; top:-12px; transform:translateX(-50%); text-align:center; font-size:9pt; color:var(--muted); }}
.price-mark::before {{ content:""; display:block; width:3px; height:22px; margin:0 auto 4px; background:var(--blue); }}
.price-mark.current {{ color:var(--navy); font-weight:850; }} .price-mark.current::before {{ background:var(--red); height:30px; }}
.caption {{ color:var(--muted); font-size:9.5pt; line-height:1.45; }}
.debate {{ display:grid; grid-template-columns:118px minmax(0,1fr); gap:12px; padding:10px 0; border-top:1px solid var(--line); }}
.debate strong {{ color:var(--navy); }} .debate p {{ font-size:10.5pt; line-height:1.48; }}
.conditions {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }}
.condition {{ padding:12px; border:1px solid var(--line); }} .condition p {{ font-size:10pt; line-height:1.45; }}
.ai {{ padding:14px 16px; border:1px solid #b7d8d2; background:#f1faf8; }}
.ai h3 {{ color:#08685a; }} .ai p {{ font-size:10.5pt; line-height:1.52; }}
.source-list {{ list-style:none; margin:0; padding:0; columns:2; column-gap:22px; }}
.source-list li {{ break-inside:avoid; display:grid; grid-template-columns:27px minmax(0,1fr) auto; gap:8px; align-items:start; padding:9px 0; border-bottom:1px solid var(--line); font-size:9.4pt; }}
.source-no {{ color:#8a98a8; font-weight:800; }} .source-copy small {{ display:block; color:var(--muted); margin-top:2px; }}
.source-list a {{ white-space:nowrap; font-size:8.8pt; }}
.visuals {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:14px; }}
figure {{ margin:0; }} figure img {{ display:block; width:100%; max-height:104mm; object-fit:contain; border:1px solid var(--line); background:#f7f8f9; }}
figcaption {{ margin-top:5px; color:var(--muted); font-size:8.5pt; text-align:center; }}
.footnote {{ margin-top:12px; padding-top:10px; border-top:1px solid var(--line); color:var(--muted); font-size:9pt; line-height:1.45; }}
.technical {{ width:min(210mm, calc(100vw - 28px)); margin:0 auto; background:white; padding:16px 20px; box-shadow:0 8px 26px rgba(17,42,70,.08); }}
.technical summary {{ cursor:pointer; font-weight:800; color:var(--navy); }} .technical p {{ margin-top:10px; font-size:10pt; color:var(--muted); }}
@media (max-width:760px) {{
  body {{ font-size:12pt; }} .page {{ width:100%; min-height:0; padding:26px 20px 54px; margin-bottom:12px; box-shadow:none; }}
  .hero-grid,.two-col,.conditions {{ grid-template-columns:1fr; }} .scenario-strip {{ grid-template-columns:1fr; }}
  .source-list {{ columns:1; }} .visuals {{ grid-template-columns:1fr; }} .section-head {{ display:block; }} .section-head p {{ text-align:left; }}
  .table-wrap {{ overflow-x:auto; max-width:100%; }} table {{ min-width:650px; }}
}}
@page {{ size:A4; margin:0; }}
@media print {{
  html,body {{ background:#fff; }} .report {{ padding:0; }} .page {{ width:210mm; min-height:297mm; margin:0; padding:14mm 15mm 13mm; box-shadow:none; overflow:visible; break-after:page; page-break-after:always; }}
  .page:last-of-type {{ break-after:auto; page-break-after:auto; }} .technical {{ display:none; }} a {{ color:inherit; border:0; }}
}}
</style>
</head>
<body>
<main class="report">
  <section class="page" data-page="01 / 04">
    <header class="masthead"><span>PRISM 기업분석 · 전력기기</span><span>2026.08.27</span></header>
    <div class="eyebrow" style="margin-top:20px">산일전기 · 062040 · KOSPI</div>
    <div class="hero-grid">
      <div>
        <h1>부지가 아니라 5,026억원의 생산 슬롯을 샀다</h1>
        <p class="subtitle">신규 부지 → 제품 구성 → 영업이익 → 잉여현금흐름 → 현금흐름할인법 연결 · 자료 기준 2026년 8월 27일</p>
        <p class="lead">공장 가동률 87.2%에서는 판매율보다 생산 슬롯이 병목입니다. 신규 부지를 DCF에 반영하면 기준 내재가치는 {_money(core_value)}, 현재가 대비 {_pct(market_gaps['Core'], signed=True)}입니다. 다만 1년 안팎의 회수기간은 지나치게 좋아 보이므로 기존품목 증분 CAPA는 기준에 50%만 인정했습니다.</p>
      </div>
      <aside class="stance">
        <div class="label">투자판단</div><div class="view">{investment_view}</div>
        <div class="metric"><span>현재가</span><strong>{_money(market_price)}</strong></div>
        <div class="metric"><span>목표가(기준)</span><strong>{_money(core_value)}</strong></div>
        <div class="metric"><span>상승여력</span><strong class="positive">{_pct(market_gaps['Core'], signed=True)}</strong></div>
        <div class="metric"><span>증권사 평균</span><strong>{_money(street_mean)}</strong></div>
        <div class="metric"><span>평가방법</span><strong>현금흐름할인법</strong></div>
      </aside>
    </div>
    <div class="scenario-strip">
      <div class="scenario"><small>하방</small><strong>{_money(down_value)}</strong><span class="negative">현재가 대비 {_pct(market_gaps['Down'], signed=True)}</span></div>
      <div class="scenario core"><small>기준</small><strong>{_money(core_value)}</strong><span class="positive">현재가 대비 {_pct(market_gaps['Core'], signed=True)}</span></div>
      <div class="scenario"><small>상방</small><strong>{_money(bull_value)}</strong><span class="positive">현재가 대비 {_pct(market_gaps['Bull'], signed=True)}</span></div>
    </div>
    <span class="pill">핵심 투자포인트 3가지</span>
    <div class="thesis-list">
      <div class="thesis"><b>01</b><p><strong>수요보다 슬롯이 먼저입니다.</strong> 상반기 영업이익률 37.4%, 가동률 87.2%, 수주잔고 5,567억원을 감안하면 신규공장은 기존 고마진 제품의 출하 제약을 푸는 투자입니다. {q2_ir_link} · {half_year_link}</p></div>
      <div class="thesis"><b>02</b><p><strong>마진 확대보다 절대이익 증가가 핵심입니다.</strong> 신규 부지의 물리적 매출 CAPA는 기존제품 2,936억원과 초고압 2,090억원, 합계 5,026억원입니다. 초고압 마진이 35%여도 전사 영업이익률은 약 40%를 유지합니다.</p></div>
      <div class="thesis"><b>03</b><p><strong>DCF 반영은 맞지만 전량 인정은 아직 이릅니다.</strong> 기준은 기존품목 CAPA 50%만 증분 매출로 인정하고, 회사가 3,000억원 안팎의 순증 CAPA·설비·시험동을 확인할 때 상방 가정을 기준으로 승격합니다. {disclosure_link}</p></div>
    </div>
    <p class="footnote">목표가는 확률가중값이 아니라 기준 시나리오 DCF입니다. 하방 위험이 큰 만큼 CAPA 확인 전에는 분할 접근이 적절합니다. 현재가 기준일 {escape(market_as_of)}. {market_link}</p>
  </section>

  <section class="page" data-page="02 / 04">
    <header class="masthead"><span>산일전기(062040) · 기업분석</span><span>생산능력과 현금흐름</span></header>
    <div class="section-head" style="margin-top:20px"><div><div class="eyebrow">생산능력·수익성</div><h2>부지 3.27만㎡가 5,026억원 매출 슬롯으로 연결된다</h2></div><p>단위: 억원<br>95% 유효가동 기준</p></div>
    <div class="two-col">
      <div class="box">
        <h3>현재 현금창출력</h3>
        <p>상반기 매출 {_billion(operating['revenue_h1_2026_krw_billion'])}, 영업이익 {_billion(operating['operating_profit_h1_2026_krw_billion'])}, 영업이익률 {_pct(Decimal(str(operating['operating_profit_h1_2026_krw_billion'])) / Decimal(str(operating['revenue_h1_2026_krw_billion'])))}입니다. 영업현금흐름에서 유·무형자산 취득을 뺀 단순 잉여현금흐름은 약 805억원입니다. {half_year_link}</p>
      </div>
      <div class="box">
        <h3>현재 제품 구성</h3>
        <p>특수변압기 76% · 전력망 일반변압기 21% · 기타 3%입니다. 높은 특수변압기 비중과 87.2% 가동률은 신규 부지의 경제성이 판매율보다 생산 슬롯에 좌우됨을 뜻합니다. {q2_ir_link}</p>
      </div>
    </div>
    <h3 style="margin-top:16px">신규공장 물리적 매출 생산능력(CAPA) 계산</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>구분</th><th>계산</th><th>연매출 CAPA</th><th>신규공장 믹스</th></tr></thead>
      <tbody>
        <tr><th>기존제품 명목</th><td>7,000억원 × 32,703㎡ / 37,040㎡ × 50%</td><td>{_billion(physical['existing_product_nameplate_capacity'])}</td><td>—</td></tr>
        <tr><th>기존제품 유효</th><td>명목 CAPA × 95%</td><td>{_billion(physical_existing)}</td><td>{_pct(physical['existing_product_mix'])}</td></tr>
        <tr><th>154kV 초고압</th><td>2,200억원 × 95%</td><td>{_billion(physical_uhv)}</td><td>{_pct(physical['uhv_mix'])}</td></tr>
        <tr><th>합계</th><td>기존제품 + 초고압</td><td><strong>{_billion(physical_total)}</strong></td><td>100.0%</td></tr>
      </tbody>
    </table></div>
    <p class="caption">37,040㎡·연 7,000억원은 증권사 추정, 32,703㎡는 공시에서 추출한 부지면적, 2,200억원은 분석가 중심값입니다. 면적 비례는 생산라인 밀도·시험설비·물류 공간이 같다는 가정이므로 물리적 상한으로 사용합니다. {ls_link}</p>
    <h3 style="margin-top:14px">성숙기 전사 실적과 정상화 잉여현금흐름</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>시나리오</th><th>기존제품 CAPA 인정</th><th>매출</th><th>영업이익</th><th>영업이익률</th><th>FCF</th></tr></thead>
      <tbody>{''.join(capacity_rows)}</tbody>
    </table></div>
    <div class="two-col">
      <div>
        <h3>전량가동 초고압 마진 민감도</h3>
        <table><thead><tr><th>초고압 마진</th><th>전사 영업이익</th><th>전사 마진</th></tr></thead><tbody>{''.join(margin_rows)}</tbody></table>
      </div>
      <div class="box warn">
        <h3>마진보다 절대금액</h3>
        <p>초고압 마진이 30~40%여도 전사 마진은 39.4~40.6%입니다. 기업잉여현금흐름은 세후영업이익 + 감가상각 1.0% − 유지투자 1.5% − 연간 증분매출의 운전자본 5%로 계산했습니다.</p>
      </div>
    </div>
    <p class="footnote">성숙기 수치는 회사 가이던스가 아닌 분석가 시나리오입니다. 기준은 기존품목 CAPA 50%, 상방은 100%를 매출로 인정합니다.</p>
  </section>

  <section class="page" data-page="03 / 04">
    <header class="masthead"><span>산일전기(062040) · 기업분석</span><span>가치평가와 논쟁</span></header>
    <div class="section-head" style="margin-top:20px"><div><div class="eyebrow">가치평가</div><h2>기준 목표가 {_money(core_value)}, 현재가 대비 {_pct(market_gaps['Core'], signed=True)}</h2></div><p>할인율 {_pct(wacc)}<br>영구성장률 2.5%</p></div>
    <div class="price-line">
      <div class="price-mark" style="left:{price_position(down_value)}">하방<br><strong>{_money(down_value)}</strong></div>
      <div class="price-mark current" style="left:{price_position(market_price)}">현재가<br><strong>{_money(market_price)}</strong></div>
      <div class="price-mark" style="left:{price_position(core_value)}">기준<br><strong>{_money(core_value)}</strong></div>
      <div class="price-mark" style="left:{price_position(bull_value)}">상방<br><strong>{_money(bull_value)}</strong></div>
    </div>
    <div class="table-wrap"><table>
      <thead><tr><th>시나리오</th><th>내재가치</th><th>현재가 대비</th><th>가능성*</th><th>전제</th></tr></thead>
      <tbody>{''.join(scenario_rows)}</tbody>
    </table></div>
    <p class="caption">* 가능성 산식: 하방·기준·상방 상대점수 3:5:2를 정규화해 30%·50%·20%로 표시했습니다. 실제 해결 이력이 없어 목표가를 확률가중하지 않았습니다.</p>
    <h3 style="margin-top:12px">5년차 현금흐름할인법 적용 현금흐름</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>시나리오</th><th>기존 사업</th><th>신규 부지 증분</th><th>평가 사용 합계</th></tr></thead>
      <tbody>{''.join(fcff_rows)}</tbody>
    </table></div>
    <div class="two-col">
      <div class="box"><h3>투자비 반영</h3><p>부동산 692.5억원에 생산·시험설비 분석가 가정 600억원을 별도 차감했습니다. 기존 제2공장 투자 420억원 전액이 아니라 2027년 이후 공시 잔여액 93.7억원만 미래 현금유출로 반영했습니다.</p></div>
      <div class="box risk"><h3>회수기간 경고</h3><p>기준 총 프로젝트 투자비 1,292.5억원 ÷ 성숙기 증분 세후영업이익은 약 {core_payback:.1f}년입니다. 이례적으로 짧아 이전·창고·시험동·인증비용과 중복매출을 반드시 재확인해야 합니다.</p></div>
    </div>
    <h3 style="margin-top:14px">증권사 목표가와의 차이</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>증권사</th><th>목표가</th><th>기준 및 평가방법</th><th>PRISM 기준가 대비</th></tr></thead>
      <tbody>{''.join(broker_rows)}</tbody>
    </table></div>
    <div class="debate"><strong>핵심 차이</strong><p>증권사는 예상 EPS에 PER을 적용합니다. PRISM은 부지·설비투자비를 차감하고 생산 슬롯이 매출·영업이익·FCF로 전환되는 연도별 경로를 할인합니다.</p></div>
    <div class="debate"><strong>중복 방지</strong><p>{kis_aug12_link}의 2025~2028년 매출 CAGR 32.5%와 2028년 영업이익률 40.7%는 신규공장·초고압 계획을 일부 포함할 가능성이 있습니다. 따라서 증권사 성장률을 신규 부지 CAPA에 기계적으로 더하지 않고, 기존제품 증분을 기준 50%로 제한했습니다.</p></div>
    <div class="ai" style="margin-top:12px">
      <h3>인공지능 인사이트 <small style="font-weight:500">· 1,000자 이내 별도 구분</small></h3>
      <p>{escape(ai_insight)}</p>
    </div>
    <p class="footnote">상방 승격 조건: 회사가 초고압 2,000억원 이상과 기존품목 약 3,000억원의 순증 CAPA, 생산·시험설비, 고객 인증 일정을 확인할 때. {kis_link}</p>
  </section>

  <section class="page" data-page="04 / 04">
    <header class="masthead"><span>산일전기(062040) · 기업분석</span><span>원문 출처</span></header>
    <div class="section-head" style="margin-top:20px"><div><div class="eyebrow">정보 출처</div><h2>수치와 판단을 바로 확인할 수 있는 원문</h2></div><p>클릭 시 원문 이동<br>자료 기준 2026.08.27</p></div>
    <ol class="source-list">{_source_register(data)}</ol>
    <h3 style="margin-top:16px">최종 요약 이미지 2장</h3>
    <div class="visuals">{_visual_cards(visuals)}</div>
    <p class="footnote">추가 원문: {half_year_link} · {q2_ir_link} · {disclosure_link} · {kis_aug12_link} · {kis_link} · {ls_link}. 상세 계산은 <a href="{escape(markdown_filename, quote=True)}" target="_blank">감사용 부속자료</a>에 보존했습니다. 본 자료는 공개자료를 바탕으로 한 조사 보고서이며 최종 투자판단은 독자에게 있습니다.</p>
  </section>
</main>
<details class="technical"><summary>감사용 부속자료 안내</summary><p>수치 재현과 원문 연결 검토가 필요할 때만 Markdown 부속자료를 여십시오. 투자 결론과 핵심 위험은 위 4쪽에 모두 포함돼 있습니다.</p></details>
</body>
</html>
'''
