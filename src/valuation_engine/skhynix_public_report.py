from __future__ import annotations

import re


PUBLIC_REQUIRED_HEADINGS = (
    "## 투자 요약",
    "## 가치평가",
    "## 핵심 가정과 위험",
    "## 증권사·시장 비교",
    "## 인공지능 인사이트 — 환경 변화 × 기업 강점",
    "## 최종 요약 이미지",
    "## 정보 출처 — 원문 바로 확인",
    "## 분석 범위와 유의사항",
)

_FORBIDDEN_PUBLIC_TOKENS = (
    "SK hynix Inc.",
    "post-freeze",
    "frozen LIVE",
    "commodity_price_taker",
    "COMPANY_RESOLUTION",
    "E:SKHYNIX:",
    "단계 기술 식별자:",
    "초고압",
)


def _replace_section(
    text: str,
    start_heading: str,
    end_heading: str,
    replacement: str,
) -> str:
    start = text.find(start_heading)
    end = text.find(end_heading, start + len(start_heading))
    if start < 0 or end < 0:
        raise RuntimeError(
            f"표준 보고서 구역을 찾을 수 없습니다: {start_heading} → {end_heading}"
        )
    return text[:start] + replacement.rstrip() + "\n\n" + text[end:]


def _replace_block(
    text: str,
    start_marker: str,
    end_marker: str,
    replacement: str,
) -> str:
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise RuntimeError(
            f"표준 보고서 블록을 찾을 수 없습니다: {start_marker} → {end_marker}"
        )
    return text[:start] + replacement.rstrip() + "\n\n" + text[end:]


def validate_skhynix_public_report(report: str) -> None:
    positions = tuple(report.find(heading) for heading in PUBLIC_REQUIRED_HEADINGS)
    if any(position < 0 for position in positions) or positions != tuple(sorted(positions)):
        raise RuntimeError("SK하이닉스 공개 보고서가 표준 구역 순서를 따르지 않습니다")
    if not report.startswith("# SK하이닉스(000660) 투자보고서"):
        raise RuntimeError("SK하이닉스 공개 보고서 제목이 표준형이 아닙니다")
    forbidden = tuple(token for token in _FORBIDDEN_PUBLIC_TOKENS if token in report)
    if forbidden:
        raise RuntimeError(
            "SK하이닉스 공개 보고서에 금지된 영문·내부 표현이 남아 있습니다: "
            + ", ".join(forbidden)
        )
    scrubbed = re.sub(r"https?://[^\s)]+", "", report)
    scrubbed = re.sub(r"PRISM_[A-Za-z0-9_.-]+", "", scrubbed)
    if re.search(r"\b[A-Z]{2,}_[A-Z0-9_]+\b", scrubbed):
        raise RuntimeError("SK하이닉스 공개 보고서에 내부 기술 식별자가 남아 있습니다")
    if "github.com/newwonwoo/valuation" in report:
        raise RuntimeError("공개 보고서에는 내부 가치평가 저장소 링크를 노출하지 않습니다")


