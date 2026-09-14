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
from .ledger import EvidenceLedger
from .source_reporting import canonical_verification_url
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
    entry_margin_of_safety: Decimal | None = None
    entry_rule_rationale: str = ""

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
        allowed = {"공시", "IR", "신용평가", "시장가격", "산업자료", "비교기업 공시", "증권사 조사단서"}
        for source_type, label, url in self.sources:
            if source_type not in allowed or not label or not url.startswith("https://"):
                raise ValueError("investor report source must have a supported public source type and HTTPS URL")
        if self.entry_margin_of_safety is not None:
            if not Decimal("0") < self.entry_margin_of_safety < Decimal("1"):
                raise ValueError("entry margin of safety must be between zero and one")
            if len(self.entry_rule_rationale) < 20:
                raise ValueError("entry rule requires a decision-useful rationale")
        elif self.entry_rule_rationale:
            raise ValueError("entry rule rationale requires a margin of safety")


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
    entry_rule = payload.get("entry_rule") or {}
    if not isinstance(entry_rule, Mapping):
        raise ValueError("investor report entry_rule must be a mapping")
    entry_margin = entry_rule.get("margin_of_safety")
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
        entry_margin_of_safety=(
            Decimal(str(entry_margin)) if entry_margin is not None else None
        ),
        entry_rule_rationale=str(entry_rule.get("rationale") or "").strip(),
    )
    profile.validate()
    return profile


def _money(value: Decimal | float) -> str:
    amount = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{amount:,.0f}원"


def _scenario_map(valuation: GenericValuationResult) -> dict[str, Decimal]:
    return {item.scenario_id: item.value_per_share for item in valuation.scenarios}


def probability_weighted_equity_value(
    valuation: GenericValuationResult,
    bound_scenarios: object,
) -> Decimal | None:
    """Return the public-equity target while retaining signed audit residuals.

    Enterprise value less senior claims may be negative in a downside case,
    and that signed residual remains in the audit bundle. A listed share has
    limited liability, however, so the investor-facing expected stock value
    weights ``max(0, residual)`` scenario by scenario.
    """
    probability_map = {
        str(getattr(item, "scenario_id", "")): getattr(item, "probability", None)
        for item in tuple(getattr(bound_scenarios, "scenarios", ()))
    }
    scenario_values = _scenario_map(valuation)
    center_id = (
        "Base"
        if "Base" in scenario_values and "Base" in probability_map
        else "Core"
    )
    required = ("Down", center_id, "Bull")
    if valuation.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC:
        return None
    if valuation.expected_value_per_share is None:
        return None
    if not bool(getattr(bound_scenarios, "numeric_weighting_allowed", False)):
        return None
    if not all(
        isinstance(probability_map.get(scenario_id), Decimal)
        and scenario_id in scenario_values
        for scenario_id in required
    ):
        return None
    target = sum(
        (
            probability_map[scenario_id] * max(Decimal("0"), scenario_values[scenario_id])
            for scenario_id in required
        ),
        Decimal("0"),
    )
    return target


def entry_price_with_margin(
    target: Decimal, margin_of_safety: Decimal
) -> Decimal:
    """Apply the declared safety margin and round to a decision-useful KRW tick."""
    # A 100-won tick avoids implying single-won precision in a long-range DCF.
    raw = target * (Decimal("1") - margin_of_safety)
    return (raw / Decimal("100")).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    ) * Decimal("100")


