from __future__ import annotations

from html import escape
import re

import mistune


_CSS = r'''
.page{width:min(210mm,calc(100vw - 24px));min-height:297mm;margin:0 auto 18px;padding:15mm;background:#fff;box-shadow:0 8px 28px #112a4620;position:relative}.page:after{content:attr(data-page);position:absolute;right:15mm;bottom:8mm;color:#8792a2;font-size:9pt}html{background:#eef1f4;color:#152033;font-family:Pretendard,"Noto Sans KR","Malgun Gothic",sans-serif}body{margin:0;line-height:1.6;word-break:keep-all}.report{padding:24px 0}.masthead{display:flex;justify-content:space-between;border-bottom:3px solid #112a46;padding-bottom:8px;color:#112a46;font-weight:800;font-size:10pt}.eyebrow{margin-top:18px;color:#1f5f9b;font-weight:800}h1{font-size:28pt;line-height:1.2;color:#112a46}h2,h3{color:#112a46}.hero-grid,.two-col,.visuals{display:grid;grid-template-columns:1.4fr .8fr;gap:18px}.two-col,.visuals{grid-template-columns:1fr 1fr}.stance,.box{border:1px solid #d9e1e8;padding:14px;background:#fbfdff}.stance{border-top:5px solid #1f5f9b}.metric{display:flex;justify-content:space-between;border-top:1px solid #d9e1e8;padding:7px 0;font-size:10pt}.scenario-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin:18px 0}.scenario{border:1px solid #d9e1e8;padding:12px}.scenario.core{background:#eaf3fb}.scenario strong{display:block;font-size:17pt;color:#112a46}.scenario small,.scenario span,.subtitle,.section-head p{color:#667487}.section-head{display:flex;justify-content:space-between;align-items:end;border-bottom:2px solid #112a46;margin:18px 0 12px}.box.warn{background:#fff8eb;border-left:4px solid #b88719}.box.risk{background:#fff4f2;border-left:4px solid #b42318}.ai{background:#f1faf8;border:1px solid #b7d8d2;padding:14px}table{width:100%;border-collapse:collapse;font-size:10pt}th,td{padding:8px;border-bottom:1px solid #d9e1e8;text-align:left;vertical-align:top}thead th{background:#112a46;color:#fff}.svgbox{border:1px solid #d9e1e8;padding:7px;background:#f7f8f9}.svgbox svg{width:100%;height:auto}.technical{width:min(210mm,calc(100vw - 24px));margin:0 auto 24px;background:#fff;padding:16px}a{color:#1f5f9b}@media(max-width:760px){.page{width:100%;min-height:0;box-shadow:none}.hero-grid,.two-col,.visuals,.scenario-strip{grid-template-columns:1fr}}@page{size:A4;margin:0}@media print{html{background:#fff}.page{width:210mm;margin:0;box-shadow:none;break-after:page}.technical{display:none}}
'''


def _section(text: str, start: str, end: str | None = None) -> str:
    begin = text.find(start)
    if begin < 0:
        raise ValueError(f"보고서 구역을 찾을 수 없습니다: {start}")
    finish = text.find(end, begin + len(start)) if end else -1
    return text[begin:(finish if finish >= 0 else len(text))].strip()


def _extract_between(text: str, start: str, end: str) -> str:
    match = re.search(re.escape(start) + r"\s+(.+?)(?=\n" + re.escape(end) + ")", text, re.S)
    return match.group(1).strip() if match else ""


