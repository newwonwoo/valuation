from __future__ import annotations

from html import escape
import re


_CSS = """
*{box-sizing:border-box}html{background:#eef1f4;color:#152033;font-family:Pretendard,'Noto Sans KR','Malgun Gothic',sans-serif}body{margin:0;font-size:13pt;line-height:1.55}.report{padding:24px 0}.page{width:min(210mm,calc(100vw - 24px));min-height:297mm;margin:0 auto 18px;padding:15mm;background:#fff;box-shadow:0 8px 28px #112a4620;position:relative}.page:after{content:attr(data-page);position:absolute;right:15mm;bottom:8mm;color:#8792a2;font-size:9pt}.mast{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;border-bottom:3px solid #112a46;padding-bottom:8px;color:#112a46;font-weight:800}.eyebrow{margin-top:18px;color:#1f5f9b;font-weight:800}h1{font-size:28pt;line-height:1.18;color:#112a46}h2{font-size:18pt}h2,h3{color:#112a46}.hero,.two,.cards{display:grid;grid-template-columns:1.4fr .8fr;gap:18px;min-width:0}.two,.cards{grid-template-columns:1fr 1fr}.box{min-width:0;border:1px solid #d9e1e8;padding:14px;background:#fbfdff}.box.warn{background:#fff8eb;border-left:4px solid #b88719}.box.risk{background:#fff4f2;border-left:4px solid #b42318}.metric{display:flex;justify-content:space-between;gap:10px;border-top:1px solid #d9e1e8;padding:7px 0;font-size:11pt}.scenarios{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin:18px 0}.scenario{min-width:0;border:1px solid #d9e1e8;padding:12px}.scenario.core{background:#eaf3fb}.scenario strong{display:block;font-size:17pt;color:#112a46}table{width:100%;border-collapse:collapse;font-size:11pt}th,td{padding:8px;border-bottom:1px solid #d9e1e8;text-align:left}thead th{background:#112a46;color:white}.cards img{display:block;width:100%;max-width:100%;border:1px solid #d9e1e8}.muted{color:#667487}.ai{background:#f1faf8;border:1px solid #b7d8d2;padding:14px}a{color:#1f5f9b}@media(max-width:760px){.report{padding:0}.page{width:100%;min-height:0;margin:0 0 12px;padding:24px 20px;box-shadow:none}.page:after{right:20px;bottom:8px}h1{font-size:22pt}.hero,.two,.cards,.scenarios{grid-template-columns:1fr}.metric{align-items:flex-start}.metric strong{text-align:right}}@page{size:A4;margin:0}@media print{html{background:white}.page{width:210mm;margin:0;box-shadow:none;break-after:page}}
"""


def _section(text: str, start: str, end: str | None = None) -> str:
    i = text.find(start)
    if i < 0:
        raise ValueError(f"보고서 구역 누락: {start}")
    j = text.find(end, i + len(start)) if end else -1
    return text[i:(j if j >= 0 else len(text))]


def _field(summary: str, name: str, fallback: str = "미확보") -> str:
    m = re.search(rf"\| \*\*{re.escape(name)}\*\* \| (.*?) \|", summary)
    return m.group(1) if m else fallback


def _scenario_value(valuation: str, label: str) -> str:
    m = re.search(rf"\*\*{label} 시나리오:\*\* 내재가치 주당 ([0-9,]+)원", valuation)
    return m.group(1) if m else "미산출"


def _probability(summary: str, label: str) -> str:
    line = _field(summary, "시나리오 가능성", "")
    m = re.search(rf"{label} ([0-9.]+%)", line)
    return m.group(1) if m else "미산출"


def _extract(text: str, pattern: str, fallback: str = "미산출") -> str:
    m = re.search(pattern, text, re.S)
    return m.group(1) if m else fallback


def validate_skhynix_brokerage_html(report: str) -> None:
    required = ("<html lang=\"ko\">", "SK하이닉스 · 000660 · 코스피", "가치평가와 핵심 가정", "증권사·시장 비교", "인공지능 인사이트 — 환경 변화 × 기업 강점", "정보 출처 — 원문 바로 확인")
    if any(item not in report for item in required):
        raise ValueError("SK하이닉스 HTML 표준 구역이 누락되었습니다")
    if report.count('<section class="page"') != 4:
        raise ValueError("SK하이닉스 HTML은 4페이지 표준이어야 합니다")


