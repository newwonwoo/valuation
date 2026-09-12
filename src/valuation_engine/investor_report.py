"""Compact investor-facing report rendered from a completed valuation run.

The controlled run keeps its full audit bundle.  This module renders the
separate publication view: decision-useful valuation, business-unit coverage,
risks, decision-change conditions and public sources only.  Runtime hashes,
stage traces, evidence IDs and model-role explanations are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Mapping

import yaml

from .post_freeze import MarketComparisonBundle
from .records import MarketObservation
from .valuation_execution import GenericValuationResult, IntrinsicValuationScope


_FORBIDDEN_PUBLIC_TOKENS = (
    "<details",
    "</details>",
    "<summary",
    "실행 식별자",
    "실행 id",
    "run_id",
    "해시",
    "33단계",
    "단계별 로그",
    "분석 절차 기록",
    "근거 id",
    "evidence id",
    "operator declared",
    "인공지능 인사이트",
    "자동 오류 점검",
    "모듈 점검",
    "계산 확인",
)


@dataclass(frozen=True)
class InvestorReportProfile:
    investment_points: tuple[tuple[str, str, str], ...]
    valuation_method: str
    major_assumptions: str
    valuation_exclusions: str
    segment_notes: tuple[tuple[str, str, str], ...]
    risks: tuple[str, ...]
    upside_condition: str
    downside_condition: str
    actionable_condition: str
    sources: tuple[tuple[str, str, str], ...]
    prior_reference_per_share: Decimal | None = None
    prior_scope_label: str = ""

    def validate(self) -> None:
        if not 1 <= len(self.investment_points) <= 3:
            raise ValueError("investor report requires one to three investment points")
        if not 3 <= len(self.risks) <= 5:
            raise ValueError("investor report requires three to five risks")
        if not all((self.valuation_method, self.major_assumptions)):
            raise ValueError("investor report requires method and assumptions")
        if not all((self.upside_condition, self.downside_condition, self.actionable_condition)):
            raise ValueError("investor report requires all decision-change conditions")
        if not self.sources:
            raise ValueError("investor report requires public sources")
        allowed = {"공시", "IR", "신용평가", "시장가격"}
        for source_type, label, url in self.sources:
            if source_type not in allowed or not label or not url.startswith("https://"):
                raise ValueError("investor report source must be a public filing/IR/rating/market URL")


def _rows(
    value: object,
    *,
    label: str,
    keys: tuple[str, ...],
) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    result = []
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise ValueError(f"{label}[{index}] must be a mapping")
        unknown = set(row) - set(keys)
        if unknown:
            raise ValueError(
                f"{label}[{index}] carries unknown fields: {', '.join(sorted(unknown))}"
            )
        values = tuple(str(row.get(key) or "").strip() for key in keys)
        if not all(values):
            raise ValueError(f"{label}[{index}] is incomplete")
        result.append(values)
    return tuple(result)


def load_investor_report_profile(path: str | Path) -> InvestorReportProfile:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("investor report profile must be a mapping")
    conditions = payload.get("conditions") or {}
    if not isinstance(conditions, Mapping):
        raise ValueError("investor report conditions must be a mapping")
    prior = payload.get("prior_reference_per_share")
    profile = InvestorReportProfile(
        investment_points=_rows(
            payload.get("investment_points"),
            label="investment_points",
            keys=("title", "content", "evidence"),
        ),
        valuation_method=str(payload.get("valuation_method") or "").strip(),
        major_assumptions=str(payload.get("major_assumptions") or "").strip(),
        valuation_exclusions=str(payload.get("valuation_exclusions") or "").strip(),
        segment_notes=_rows(
            payload.get("segment_notes"),
            label="segment_notes",
            keys=("segment_id", "content", "note"),
        ),
        risks=tuple(str(item).strip() for item in payload.get("risks") or ()),
        upside_condition=str(conditions.get("upside") or "").strip(),
        downside_condition=str(conditions.get("downside") or "").strip(),
        actionable_condition=str(conditions.get("actionable") or "").strip(),
        sources=_rows(
            payload.get("sources"),
            label="sources",
            keys=("source_type", "label", "url"),
        ),
        prior_reference_per_share=(Decimal(str(prior)) if prior is not None else None),
        prior_scope_label=str(payload.get("prior_scope_label") or "").strip(),
    )
    profile.validate()
    return profile


def _money(value: Decimal | float) -> str:
    amount = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{amount:,.0f}원"


def _scenario_map(valuation: GenericValuationResult) -> dict[str, Decimal]:
    return {item.scenario_id: item.value_per_share for item in valuation.scenarios}


def _opinion(
    valuation: GenericValuationResult,
    market: MarketComparisonBundle | None,
) -> tuple[str, str]:
    if valuation.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC:
        return "판단 유보", "전체 기업가치가 아니라 평가 완료 사업부 기준입니다."
    expected = valuation.expected_value_per_share
    if expected is None:
        return "판단 유보", "보정된 시나리오 확률과 확률가중 기대값이 없습니다."
    if market is None or not valuation.scenarios:
        return "판단 유보", "확률가중 기대값과 비교할 검증된 현재가가 없습니다."
    current = Decimal(str(market.observation.price))
    if current < expected:
        return "매수 검토", "현재가가 확률가중 기대값보다 낮습니다."
    if current > expected:
        return "비중축소", "현재가가 확률가중 기대값보다 높습니다."
    return "중립", "현재가와 확률가중 기대값이 같습니다."


def render_investor_report(
    data: Mapping[str, object],
    profile: InvestorReportProfile,
) -> str:
    profile.validate()
    company = str(data.get("company") or "").strip()
    valuation = data.get("generic_valuation_result")
    if not company or not isinstance(valuation, GenericValuationResult):
        raise ValueError("completed company valuation is required")
    market_raw = data.get("market_comparison")
    market = market_raw if isinstance(market_raw, MarketComparisonBundle) else None
    observation_raw = data.get("market_observation")
    observation = (
        market.observation
        if market is not None
        else observation_raw if isinstance(observation_raw, MarketObservation) else None
    )
    scenarios = _scenario_map(valuation)
    missing = {"Down", "Base", "Bull"} - set(scenarios)
    if missing:
        raise ValueError("investor report requires Down/Base/Bull scenarios")
    opinion, opinion_reason = _opinion(valuation, market)
    partial = valuation.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC
    reference_label = "부분 내재가치" if partial else "평가 기준가"
    current_price = _money(observation.price) if observation is not None else "미확보"
    current_as_of = f" ({observation.as_of})" if observation is not None else ""
    if valuation.expected_value_per_share is not None:
        probability_note = (
            f"확률가중 기대값은 {_money(valuation.expected_value_per_share)}입니다."
        )
    elif partial:
        probability_note = (
            "확률가중 기대값은 산출되지 않았으며, 부분 평가이므로 현재가와의 "
            "상승여력은 비교하지 않았습니다."
        )
    else:
        probability_note = (
            "확률가중 기대값은 산출되지 않았습니다. 현재가는 확률 생성에 사용하지 "
            "않으며, 보정값이 없으면 방향 판단을 유보합니다."
        )

    lines = [
        f"# {company} 투자보고서",
        "",
        "## 1. 투자판단 요약",
        f"- 투자의견: {opinion}",
        f"- 현재가: {current_price}{current_as_of}",
        f"- {reference_label}: {_money(scenarios['Base'])}",
        f"- 핵심 결론: {opinion_reason} {probability_note}",
    ]
    if partial:
        lines.append(
            "- 판단 유보 사유: 전체 기업가치가 아니라 평가 완료 사업부 기준이며, "
            "미평가 사업부는 0원으로 처리하지 않았습니다."
        )
    lines.extend(("", "## 2. 핵심 투자포인트"))
    for title, content, evidence in profile.investment_points:
        lines.extend(
            (
                f"- 제목: {title}",
                f"  내용: {content}",
                f"  근거: {evidence}",
            )
        )
    lines.extend(
        (
            "",
            "## 3. 가치평가",
            "",
            "| 구분 | 하방 | 기준 | 상방 |",
            "|---|---:|---:|---:|",
            f"| 주당가치 | {_money(scenarios['Down'])} | {_money(scenarios['Base'])} | {_money(scenarios['Bull'])} |",
            "",
            f"- 평가방법: {profile.valuation_method}",
            f"- 주요 가정: {profile.major_assumptions}",
            f"- 평가 제외 항목: {profile.valuation_exclusions or '없음'}",
        )
    )
    if profile.prior_reference_per_share is not None:
        delta = scenarios["Base"] - profile.prior_reference_per_share
        lines.append(
            "- 변경 요약: 이전 "
            f"{profile.prior_scope_label or '기준가'} {_money(profile.prior_reference_per_share)} → "
            f"수정 {_money(scenarios['Base'])}, 주당 {_money(delta)} 증가."
        )

    unvalued = {item.segment_id: item for item in valuation.unvalued_segments}
    lines.extend(
        (
            "",
            "## 4. 사업부별 평가",
            "",
            "| 구분 | 평가 여부 | 핵심 내용 | 비고 |",
            "|---|---|---|---|",
        )
    )
    for segment_id, content, note in profile.segment_notes:
        if segment_id in unvalued:
            status = "미평가, 추가 확인 필요"
            note = unvalued[segment_id].rationale
        else:
            status = "평가"
        lines.append(f"| {segment_id} | {status} | {content} | {note} |")
    if unvalued:
        lines.append(
            "\n미평가 사업부는 분해 가능한 매출·이익·자산 또는 독립 현금흐름 자료가 "
            "확보되면 추가 평가할 수 있습니다."
        )

    lines.extend(("", "## 5. 리스크와 확인 필요 사항"))
    lines.extend(f"- {risk}" for risk in profile.risks)
    lines.extend(
        (
            "",
            "## 6. 판단 변경 조건",
            f"- 상방 조건: {profile.upside_condition}",
            f"- 하방 조건: {profile.downside_condition}",
            f"- 행동 가능 조건: {profile.actionable_condition}",
            "",
            "## 7. 참고자료",
        )
    )
    lines.extend(
        f"- {source_type}: [{label}]({url})"
        for source_type, label, url in profile.sources
    )
    report = "\n".join(lines).rstrip() + "\n"
    lowered = report.casefold()
    leaked = tuple(token for token in _FORBIDDEN_PUBLIC_TOKENS if token in lowered)
    if leaked:
        raise ValueError(
            "investor report contains developer-facing content: " + ", ".join(leaked)
        )
    return report


__all__ = [
    "InvestorReportProfile",
    "load_investor_report_profile",
    "render_investor_report",
]