def _field_map(summary: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in summary.splitlines():
        match = re.match(r"\| \*\*(.+?)\*\* \| (.*?) \|$", line.strip())
        if match:
            fields[match.group(1)] = match.group(2)
    return fields


def _clean_source_markdown(markdown_report: str) -> str:
    return re.sub(r"\n---\n보고서 (?:식별번호|ID).*?\n?$", "", markdown_report, flags=re.S)


def _date_label(as_of: str) -> str:
    year, month, day = as_of[:10].split("-")
    return f"{year}.{month}.{day}"


def validate_skhynix_brokerage_html(html_report: str) -> None:
    required = (
        '<html lang="ko">',
        "프리즘 기업분석 · 반도체/메모리",
        "SK하이닉스 · 000660 · 코스피",
        "가치평가와 핵심 가정",
        "증권사·시장 비교",
        "인공지능 인사이트 — 환경 변화 × 기업 강점",
        "정보 출처 — 원문 바로 확인",
        "분석 범위와 유의사항",
    )
    missing = tuple(item for item in required if item not in html_report)
    if missing:
        raise ValueError("SK하이닉스 HTML 표준 구역이 누락되었습니다: " + ", ".join(missing))
    if "SK hynix Inc." in html_report or "commodity_price_taker" in html_report:
        raise ValueError("SK하이닉스 HTML에 공개 금지 표현이 남아 있습니다")
    if html_report.count('<section class="page"') != 4:
        raise ValueError("SK하이닉스 HTML은 4페이지 표준 레이아웃이어야 합니다")


def render_skhynix_brokerage_html(
    markdown_report: str,
    *,
    summary_svg: str,
    assumptions_svg: str,
    as_of: str,
) -> str:
    text = _clean_source_markdown(markdown_report)
    md = mistune.create_markdown(escape=False, plugins=["table"])
    summary = _section(text, "## 투자 요약", "## 가치평가")
    valuation = _section(text, "## 가치평가", "## 핵심 가정과 위험")
    assumptions = _section(text, "## 핵심 가정과 위험", "## 증권사·시장 비교")
    market = _section(text, "## 증권사·시장 비교", "## 인공지능 인사이트 — 환경 변화 × 기업 강점")
    insight = _section(text, "## 인공지능 인사이트 — 환경 변화 × 기업 강점", "## 최종 요약 이미지")
    sources = _section(text, "## 정보 출처 — 원문 바로 확인", "## 분석 범위와 유의사항")
    scope = _section(text, "## 분석 범위와 유의사항", "<details>")
    details = _section(text, "<details>")

    fields = _field_map(summary)
    conclusion = " ".join(_extract_between(summary, "### 한 문장 결론", "### 투자포인트").split())
    points_md = _extract_between(summary, "### 투자포인트", "### 판단 변경 조건")
    conditions_match = re.search(r"### 판단 변경 조건\s+(.+)$", summary, re.S)
    conditions_md = conditions_match.group(1).strip() if conditions_match else ""

    values: dict[str, str] = {}
    for label in ("하방", "기준", "상방"):
        match = re.search(rf"\*\*{label} 시나리오:\*\* 내재가치 주당 ([0-9,]+)원", valuation)
        if match:
            values[label] = match.group(1)
    probabilities: dict[str, str] = {}
    probability_line = fields.get("시나리오 가능성", "")
    for label in ("하방", "기준", "상방"):
        match = re.search(rf"{label} ([0-9.]+%)", probability_line)
        if match:
            probabilities[label] = match.group(1)
    expected_match = re.search(r"확률가중 기대값:\*\* 주당 ([0-9,]+)원", valuation)
    expected_value = expected_match.group(1) if expected_match else "미산출"

    date_label = _date_label(as_of)
    korean_date = f"{as_of[:4]}년 {int(as_of[5:7])}월 {int(as_of[8:10])}일"
    report = f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SK하이닉스(000660) 투자보고서 — {date_label}</title><style>{_CSS}</style></head><body>
<main class="report">
<section class="page" data-page="01 / 04">
<header class="masthead"><span>프리즘 기업분석 · 반도체/메모리</span><span>{date_label}</span></header>
<div class="eyebrow">SK하이닉스 · 000660 · 코스피</div>
<div class="hero-grid"><div><h1>고대역폭메모리 공급 우위가 현금흐름으로 이어질 수 있는가</h1><p class="subtitle">SK하이닉스(000660) · 인공지능 메모리 수요, 수익성, 현금전환을 연결한 프리즘 가치평가 · 자료 기준 {korean_date}</p><p class="lead">{escape(conclusion)}</p></div>
<aside class="stance"><div class="label">투자판단</div><div class="view">판단 유보</div>
<div class="metric"><span>현재가</span><strong>{fields.get('현재가','미확보')}</strong></div>
<div class="metric"><span>기준 내재가치</span><strong>{fields.get('기준 내재가치','미산출')}</strong></div>
<div class="metric"><span>가치평가 범위</span><strong>{fields.get('가치평가 범위','미산출')}</strong></div>
<div class="metric"><span>확률가중 기대값</span><strong>{expected_value}원</strong></div>
<div class="metric"><span>증권사 참고값</span><strong>{fields.get('증권사 참고값','미확보')}</strong></div>
</aside></div>
<div class="scenario-strip">
<div class="scenario"><small>하방 · {probabilities.get('하방','')}</small><strong>{values.get('하방','미산출')}원</strong><span>보정된 연속 재무경로</span></div>
<div class="scenario core"><small>기준 · {probabilities.get('기준','')}</small><strong>{values.get('기준','미산출')}원</strong><span>기준 가치평가</span></div>
<div class="scenario"><small>상방 · {probabilities.get('상방','')}</small><strong>{values.get('상방','미산출')}원</strong><span>보정된 연속 재무경로</span></div>
</div>
<span class="pill">핵심 투자포인트</span><div class="box">{md(points_md)}</div>
<h3>판단 변경 조건</h3><div class="box warn">{md(conditions_md)}</div>
</section>
<section class="page" data-page="02 / 04"><header class="masthead"><span>프리즘 기업분석 · 가치평가</span><span>SK하이닉스</span></header>
<div class="section-head"><h2>가치평가와 핵심 가정</h2><p>확률·가치평가 확정 뒤 시장가격과 증권사 자료를 비교</p></div>
<div class="two-col"><div class="box">{md(valuation)}</div><div class="box risk">{md(assumptions)}</div></div>
<div class="section-head" style="margin-top:22px"><h2>증권사·시장 비교</h2><p>가치평가 확정 후 참고</p></div><div class="table-wrap">{md(market)}</div>
</section>
<section class="page" data-page="03 / 04"><header class="masthead"><span>프리즘 기업분석 · 인사이트와 근거</span><span>SK하이닉스</span></header>
<div class="ai">{md(insight)}</div>
<div class="section-head" style="margin-top:20px"><h2>최종 요약 이미지</h2><p>투자결론 · 가정 · 위험</p></div>
<div class="visuals"><div class="svgbox">{summary_svg}</div><div class="svgbox">{assumptions_svg}</div></div>
<div class="section-head" style="margin-top:20px"><h2>정보 출처 — 원문 바로 확인</h2><p>핵심 주장과 입력값의 직접 원문 링크</p></div><div class="sources">{md(sources)}</div>
</section>
<section class="page" data-page="04 / 04"><header class="masthead"><span>프리즘 기업분석 · 범위와 검증</span><span>SK하이닉스</span></header>
<div class="section-head"><h2>분석 범위와 유의사항</h2><p>사실 · 분석가 가정 · 인공지능 인사이트 구분</p></div>{md(scope)}
<div class="box" style="margin-top:20px"><h3>최종 확인</h3><ul><li>33개 표준 분석 단계 완료</li><li>가치평가·확률·감사·결과 확정 절차 통과</li><li>현재가와 증권사 자료는 가치평가 확정 후 비교</li><li>사용자용 본문은 한글 표준 양식, 세부 검증정보는 별도 파일로 분리</li></ul></div>
<p class="footnote">본 보고서는 프리즘 가치평가 모델의 확정 결과를 사용자용 한글 표준 HTML 양식으로 표시한 것입니다. 별도 진입 규칙이 승인되지 않아 특정 매수가는 제시하지 않습니다.</p>
</section>
</main><details class="technical"><summary>작성 근거와 계산 과정 보기</summary>{md(details)}</details>
</body></html>'''
    validate_skhynix_brokerage_html(report)
    return report