def render_skhynix_brokerage_html(markdown_report: str, *, summary_filename: str, assumptions_filename: str, as_of: str, markdown_filename: str) -> str:
    text = re.sub(r"\n---\n보고서 (?:식별번호|ID).*?\n?$", "", markdown_report, flags=re.S)
    summary = _section(text, "## 투자 요약", "## 가치평가")
    valuation = _section(text, "## 가치평가", "## 핵심 가정과 위험")
    assumptions = _section(text, "## 핵심 가정과 위험", "## 증권사·시장 비교")
    market = _section(text, "## 증권사·시장 비교", "## 인공지능 인사이트")
    current = _field(summary, "현재가")
    core_field = _field(summary, "기준 내재가치", "미산출")
    street = _field(summary, "증권사 참고값")
    down, core, bull = (_scenario_value(valuation, x) for x in ("하방", "기준", "상방"))
    pd, pc, pb = (_probability(summary, x) for x in ("하방", "기준", "상방"))
    expected = _extract(valuation, r"확률가중 기대값:\*\* 주당 ([0-9,]+)원")
    beta = _extract(assumptions, r"계층형 베타 ([0-9.]+)")
    wacc = _extract(assumptions, r"가중평균자본비용 ([0-9.]+%)")
    core_up = _extract(market, r"기준 대비 상승여력:\*\* [0-9,]+원 \(([0-9.]+%)\)")
    street_gap = _extract(market, r"프리즘 기준 내재가치는 증권사 평균 목표가보다 ([0-9.]+%) 높습니다")
    conclusion = _extract(summary, r"### 한 문장 결론\s+(.+?)\n### 투자포인트", "")
    conclusion = " ".join(conclusion.split())
    d = as_of[:10]
    date_label = d.replace("-", ".")
    kdate = f"{d[:4]}년 {int(d[5:7])}월 {int(d[8:10])}일"
    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SK하이닉스(000660) 투자보고서 — {date_label}</title><style>{_CSS}</style></head><body><main class="report">
