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
        "Down": "가동·수주 전환 지연과 마진 정상화를 함께 반영",
        "Core": "공시된 투자와 점진적 가동 정상화를 반영",
        "Bull": "초고압 양산과 높은 현금창출력이 동시에 필요",
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
    linkage = data["context_strength_linkages"][0]

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
    bull_consumed = market_price / bull_value
    ai_insight = (
        f"{linkage.external_change} {linkage.linkage_thesis} "
        f"가치 포착 경로는 ‘{linkage.value_capture_path}’입니다. 다만 "
        f"{linkage.kill_conditions[0]}하거나, 생산능력이 출하로 전환되기 전에 "
        "수주가 둔화되면 이 해석은 폐기해야 합니다. 이 문단은 인공지능의 연결 "
        "가설이며 가치평가 수치와 시나리오 가정에는 관여하지 않았습니다."
    )
    if len(ai_insight) > 1000:
        raise ValueError("인공지능 인사이트는 1,000자를 초과할 수 없습니다")

    disclosure_link = _source_anchor(
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260826000660",
        "DART 유형자산 양수결정 원문",
    )
    company_list_link = _source_anchor(
        "https://www.sanil.co.kr/kr/sub/reference/announce.php",
        "회사 공시목록",
    )
    market_link = _source_anchor(market.observation.source_ref, "현재가 원문")
    kis_link = _source_anchor(
        "https://securities.koreainvestment.com/main/research/research/StrategyDetail.jsp?id=158730&jkGubun=6",
        "한국투자증권 8월 27일 보고서",
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
    <header class="masthead"><span>PRISM EQUITY RESEARCH · 전력기기</span><span>2026.08.27</span></header>
    <div class="eyebrow" style="margin-top:20px">산일전기 · 062040 · KOSPI</div>
    <div class="hero-grid">
      <div>
        <h1>증설 발표보다 중요한 것은 현금흐름 전환 속도</h1>
        <p class="subtitle">8월 26일 초고압 생산용 부동산 취득 공시 반영 · 자료 기준 2026년 8월 27일</p>
        <p class="lead">회사의 수요 환경은 우호적이지만 현재가는 기준 내재가치를 19.8% 웃돌고 상방가의 {_pct(bull_consumed)}까지 반영합니다. 초고압 설비의 생산능력·양산시점·현금창출력이 공시로 연결되기 전까지는 <strong>관망</strong>이 합리적입니다.</p>
      </div>
      <aside class="stance">
        <div class="label">투자판단</div><div class="view">관망</div>
        <div class="metric"><span>현재가</span><strong>{_money(market_price)}</strong></div>
        <div class="metric"><span>기준 내재가치</span><strong>{_money(core_value)}</strong></div>
        <div class="metric"><span>기준가 대비</span><strong class="negative">{_pct(market_gaps['Core'], signed=True)}</strong></div>
        <div class="metric"><span>증권사 평균</span><strong>{_money(street_mean)}</strong></div>
        <div class="metric"><span>평가방법</span><strong>현금흐름할인법</strong></div>
      </aside>
    </div>
    <div class="scenario-strip">
      <div class="scenario"><small>하방</small><strong>{_money(down_value)}</strong><span class="negative">현재가 대비 {_pct(market_gaps['Down'], signed=True)}</span></div>
      <div class="scenario core"><small>기준</small><strong>{_money(core_value)}</strong><span class="negative">현재가 대비 {_pct(market_gaps['Core'], signed=True)}</span></div>
      <div class="scenario"><small>상방</small><strong>{_money(bull_value)}</strong><span class="positive">현재가 대비 {_pct(market_gaps['Bull'], signed=True)}</span></div>
    </div>
    <span class="pill">핵심 투자포인트 3가지</span>
    <div class="thesis-list">
      <div class="thesis"><b>01</b><p><strong>회사가 확정한 것은 부지와 매매대금입니다.</strong> 안산 토지·건물 692.5억원, 자기자금 지급, 초고압 변압기 생산시설 및 기존 제품 생산능력 확대 목적은 확인됐습니다. {disclosure_link}</p></div>
      <div class="thesis"><b>02</b><p><strong>주가에는 상방 시나리오가 상당 부분 반영돼 있습니다.</strong> 현재가는 기준가보다 33,277원 높고 상방가까지 남은 폭은 15,604원에 불과합니다. {market_link}</p></div>
      <div class="thesis"><b>03</b><p><strong>차이를 메울 증거는 생산능력보다 현금전환입니다.</strong> 고객 인증·수주, 상업 생산시점, 제품별 매출총이익률, 추가 건설·설비투자비가 확인돼야 기준가를 다시 높일 수 있습니다.</p></div>
    </div>
    <p class="footnote">자료 기준: 회사 공시·IR, 한국거래소 공시, 증권사 원문, 시장가격 원문. 현재가 기준일 {escape(market_as_of)}. 회사 공시와 증권사 추정은 구분해 표시했습니다.</p>
  </section>

  <section class="page" data-page="02 / 04">
    <header class="masthead"><span>산일전기(062040) · 기업분석</span><span>가치평가</span></header>
    <div class="section-head" style="margin-top:20px"><div><div class="eyebrow">VALUATION</div><h2>현재가는 기준가보다 상방가에 가깝다</h2></div><p>단위: 원, 억원<br>현재가 {escape(market_as_of)}</p></div>
    <div class="price-line">
      <div class="price-mark" style="left:4%">하방<br><strong>{_money(down_value)}</strong></div>
      <div class="price-mark" style="left:50%">기준<br><strong>{_money(core_value)}</strong></div>
      <div class="price-mark current" style="left:81%">현재가<br><strong>{_money(market_price)}</strong></div>
      <div class="price-mark" style="left:96%">상방<br><strong>{_money(bull_value)}</strong></div>
    </div>
    <div class="table-wrap"><table>
      <thead><tr><th>시나리오</th><th>내재가치</th><th>현재가 대비</th><th>가능성*</th><th>전제</th></tr></thead>
      <tbody>{''.join(scenario_rows)}</tbody>
    </table></div>
    <p class="caption">* 가능성 산식: 하방·기준·상방 상대점수 3:5:2를 합계 10으로 나눠 30%·50%·20%로 표시했습니다. 실제 해결 이력으로 보정되지 않아 확률가중 기대가치나 특정 매수가 산정에는 사용하지 않았습니다.</p>
    <h3 style="margin-top:18px">5년차 잉여현금흐름 연결</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>시나리오</th><th>기존 사업</th><th>초고압 증분</th><th>평가 사용 합계</th></tr></thead>
      <tbody>{''.join(fcff_rows)}</tbody>
    </table></div>
    <div class="two-col">
      <div class="box">
        <h3>평가의 핵심</h3>
        <p>기준 시나리오는 5년차 기존사업 2,700억원과 초고압 증분 420억원을 합친 3,120억원을 사용합니다. 계층형 베타 {beta:.3f}, 가중평균자본비용 {_pct(wacc)}, 영구성장률 2.5%를 적용했습니다.</p>
      </div>
      <div class="box risk">
        <h3>가장 큰 수치 위험</h3>
        <p>기준 기업가치의 {_pct(terminal_value_share)}가 영구가치입니다. 양산 지연이나 정상 마진 하락은 먼 미래 현금흐름을 통해 가치에 크게 반영됩니다.</p>
      </div>
    </div>
    <h3 style="margin-top:18px">8월 26일 공시가 확정한 것과 남긴 빈칸</h3>
    <div class="two-col">
      <div class="box">
        <h3>확정 사실</h3>
        <ul><li>안산 토지·건물 692.5억원</li><li>계약금 69.25억원, 중도금 207.75억원, 잔금 415.5억원</li><li>자기자금 지급, 초고압 및 기존 제품 생산능력 확대 목적</li></ul>
      </div>
      <div class="box warn">
        <h3>아직 확인되지 않은 것</h3>
        <ul><li>추가 생산능력과 제품 구성</li><li>건설·설비·세금·수수료 포함 총투자비</li><li>고객 인증, 수주, 양산시점, 매출·마진 기여</li></ul>
      </div>
    </div>
    <p class="footnote">현재 모델은 제2공장 420억원과 초고압 부동산 692.5억원을 2년차 현금유출로 단순화합니다. 공시된 2026~2027년 분할 지급일을 반영하면 현재가치가 달라질 수 있습니다. {disclosure_link}</p>
  </section>

  <section class="page" data-page="03 / 04">
    <header class="masthead"><span>산일전기(062040) · 기업분석</span><span>논쟁과 위험</span></header>
    <div class="section-head" style="margin-top:20px"><div><div class="eyebrow">STREET DEBATE</div><h2>증권사 목표가와의 차이는 성장률보다 ‘평가 방식’에서 시작한다</h2></div><p>증권사 평균 {_money(street_mean)}<br>PRISM 기준 {_money(core_value)}</p></div>
    <div class="table-wrap"><table>
      <thead><tr><th>증권사</th><th>목표가</th><th>기준 및 평가방법</th><th>PRISM 기준가 대비</th></tr></thead>
      <tbody>{''.join(broker_rows)}</tbody>
    </table></div>
    <div class="debate"><strong>핵심 차이</strong><p>증권사는 주로 2027~2028년 예상 주당순이익(EPS)에 목표 주가수익비율(PER)을 적용합니다. PRISM은 공시된 투자비를 먼저 차감하고 생산능력이 실제 잉여현금흐름으로 전환되는 경로를 할인합니다.</p></div>
    <div class="debate"><strong>최대 편차</strong><p>신한투자증권 310,000원과 IBK투자증권 220,000원의 차이는 90,000원, 40.9%입니다. 기준연도와 적용 배수가 달라 단순 평균만으로 적정성을 판단하기 어렵습니다.</p></div>
    <div class="debate"><strong>8월 27일 해석</strong><p>{kis_link}는 2028년 양산, 2029년 추가 매출 2,000억원 이상, 목표가 270,000원을 제시했습니다. 이는 회사 확정치가 아니라 증권사 추정이며, 기준 가치평가가 끝난 뒤 비교에만 사용했습니다.</p></div>
    <h3 style="margin-top:16px">판단을 바꾸는 세 가지 확인 조건</h3>
    <div class="conditions">
      <div class="condition"><h3>상방 확인</h3><p>제2공장·초고압 설비 일정, 고객 인증, 가동률, 수주잔고의 매출 전환이 회사 자료에서 확인될 때.</p></div>
      <div class="condition"><h3>하방 확인</h3><p>증설 지연·취소, 신규수주 둔화, 높은 가동비용과 마진 하락이 출하 효과를 상쇄할 때.</p></div>
      <div class="condition"><h3>매수 판단 가능</h3><p>실제 사건 해결 이력이 쌓여 확률이 보정되고, 총투자비와 양산 현금흐름 연결이 완성될 때.</p></div>
    </div>
    <div class="ai" style="margin-top:15px">
      <h3>인공지능 인사이트 <small style="font-weight:500">· 1,000자 이내 별도 구분</small></h3>
      <p>{escape(ai_insight)}</p>
    </div>
    <p class="footnote">인공지능은 외부 변화와 기업 강점의 연결 가설만 작성했습니다. 회사 사실, 시나리오 입력, 할인율, 내재가치 계산은 별도 결정론적 절차에서 산출했습니다. 8월 27일 신규·정정 공시는 없으며 최신 회사 공시는 8월 26일 자료입니다. {company_list_link}</p>
  </section>

  <section class="page" data-page="04 / 04">
    <header class="masthead"><span>산일전기(062040) · 기업분석</span><span>원문 출처</span></header>
    <div class="section-head" style="margin-top:20px"><div><div class="eyebrow">SOURCE REGISTER</div><h2>수치와 판단을 바로 확인할 수 있는 원문</h2></div><p>클릭 시 원문 이동<br>자료 기준 2026.08.27</p></div>
    <ol class="source-list">{_source_register(data)}</ol>
    <h3 style="margin-top:16px">최종 요약 이미지 2장</h3>
    <div class="visuals">{_visual_cards(visuals)}</div>
    <p class="footnote">상세 근거 식별자, 계산 과정, 33단계 처리 기록은 <a href="{escape(markdown_filename, quote=True)}" target="_blank">감사용 부속자료</a>에 보존했습니다. 독자용 본문에서는 내부 상태명·해시·개발자 용어를 제거했습니다. 본 자료는 공개자료를 바탕으로 한 조사 보고서이며 최종 투자판단은 독자에게 있습니다.</p>
  </section>
</main>
<details class="technical"><summary>감사용 부속자료 안내</summary><p>수치 재현과 원문 연결 검토가 필요할 때만 Markdown 부속자료를 여십시오. 투자 결론과 핵심 위험은 위 4쪽에 모두 포함돼 있습니다.</p></details>
</body>
</html>
'''