def render_skhynix_public_report(report: str) -> str:
    """Convert the frozen canonical report into the Korean public standard form.

    This layer changes presentation only. It does not recompute or alter assumptions,
    probabilities, valuation, Audit, Freeze, market observations, or Street data.
    """
    positions = tuple(report.find(heading) for heading in PUBLIC_REQUIRED_HEADINGS)
    if any(position < 0 for position in positions) or positions != tuple(sorted(positions)):
        raise RuntimeError("canonical report does not match the verified report-form order")

    rendered = report
    common_replacements = {
        "# SK hynix Inc. 투자보고서": "# SK하이닉스(000660) 투자보고서",
        "SK hynix Inc.": "SK하이닉스",
        "S&P Global consensus via StockAnalysis": "에스앤피 글로벌 컨센서스(스톡애널리시스 집계)",
        "2026년 기준 · post-freeze consensus reference only": "2026년 기준 · 가치평가 확정 후 참고용 컨센서스",
        "2026년 post-freeze consensus reference only": "2026년 가치평가 확정 후 참고용 컨센서스",
        "SK hynix frozen LIVE source pack": "SK하이닉스 원문 근거 묶음",
        "1년차 DCF 사용 FCFF": "1년차 현금흐름할인법 적용 기업잉여현금흐름",
        "5년차 DCF 사용 FCFF": "5년차 현금흐름할인법 적용 기업잉여현금흐름",
        "영구 ROIC": "영구 투하자본이익률",
        "하방 (Down)": "하방",
        "기준 (Core)": "기준",
        "상방 (Bull)": "상방",
        "하방(Down)": "하방",
        "기준(Core)": "기준",
        "상방(Bull)": "상방",
    }
    for source, target in common_replacements.items():
        rendered = rendered.replace(source, target)

    rendered = re.sub(r"(?<![A-Z0-9_])PRISM(?![A-Z0-9_])", "프리즘", rendered)
    rendered = re.sub(r"\bDCF\b", "현금흐름할인법", rendered)
    rendered = re.sub(r"\bFCFF\b", "기업잉여현금흐름", rendered)
    rendered = re.sub(r"\bROIC\b", "투하자본이익률", rendered)
    rendered = re.sub(r"\bCAPA\b", "생산능력", rendered)

    conclusion = """### 한 문장 결론

4세대 고대역폭메모리 양산 출하, 높은 설비가동률, 주요 고객과의 장기계약이 인공지능 메모리 수요를 현금흐름으로 전환하는 핵심 축입니다. 기준 내재가치는 3,542,393원, 확률가중 기대값은 3,726,580원이며, 현재가에서는 메모리 업황 정상화·대규모 설비투자·현금전환 지속 여부를 함께 확인해야 합니다."""
    rendered = _replace_block(
        rendered,
        "### 한 문장 결론",
        "### 투자포인트",
        conclusion,
    )

    investment_points = """### 투자포인트

- **사업모델과 강점:** 고대역폭메모리·디램·기업용 저장장치를 인공지능·서버 고객에 공급하며, 4세대 고대역폭메모리 양산 출하와 주요 고객 접근성이 핵심 경쟁력입니다.
- **가치동인:** 매출 성장률·영업이익률·현금전환율·설비투자 비중의 연속 경로가 현금흐름과 내재가치를 좌우합니다.
- **가치평가:** 하방 1,069,224원 · 기준 3,542,393원 · 상방 4,963,295원, 확률가중 기대값 3,726,580원입니다.
- **핵심 위험:** 메모리 가격 정상화, 고대역폭메모리 수율·고객 인증, 대규모 설비투자의 현금흐름 부담, 높은 이익률의 정상화 속도를 함께 봐야 합니다.
- **행동 기준:** 별도 진입 규칙이 확정되지 않아 특정 매수가는 제시하지 않습니다."""
    rendered = _replace_block(
        rendered,
        "### 투자포인트",
        "### 판단 변경 조건",
        investment_points,
    )

    change_conditions = """### 판단 변경 조건

- **상방 확인:** 4세대 고대역폭메모리 출하 확대가 매출·영업이익률·영업현금흐름 개선으로 이어지고, 증설 이후에도 현금전환율이 유지될 때.
- **하방 훼손:** 고대역폭메모리 수율·고객 인증 차질, 메모리 가격 급락, 재고 증가, 설비투자 확대가 현금흐름을 앞지를 때.
- **행동 가능 조건:** 확률 보정 결과와 별도로 승인된 진입 규칙이 마련될 때."""
    rendered = _replace_block(
        rendered,
        "### 판단 변경 조건",
        "## 가치평가",
        change_conditions,
    )

    insight = """## 인공지능 인사이트 — 환경 변화 × 기업 강점

- **환경 변화:** 인공지능 서버 확대로 고대역폭메모리 수요와 고성능 메모리 공급 능력의 희소성이 커지고 있습니다.
- **기업 강점:** SK하이닉스는 4세대 고대역폭메모리 양산 출하를 시작했고, 높은 설비가동률과 주요 고객과의 장기계약 기반을 보유하고 있습니다.
- **연결 논리:** 고객 인증과 공급 능력의 우위가 제품 구성·가격·가동률을 거쳐 영업이익과 현금전환으로 이어질 때 기존 경쟁력이 실제 기업가치로 재평가됩니다.
- **가치 포착 경로:** 고대역폭메모리 수요 → 제품 구성·가동률 → 영업이익률 → 현금전환 → 재투자 후 기업잉여현금흐름.
- **반증 조건:** 수율·인증 차질, 예상보다 빠른 메모리 가격 정상화, 재고 증가, 설비투자 부담이 현금흐름 개선보다 빨라지는 경우입니다.
- **다음 확인:** 고대역폭메모리 출하, 재고와 판매가격, 설비투자 집행, 영업현금흐름의 동반 개선 여부입니다."""
    rendered = _replace_section(
        rendered,
        "## 인공지능 인사이트 — 환경 변화 × 기업 강점",
        "## 최종 요약 이미지",
        insight,
    )

    sources = """## 정보 출처 — 원문 바로 확인

- **SK하이닉스 2026년 2분기 실적 발표:** 4세대 고대역폭메모리 양산 출하, 주요 고객 장기계약, 실적과 설비가동 관련 회사 발표 — [원문 바로 열기](https://news.skhynix.com/en/q2-2026-business-results/)
- **미국 증권거래위원회 공시:** 용인 반도체 클러스터 생산능력 투자 관련 이사회 승인 — [원문 바로 열기](https://www.sec.gov/Archives/edgar/data/2120882/000119312526311230/d121520d6k.htm)
- **미국 증권거래위원회 공시:** 재무상태·현금흐름·생산 관련 공시 — [원문 바로 열기](https://www.sec.gov/Archives/edgar/data/2120882/000119312526354777/d147827d6k.htm)
- **미국 증권거래위원회 공시:** 자사주 취득·주식수 관련 공시 — [원문 바로 열기](https://www.sec.gov/Archives/edgar/data/2120882/000119312526356141/d436722d6k.htm)
- **할인율 입력 근거:** 대한민국 정부 금리 자료 — [원문 바로 열기](https://english.mofe.go.kr/?boardCd=P0002&seq=2052)
- **시장위험 입력 근거:** 뉴욕대학교 다모다란 국가위험프리미엄 자료 — [원문 바로 열기](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctrypremtable.htm)
- **현재 시장가격:** 인베스팅닷컴 2026년 8월 28일 종가 자료 — [원문 바로 열기](https://kr.investing.com/equities/sk-hynix-inc-historical-data)
- **증권사 컨센서스:** 스톡애널리시스 집계 자료 — [원문 바로 열기](https://stockanalysis.com/quote/krx/000660/forecast/)
- **베타 비교군:** 브로드컴 · 인텔 · 마벨 · 마이크론 통계 자료 — [브로드컴](https://stockanalysis.com/stocks/avgo/statistics/) · [인텔](https://stockanalysis.com/stocks/intc/statistics/) · [마벨](https://stockanalysis.com/stocks/mrvl/statistics/) · [마이크론](https://stockanalysis.com/stocks/mu/statistics/)"""
    rendered = _replace_section(
        rendered,
        "## 정보 출처 — 원문 바로 확인",
        "## 분석 범위와 유의사항",
        sources,
    )

    details_marker = "<details>\n<summary>작성 근거와 계산 과정 보기</summary>"
    details_start = rendered.find(details_marker)
    if details_start >= 0:
        detail_record = rendered.find("## 세부 계산 기록", details_start)
        details_end = rendered.find("</details>", detail_record)
        if detail_record < 0 or details_end < 0:
            raise RuntimeError("표준 작성 근거 구역이 손상되었습니다")
        public_details = (
            rendered[details_start:detail_record]
            + """## 세부 계산 기록

- 33개 분석 단계는 표준 순서로 모두 종료되었습니다.
- 세부 계산 해시, 단계 기술 식별자, 원장 식별자는 동봉된 검증 파일에 보관합니다.
- 사용자용 보고서에는 투자 판단에 필요한 한국어 결과와 원문 출처만 표시합니다.

</details>"""
        )
        rendered = (
            rendered[:details_start]
            + public_details
            + rendered[details_end + len("</details>") :]
        )

    rendered = rendered.replace(
        "### 증권사별 목표가와 PRISM의 차이",
        "### 증권사별 목표가와 프리즘의 차이",
    )
    rendered = rendered.replace(
        "PRISM 결과와의 차이 자체가 계산 오류를 뜻하지는 않습니다.",
        "프리즘 결과와의 차이 자체가 계산 오류를 뜻하지는 않습니다.",
    )
    rendered = re.sub(r"\n---\n보고서 ID `[^`]+`\s*$", "", rendered)
    validate_skhynix_public_report(rendered)
    return rendered.rstrip() + "\n"