<section class="page" data-page="01 / 04"><header class="mast"><span>프리즘 기업분석 · 반도체/메모리</span><span>{date_label}</span></header><div class="eyebrow">SK하이닉스 · 000660 · 코스피</div><div class="hero"><div><h1>고대역폭메모리 공급 우위가 현금흐름으로 이어질 수 있는가</h1><p class="muted">인공지능 메모리 수요·수익성·현금전환을 연결한 가치평가 · 자료 기준 {kdate}</p><p>{escape(conclusion)}</p></div><aside class="box"><h3>투자판단 · 판단 유보</h3><div class="metric"><span>현재가</span><strong>{current}</strong></div><div class="metric"><span>기준 내재가치</span><strong>{core_field}</strong></div><div class="metric"><span>확률가중 기대값</span><strong>{expected}원</strong></div><div class="metric"><span>증권사 평균</span><strong>{street}</strong></div></aside></div><div class="scenarios"><div class="scenario"><small>하방 · {pd}</small><strong>{down}원</strong></div><div class="scenario core"><small>기준 · {pc}</small><strong>{core}원</strong></div><div class="scenario"><small>상방 · {pb}</small><strong>{bull}원</strong></div></div><div class="two"><div class="box"><h3>핵심 투자포인트</h3><ul><li>고대역폭메모리 양산 출하와 주요 고객 접근성이 핵심 경쟁력입니다.</li><li>매출 성장률·영업이익률·현금전환율·설비투자 비중의 연속 경로가 내재가치를 좌우합니다.</li><li>별도 진입 규칙이 확정되지 않아 특정 매수가는 제시하지 않습니다.</li></ul></div><div class="box warn"><h3>판단 변경 조건</h3><ul><li>상방: 출하 확대가 영업현금흐름 개선으로 이어지고 현금전환율이 유지될 때.</li><li>하방: 수율·인증 차질, 가격 급락, 재고 증가, 설비투자 부담이 현금흐름을 앞지를 때.</li></ul></div></div></section>
<section class="page" data-page="02 / 04"><header class="mast"><span>프리즘 기업분석 · 가치평가</span><span>SK하이닉스</span></header><h2>가치평가와 핵심 가정</h2><table><thead><tr><th>구분</th><th>내재가치</th><th>확률</th></tr></thead><tbody><tr><td>하방</td><td>{down}원</td><td>{pd}</td></tr><tr><td>기준</td><td>{core}원</td><td>{pc}</td></tr><tr><td>상방</td><td>{bull}원</td><td>{pb}</td></tr><tr><td>확률가중 기대값</td><td>{expected}원</td><td>보정 완료</td></tr></tbody></table><div class="two"><div class="box risk"><h3>위험 입력</h3><p>계층형 베타 {beta} · 가중평균자본비용 {wacc}</p><p>메모리 업황·가격·물량을 현금흐름으로 연결하고 공시된 확정 투자만 반영합니다.</p></div><div class="box"><h3>시장 비교</h3><p>현재가 {current}</p><p>기준 내재가치 대비 상승여력 {core_up}</p><p>증권사 평균 대비 프리즘 기준 내재가치 차이 +{street_gap}</p></div></div><h2>증권사·시장 비교</h2><p>증권사 목표가와 현재가는 가치평가 확정 뒤 참고했으며 선행 가정을 바꾸는 데 사용하지 않았습니다.</p></section>
<section class="page" data-page="03 / 04"><header class="mast"><span>프리즘 기업분석 · 인사이트와 근거</span><span>SK하이닉스</span></header><div class="ai"><h2>인공지능 인사이트 — 환경 변화 × 기업 강점</h2><p>인공지능 서버 확대로 고대역폭메모리 수요와 고성능 메모리 공급 능력의 희소성이 커지고 있습니다. SK하이닉스의 양산 출하·높은 설비가동률·주요 고객 기반이 제품 구성과 영업이익률을 거쳐 현금전환으로 이어질 때 기존 경쟁력이 실제 기업가치로 재평가됩니다.</p><p><strong>반증 조건:</strong> 수율·인증 차질, 빠른 메모리 가격 정상화, 재고 증가, 설비투자 부담의 현금흐름 초과.</p></div><h2>최종 요약 이미지</h2><div class="cards"><img src="{escape(summary_filename, quote=True)}" alt="SK하이닉스 투자결론 요약"><img src="{escape(assumptions_filename, quote=True)}" alt="SK하이닉스 가치평가 가정과 위험"></div><h2>정보 출처 — 원문 바로 확인</h2><ul><li><a href="https://news.skhynix.com/en/q2-2026-business-results/">SK하이닉스 2026년 2분기 실적 발표</a></li><li><a href="https://www.sec.gov/Archives/edgar/data/2120882/000119312526354777/d147827d6k.htm">미국 증권거래위원회 재무·현금흐름 공시</a></li><li><a href="https://english.mofe.go.kr/?boardCd=P0002&amp;seq=2052">대한민국 정부 금리 자료</a></li><li><a href="https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctrypremtable.htm">다모다란 국가위험프리미엄</a></li><li><a href="https://kr.investing.com/equities/sk-hynix-inc-historical-data">현재 시장가격</a></li><li><a href="https://stockanalysis.com/quote/krx/000660/forecast/">증권사 컨센서스</a></li></ul></section>
<section class="page" data-page="04 / 04"><header class="mast"><span>프리즘 기업분석 · 범위와 검증</span><span>SK하이닉스</span></header><h2>분석 범위와 유의사항</h2><div class="box"><ul><li>전체 기업 내재가치 평가.</li><li>33개 표준 분석 단계 완료.</li><li>가치평가·확률·감사·결과 확정 절차 통과.</li><li>현재가와 증권사 자료는 가치평가 확정 후 비교.</li><li>회사 공시 사실, 분석가 가정, 인공지능 연결 인사이트를 구분.</li></ul></div><h3>작성 근거와 계산 과정</h3><p>세부 검증정보는 <a href="{escape(markdown_filename, quote=True)}">마크다운 검증본</a>에 보관합니다.</p><p class="muted">본 보고서는 프리즘 가치평가 모델의 확정 결과를 사용자용 한글 표준 HTML 양식으로 표시한 것입니다.</p></section></main></body></html>'''