def _opinion(
    valuation: GenericValuationResult,
    market: MarketComparisonBundle | None,
    *,
    decision_value: Decimal | None = None,
    entry_price: Decimal | None = None,
) -> tuple[str, str]:
    if valuation.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC:
        return "부분 사업가치 평가", "전체 기업가치가 아니라 평가 완료 사업부 기준입니다."
    expected = (
        decision_value
        if decision_value is not None
        else valuation.expected_value_per_share
    )
    if expected is None:
        return "시나리오 기준 평가", "하방·기준·상방의 조건별 가치를 제시합니다. 보정된 시나리오 확률과 확률가중 기대값이 없습니다."
    if market is None or not valuation.scenarios:
        return "내재가치 기준 평가", "내재가치는 산출했으며, 검증된 현재가가 없어 매매가격 비교는 제외합니다."
    current = Decimal(str(market.observation.price))
    if entry_price is not None:
        if current <= entry_price:
            return "매수 검토", "현재가가 선언된 안전마진을 반영한 구체 매수가 이하입니다."
        if current <= expected:
            return "관찰", "현재가는 확률가중 목표가보다 낮지만 선언된 안전마진 매수가에는 이르지 않았습니다."
        return "비중축소", "현재가가 주주 유한책임을 반영한 확률가중 목표가보다 높습니다."
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
    signed_scenarios = _scenario_map(valuation)
    partial = valuation.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC
    scenarios = {key: value if partial else max(Decimal("0"), value)
                 for key, value in signed_scenarios.items()}
    floor_applied = scenarios != signed_scenarios
    missing = {"Down", "Base", "Bull"} - set(scenarios)
    if missing:
        raise ValueError("investor report requires Down/Base/Bull scenarios")
    bound_scenarios = data.get("bound_scenario_set")
    probability_target = probability_weighted_equity_value(
        valuation, bound_scenarios
    )
    probability_map = {
        str(getattr(item, "scenario_id", "")): getattr(item, "probability", None)
        for item in tuple(getattr(bound_scenarios, "scenarios", ()))
    }
    has_complete_probabilities = all(
        isinstance(probability_map.get(scenario_id), Decimal)
        for scenario_id in ("Down", "Base", "Bull")
    )
    declared_entry_price = (
        entry_price_with_margin(probability_target, profile.entry_margin_of_safety)
        if probability_target is not None
        and profile.entry_margin_of_safety is not None
        else None
    )
    opinion, opinion_reason = _opinion(
        valuation,
        market,
        decision_value=probability_target,
        entry_price=declared_entry_price,
    )
    partial = valuation.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC
    reference_label = "부분 내재가치" if partial else "평가 기준가"
    current_price = _money(observation.price) if observation is not None else "미확보"
    current_as_of = f" ({observation.as_of})" if observation is not None else ""
    if partial:
        probability_note = (
            "확률가중 기대값은 산출하지 않았으며, 부분 평가이므로 현재가와의 "
            "상승여력은 비교하지 않았습니다."
        )
    elif probability_target is not None:
        probability_note = (
            (
                "주주 유한책임을 반영한 확률가중 목표가는 "
                if floor_applied
                else "확률가중 기대값은 "
            )
            + f"{_money(probability_target)}입니다."
        )
        if has_complete_probabilities:
            probability_note += (
                " 적용 확률은 하방 "
                f"{probability_map['Down']:.1%}, 기준 {probability_map['Base']:.1%}, "
                f"상방 {probability_map['Bull']:.1%}입니다."
            )
        if floor_applied:
            probability_note += (
                " 하방의 음수 잔여가치는 감사 계산에 보존하고, 투자자용 주식가치에는 0원 하한을 적용했습니다."
            )
    elif floor_applied:
        probability_note = (
            "주식가치는 시나리오별로 0원을 하한으로 표시합니다. "
            "음수 잔여가치는 아래에 별도 공개하며, 유한책임 반영 전 기대값을 매매 판단에 사용하지 않습니다."
        )
    elif valuation.expected_value_per_share is not None:
        probability_note = f"확률가중 기대값은 {_money(valuation.expected_value_per_share)}입니다."
        if has_complete_probabilities:
            probability_note += (
                " 적용 확률은 하방 "
                f"{probability_map['Down']:.1%}, 기준 {probability_map['Base']:.1%}, "
                f"상방 {probability_map['Bull']:.1%}입니다."
            )
    else:
        probability_note = (
            "확률가중 기대값은 산출되지 않았습니다. 현재가는 확률 생성에 사용하지 "
            "않으며, 각 시나리오의 성립 조건과 가치 범위로 판단합니다."
        )

    if not partial and observation is not None:
        current = Decimal(str(observation.price))
        if scenarios["Bull"] > scenarios["Base"] and current > scenarios["Base"]:
            if current >= scenarios["Bull"]:
                opinion_reason += " 현재가는 상방 시나리오 가치 이상으로, 상방 가정을 충족하거나 넘어서는 실적이 필요합니다."
            elif current - scenarios["Base"] >= scenarios["Bull"] - current:
                opinion_reason += " 현재가는 기준보다 상방 시나리오에 가까워, 상방에 가까운 이익·현금흐름 회복을 요구합니다."
            else:
                opinion_reason += " 현재가는 기준 시나리오를 넘어서는 이익·현금흐름 회복을 요구합니다."

    lines = [
        f"# {company} 투자보고서",
        "",
        "## 1. 투자판단 요약",
        f"- 투자의견: {opinion}",
        f"- 현재가: {current_price}{current_as_of}",
        f"- {reference_label}: {_money(scenarios['Base'])}",
    ]
    if probability_target is not None:
        lines.append(f"- 확률가중 목표가: {_money(probability_target)}")
    if declared_entry_price is not None:
        lines.append(
            f"- 구체 매수가: {_money(declared_entry_price)} 이하 "
            f"(목표가 대비 {profile.entry_margin_of_safety:.0%} 안전마진)"
        )
    lines.append(f"- 핵심 결론: {opinion_reason} {probability_note}")
    if partial:
        lines.append(
            "- 평가 범위: 전체 기업가치가 아니라 평가 완료 사업부 기준이며, "
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
    if probability_target is not None:
        lines.append(
            f"- 확률가중 목표가: {_money(probability_target)} = "
            "Σ[보정확률 × max(0원, 시나리오별 부채 차감 후 잔여가치)]."
        )
    if declared_entry_price is not None:
        lines.append(
            f"- 매수 규칙: {_money(declared_entry_price)} 이하. "
            f"확률가중 목표가에 {profile.entry_margin_of_safety:.0%} 안전마진을 적용했습니다. "
            f"{profile.entry_rule_rationale}"
        )
    if floor_applied:
        lines.extend((
            "",
            "| 구분 | 하방 | 기준 | 상방 |",
            "|---|---:|---:|---:|",
            "| 부채 차감 후 주당 잔여가치 | "
            + " | ".join(_money(signed_scenarios[key]) for key in ("Down", "Base", "Bull")) + " |",
            "- 음수 잔여가치는 추정 사업가치가 부채 등 선순위 청구액에 미달한다는 뜻입니다. "
            "주주의 추가 납입 의무나 음수 주식가격을 뜻하지 않으며, 유한책임 주식가치의 하한은 0원입니다.",
        ))
        if valuation.expected_value_per_share is not None:
            lines.append(
                f"- 유한책임 반영 전 확률가중 잔여가치: {_money(valuation.expected_value_per_share)}. "
                "시나리오별 0원 하한 적용 후의 확률가중 주식가치와 다릅니다."
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
    # A revised operating assumption may introduce sources absent from the
    # prior editorial profile. Bind those links to the actual accepted ledger.
    linked = {url for _, _, url in profile.sources}
    ledger = data.get("evidence_ledger")
    if isinstance(ledger, EvidenceLedger):
        for record in ledger.active():
            if not (getattr(record, "research_receipt", None) or getattr(record, "business_cashflow_receipt", None)):
                continue
            for source in getattr(record, "source_refs", ()):
                url = canonical_verification_url(source)
                if url is None:
                    raise ValueError("research report requires public source links")
                if url not in linked:
                    lines.append(f"- 추정 근거: [비교자료·계산 원문]({url})")
                    linked.add(url)
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
    "probability_weighted_equity_value",
    "render_investor_report",
]
