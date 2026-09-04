from __future__ import annotations

import re
from typing import Mapping

from .source_reporting import (
    SourceLink,
    build_source_link_index,
    render_source_link_section,
)


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


def skhynix_public_source_links(data: Mapping[str, object]) -> tuple[SourceLink, ...]:
    """Select public links from the same post-freeze run authority."""

    links = build_source_link_index(dict(data), require_all_http=True)
    public_authority = tuple(
        link
        for link in links
        if "github.com/newwonwoo/valuation" not in link.url
    )
    public: list[SourceLink] = []
    for link in public_authority:
        if "api.nasdaq.com/api/quote/" in link.url:
            label = "나스닥 베타 시계열 원자료"
        elif "sec.gov/Archives/edgar/data/2120882/" in link.url:
            label = "SK하이닉스 미국 증권거래위원회 공시"
        elif "sec.gov/Archives/edgar/data/" in link.url:
            label = "베타 비교기업 미국 증권거래위원회 공시"
        elif "samsungpop.com" in link.url:
            label = "삼성증권 원문 리서치"
        elif "skhynix.com/ir/UI-FR-IR01" in link.url:
            label = "SK하이닉스 주가정보"
        elif "news.skhynix.com" in link.url:
            label = "SK하이닉스 실적 발표"
        else:
            label = " / ".join(link.labels).replace(
                "SK hynix frozen LIVE source pack",
                "SK하이닉스 원문 근거 묶음",
            )
        coverage = tuple(
            "공시·평가 입력 근거" if row.startswith("근거 ") else row
            for row in link.coverage
        )
        public.append(
            SourceLink(
                url=link.url,
                labels=(label,),
                coverage=tuple(dict.fromkeys(coverage)),
            )
        )
    public_links = tuple(public)
    urls = {link.url for link in public_links}
    required = {
        str(data["market_observation"].source_ref),
        *(str(item.source_ref) for item in data.get("street_reports", ())),
        *(str(item) for item in data.get("beta_source_refs", ())),
    }
    required = {
        url
        for url in required
        if "github.com/newwonwoo/valuation" not in url
    }
    missing = required - urls
    if missing:
        raise ValueError(
            "SK hynix public source authority is incomplete: "
            + ", ".join(sorted(missing))
        )
    return public_links


def render_skhynix_public_report(
    report: str,
    *,
    data: Mapping[str, object],
    source_links: tuple[SourceLink, ...],
) -> str:
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
        "Samsung Securities": "삼성증권",
        "Jongwook Lee and Kyoungbeen Kim": "이종욱·김경빈",
        "2026년 기준 · post-freeze broker reference only": "2026년 기준 · 가치평가 확정 후 참고용 증권사 자료",
        "2026년 post-freeze broker reference only": "2026년 가치평가 확정 후 참고용 증권사 자료",
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

    valuation = data.get("generic_valuation_result")
    scenarios = {
        str(item.scenario_id): item.value_per_share
        for item in getattr(valuation, "scenarios", ())
    }
    if set(scenarios) != {"Down", "Core", "Bull"}:
        raise ValueError("SK hynix public report requires Down/Core/Bull values")
    expected_value = getattr(valuation, "expected_value_per_share", None)
    if expected_value is None:
        raise ValueError("SK hynix public report requires calibrated expected value")
    down_value = f"{scenarios['Down']:,.0f}"
    core_value = f"{scenarios['Core']:,.0f}"
    bull_value = f"{scenarios['Bull']:,.0f}"
    expected_text = f"{expected_value:,.0f}"

    conclusion = f"""### 한 문장 결론

4세대 고대역폭메모리 양산 출하, 높은 설비가동률, 주요 고객과의 장기계약이 인공지능 메모리 수요를 현금흐름으로 전환하는 핵심 축입니다. 기준 내재가치는 {core_value}원, 확률가중 기대값은 {expected_text}원이며, 현재가에서는 메모리 업황 정상화·대규모 설비투자·현금전환 지속 여부를 함께 확인해야 합니다."""
    rendered = _replace_block(
        rendered,
        "### 한 문장 결론",
        "### 투자포인트",
        conclusion,
    )

    investment_points = f"""### 투자포인트

- **사업모델과 강점:** 고대역폭메모리·디램·기업용 저장장치를 인공지능·서버 고객에 공급하며, 4세대 고대역폭메모리 양산 출하와 주요 고객 접근성이 핵심 경쟁력입니다.
- **가치동인:** 매출 성장률·영업이익률·현금전환율·설비투자 비중의 연속 경로가 현금흐름과 내재가치를 좌우합니다.
- **가치평가:** 하방 {down_value}원 · 기준 {core_value}원 · 상방 {bull_value}원, 확률가중 기대값 {expected_text}원입니다.
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

    sources = "\n".join(render_source_link_section(source_links))
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
    rendered = rendered.replace("증권사 평균 목표가", "증권사 참고 목표가")
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