def render_skhynix_public_visual(svg: str, *, card_number: int) -> str:
    """Localize deterministic report cards without changing any numeric result."""
    rendered = svg.replace("PRISM 최종보고서", "프리즘 최종보고서")
    rendered = rendered.replace("SK hynix Inc.", "SK하이닉스")

    if card_number == 1:
        replacements = {
            "SK hynix has begun HBM4 mass shipments, reports": "4세대 고대역폭메모리 양산 출하를 시작했고",
            "full average utilization on its disclosed": "높은 설비가동률과 주요 고객 기반을 확보해",
            "production-cost basis, and describes long-term…": "장기계약을 통한 수요 가시성을 높였습니다",
            "Demand bottlenecks can reprice existing HBM": "인공지능 메모리 수요가 고객 인증·공급능력과",
            "qualification, customer access and capacity only": "결합해 제품 구성·가동률·영업이익률을 높이고",
            "when those strengths convert into durable FCFF…": "현금전환으로 이어질 때 가치가 재평가됩니다",
            "qualified HBM demand → utilization/product mix →": "고대역폭메모리 수요 → 제품 구성·가동률 →",
            "margin/cash conversion → FCFF after reinvestment": "영업이익률·현금전환 → 재투자 후 현금흐름",
            "확률가중 전 개별 시나리오": "확정된 개별 시나리오",
            "하방 (Down)": "하방",
            "기준 (Core)": "기준",
            "상방 (Bull)": "상방",
            "매수 검토 기준": "행동 기준",
        }
        for source, target in replacements.items():
            rendered = rendered.replace(source, target)
    elif card_number == 2:
        replacements = {
            "1년 DCF FCFF": "1년 현금흐름",
            "5년 DCF FCFF": "5년 현금흐름",
            "영구 ROIC": "영구 투하자본이익률",
            "UHV 5년 증분": "시나리오 확률",
            "하방(Down)": "하방",
            "기준(Core)": "기준",
            "상방(Bull)": "상방",
            "commodity_price_taker/midcycle_price_volume_dcf/1": "메모리 업황·가격·물량 반영 현금흐름할인법",
            "기존 증설 — + 초고압 부동산 —": "공시된 확정 투자만 반영, 미확정 계획은 제외",
            "별도 핵심 생산능력 프로젝트 없음": "가동률·확정 투자·증설을 중복 없이 반영",
            "확률 보정 및 별도 진입 규칙 미충족 시 자동 산출 금지": "별도 진입 규칙 확정 전 특정 매수구간 미제시",
        }
        for source, target in replacements.items():
            rendered = rendered.replace(source, target)
        dash_values = (
            ('<text x="1015" y="725" font-size="21" font-weight="500" fill="#142A3A" text-anchor="start">—</text>', '<text x="1015" y="725" font-size="21" font-weight="500" fill="#142A3A" text-anchor="start">15.7%</text>'),
            ('<text x="1015" y="810" font-size="21" font-weight="500" fill="#142A3A" text-anchor="start">—</text>', '<text x="1015" y="810" font-size="21" font-weight="500" fill="#142A3A" text-anchor="start">43.9%</text>'),
            ('<text x="1015" y="895" font-size="21" font-weight="500" fill="#142A3A" text-anchor="start">—</text>', '<text x="1015" y="895" font-size="21" font-weight="500" fill="#142A3A" text-anchor="start">40.4%</text>'),
        )
        for source, target in dash_values:
            rendered = rendered.replace(source, target)
    else:
        raise ValueError("SK하이닉스 공개 요약 이미지는 1번 또는 2번 카드만 허용합니다")

    rendered = rendered.replace(
        "https://github.com/newwonwoo/valuation/blob/main/config/skhynix_live_snapshot.yaml",
        "https://www.sec.gov/Archives/edgar/data/2120882/000119312526354777/d147827d6k.htm",
    )
    forbidden = tuple(token for token in _FORBIDDEN_PUBLIC_TOKENS if token in rendered)
    if forbidden:
        raise RuntimeError(
            "SK하이닉스 공개 이미지에 금지된 영문·내부 표현이 남아 있습니다: "
            + ", ".join(forbidden)
        )
    if "Down" in rendered or "Core" in rendered or "Bull" in rendered:
        raise RuntimeError("SK하이닉스 공개 이미지에 영문 시나리오명이 남아 있습니다")
    return rendered
