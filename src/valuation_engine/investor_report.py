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
from typing import Any, Mapping

import yaml

from .post_freeze import MarketComparisonBundle
from .records import MarketObservation
from .ledger import EvidenceLedger
from .source_reporting import canonical_verification_url
from .street import StreetResearchReport
from .distributional_runtime import DistributionalPrimaryValuationResult
from .valuation_sensitivity import (
    DISCOUNT_RATE,
    FCFF_LEVEL,
    TERMINAL_GROWTH,
    ValuationSensitivityReport,
)
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
    schema_version: str
    conclusion: str
    investment_points: tuple[tuple[str, str, str, str, str], ...]
    valuation_method: str
    method_rationale: str
    major_assumptions: str
    valuation_exclusions: str
    scenario_conditions: tuple[tuple[str, str, str], ...]
    sensitivity_watch: tuple[tuple[str, str, str, str], ...]
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
        if self.schema_version != "investor_report/v2":
            raise ValueError("investor report requires schema_version=investor_report/v2")
        if len(self.conclusion) < 20:
            raise ValueError("investor report requires a decision-useful conclusion")
        if not 1 <= len(self.investment_points) <= 3:
            raise ValueError("investor report requires one to three investment points")
        if not 3 <= len(self.risks) <= 5:
            raise ValueError("investor report requires three to five risks")
        if not self.segment_notes:
            raise ValueError("investor report requires at least one business-unit note")
        if not all((self.valuation_method, self.method_rationale, self.major_assumptions)):
            raise ValueError("investor report requires method, rationale and assumptions")
        scenario_ids = tuple(item[0] for item in self.scenario_conditions)
        if set(scenario_ids) != {"Down", "Base", "Bull"} or len(scenario_ids) != 3:
            raise ValueError("investor report requires unique Down/Base/Bull scenario conditions")
        if not 1 <= len(self.sensitivity_watch) <= 5:
            raise ValueError("investor report requires one to five business sensitivity rows")
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
    allowed_fields = {
        "schema_version",
        "conclusion",
        "investment_points",
        "valuation_method",
        "method_rationale",
        "major_assumptions",
        "valuation_exclusions",
        "scenario_conditions",
        "sensitivity_watch",
        "segment_notes",
        "risks",
        "conditions",
        "sources",
        "prior_reference_per_share",
        "prior_scope_label",
        "entry_rule",
    }
    unknown_fields = set(payload) - allowed_fields
    if unknown_fields:
        raise ValueError(
            "investor report profile carries unknown fields: "
            + ", ".join(sorted(unknown_fields))
        )
    conditions = payload.get("conditions") or {}
    if not isinstance(conditions, Mapping):
        raise ValueError("investor report conditions must be a mapping")
    prior = payload.get("prior_reference_per_share")
    entry_rule = payload.get("entry_rule") or {}
    if not isinstance(entry_rule, Mapping):
        raise ValueError("investor report entry_rule must be a mapping")
    entry_margin = entry_rule.get("margin_of_safety")
    profile = InvestorReportProfile(
        schema_version=str(payload.get("schema_version") or "").strip(),
        conclusion=str(payload.get("conclusion") or "").strip(),
        investment_points=_rows(
            payload.get("investment_points"),
            label="investment_points",
            keys=("title", "content", "evidence", "valuation_link", "falsifier"),
        ),
        valuation_method=str(payload.get("valuation_method") or "").strip(),
        method_rationale=str(payload.get("method_rationale") or "").strip(),
        major_assumptions=str(payload.get("major_assumptions") or "").strip(),
        valuation_exclusions=str(payload.get("valuation_exclusions") or "").strip(),
        scenario_conditions=_rows(
            payload.get("scenario_conditions"),
            label="scenario_conditions",
            keys=("scenario_id", "condition", "valuation_effect"),
        ),
        sensitivity_watch=_rows(
            payload.get("sensitivity_watch"),
            label="sensitivity_watch",
            keys=("driver", "downside", "upside", "monitor"),
        ),
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


def _scenario_profile_map(
    profile: InvestorReportProfile,
) -> dict[str, tuple[str, str]]:
    return {
        scenario_id: (condition, valuation_effect)
        for scenario_id, condition, valuation_effect in profile.scenario_conditions
    }


def _sensitivity_delta_ko(variable: str, low: Decimal, high: Decimal) -> str:
    delta = (high - low) / Decimal("2")
    if variable in {DISCOUNT_RATE, TERMINAL_GROWTH}:
        return f"±{delta * 100:.1f}%p"
    if variable == FCFF_LEVEL:
        return f"±{delta * 100:.0f}%"
    return f"±{delta}"


def _generic_sensitivity_lines(data: Mapping[str, object]) -> list[str]:
    sensitivity = data.get("valuation_sensitivity_report")
    if not isinstance(sensitivity, ValuationSensitivityReport):
        return ["- 계산 민감도: 현재 평가법에서는 독립 재현 가능한 수치 민감도가 없습니다."]
    rows: list[tuple[str, str, object]] = []
    for scenario in sensitivity.scenarios:
        if not scenario.measured:
            continue
        rows.extend((scenario.scenario_id, "", item) for item in scenario.variables)
        rows.extend(
            (scenario.scenario_id, segment.asset_id, item)
            for segment in scenario.segments
            for item in segment.variables
        )
    if not rows:
        return ["- 계산 민감도: " + sensitivity.summary_ko + "."]
    lines = [
        "| 시나리오 | 대상 | 변수 변화 | 주당가치 영향 |",
        "|---|---|---:|---:|",
    ]
    for scenario_id, asset_id, item in rows:
        location = asset_id or "전체"
        lines.append(
            f"| {scenario_id} | {location} | {item.label} "
            f"{_sensitivity_delta_ko(item.variable, item.low_input, item.high_input)} | "
            f"{item.low_value_pct * 100:+.1f}% / {item.high_value_pct * 100:+.1f}% |"
        )
    return lines


def _street_comparison_lines(data: Mapping[str, object]) -> list[str]:
    reports = data.get("street_reports")
    if not isinstance(reports, tuple) or not reports or not all(
        isinstance(item, StreetResearchReport) for item in reports
    ):
        return ["- 비교 가능한 증권사 자료를 확보하지 못했습니다."]
    lines = []
    for report in reports:
        estimates = ", ".join(
            _street_estimate_ko(item)
            for item in report.estimates
        ) or "구조화된 실적 추정치 미확보"
        lines.append(
            f"- **{report.broker}** — 목표가 {_money(report.target_price)}, "
            f"{estimates}. {report.argument_summary or '공개 자료에서 투자논리 요약을 확보하지 못했습니다.'}"
        )
        if report.valuation_basis_note:
            lines.append(f"  - 평가식 확인: {report.valuation_basis_note}")
    return lines


def _street_estimate_ko(item: object) -> str:
    metric = str(getattr(item, "metric", ""))
    period = str(getattr(item, "period", ""))
    value = Decimal(str(getattr(item, "value", "0")))
    unit = str(getattr(item, "unit", ""))
    label = {
        "consolidated_revenue": "연결 매출",
        "consolidated_operating_profit": "연결 영업이익",
        "consolidated_operating_margin": "연결 영업이익률",
        "EPS": "주당순이익",
    }.get(metric, "공개 추정치")
    if unit == "KRW billion":
        amount = (
            f"{value / Decimal('1000'):.2f}조원"
            if abs(value) >= Decimal("1000")
            else f"{value * Decimal('10'):,.0f}억원"
        )
    elif unit == "percent":
        amount = f"{value:.1f}%"
    elif unit == "KRW/share":
        amount = f"{value:,.0f}원"
    else:
        amount = f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{period} {label} {amount}"


def _validate_public_report(report: str) -> str:
    lowered = report.casefold()
    leaked = tuple(token for token in _FORBIDDEN_PUBLIC_TOKENS if token in lowered)
    if leaked:
        raise ValueError(
            "investor report contains developer-facing content: " + ", ".join(leaked)
        )
    return report


def _supplemental_source_lines(
    data: Mapping[str, object],
    *,
    linked: set[str],
) -> list[str]:
    """Add newly accepted research URLs without exposing evidence identities."""
    ledger = data.get("evidence_ledger")
    if not isinstance(ledger, EvidenceLedger):
        return []
    lines = []
    for record in ledger.active():
        if not (
            getattr(record, "research_receipt", None)
            or getattr(record, "business_cashflow_receipt", None)
        ):
            continue
        for source in getattr(record, "source_refs", ()):
            url = canonical_verification_url(source)
            if url is None:
                raise ValueError("research report requires public source links")
            if url in linked:
                continue
            lines.append(f"- 추가 추정 근거: [원문 바로 보기]({url})")
            linked.add(url)
    return lines


def _distributional_range(
    valuation: DistributionalPrimaryValuationResult,
) -> tuple[str, Decimal, Decimal, Decimal]:
    if valuation.pathwise_distribution is not None:
        distribution = valuation.pathwise_distribution
        return (
            "경로 분포 P20~P80",
            distribution.quantile(Decimal("0.20")),
            distribution.quantile(Decimal("0.80")),
            distribution.quantile(Decimal("0.50")),
        )
    if valuation.ambiguity_intrinsic_range is not None:
        low = valuation.ambiguity_intrinsic_range.minimum_expected_value
        high = valuation.ambiguity_intrinsic_range.maximum_expected_value
        return "허용 가정 조합 범위", low, high, (low + high) / Decimal("2")
    raise ValueError("distributional valuation has no investor-facing value range")


def _distributional_sensitivity_lines(
    valuation: DistributionalPrimaryValuationResult,
) -> list[str]:
    sensitivities: tuple[object, ...] = ()
    if valuation.pathwise_entry is not None:
        sensitivities = valuation.pathwise_entry.sensitivities
    elif valuation.robust_entry is not None:
        sensitivities = valuation.robust_entry.sensitivities
    rows = []
    for item in sensitivities:
        rate = getattr(item, "required_annual_return", None)
        price = getattr(item, "entry_price", None)
        if price is None:
            price = getattr(item, "robust_entry_price", None)
        if isinstance(rate, Decimal) and isinstance(price, Decimal):
            rows.append((rate, price))
    if not rows:
        return ["- 계산 민감도: 승인된 진입가격 민감도가 없습니다."]
    lines = [
        "| 요구수익률 | 진입가격 상한 |",
        "|---:|---:|",
    ]
    lines.extend(f"| {rate:.1%} | {_money(price)} |" for rate, price in rows)
    return lines


def render_distributional_investor_report(
    data: Mapping[str, object],
    profile: InvestorReportProfile,
) -> str:
    """Render the same public-report contract from a distributional valuation."""
    profile.validate()
    company = str(data.get("company") or data.get("target_id") or "").strip()
    valuation = data.get("distributional_primary_result")
    if not company or not isinstance(valuation, DistributionalPrimaryValuationResult):
        raise ValueError("completed distributional company valuation is required")

    range_label, low, high, reference = _distributional_range(valuation)
    market = data.get("market_comparison")
    observation = data.get("market_observation")
    market_price = getattr(market, "price", None)
    market_as_of = getattr(market, "as_of", None)
    if market_price is None and isinstance(observation, MarketObservation):
        market_price = Decimal(str(observation.price))
        market_as_of = observation.as_of
    current_price = (
        f"{_money(Decimal(str(market_price)))} ({market_as_of})"
        if market_price is not None and market_as_of
        else "미확보"
    )
    entry = (
        valuation.entry_price
        if valuation.route_authorization.entry_price_authorized
        else None
    )
    if entry is None or market_price is None:
        opinion = "가치범위 확인"
        opinion_reason = "검증된 진입가격 또는 현재가가 없어 범위와 사업 조건을 우선 확인합니다."
    elif Decimal(str(market_price)) <= entry:
        opinion = "매수 검토"
        opinion_reason = "현재가가 검증된 진입가격 상한 이내입니다."
    else:
        opinion = "신규매수 보류"
        opinion_reason = "현재가가 검증된 진입가격 상한을 웃돕니다."

    probability_note = "보정된 성공확률은 산출하지 않았습니다."
    if valuation.pathwise_distribution is not None:
        distribution = valuation.pathwise_distribution
        probability_note = (
            f"모형 경로 중 곤경 경로는 {distribution.distress_probability:.1%}, "
            f"추가 희석 경로는 {distribution.dilution_probability:.1%}입니다."
        )

    scenario_profile = _scenario_profile_map(profile)
    values = {"Down": low, "Base": reference, "Bull": high}
    lines = [
        f"# {company} 투자보고서",
        "",
        "## 1. 투자판단 요약",
        f"- 투자의견: {opinion}",
        f"- 현재가: {current_price}",
        f"- 기준 내재가치: {_money(reference)}",
        f"- 가치평가 범위: {_money(low)}~{_money(high)} ({range_label})",
        *(
            (
                "- 단일 확률가중 목표가: 미산출 — 보정된 단일 확률 대신 허용 가정 조합의 범위를 사용합니다.",
                "- 보정 성공확률: 미산출 — 사전확률을 실제 성공빈도로 해석하지 않습니다.",
            )
            if valuation.ambiguity_intrinsic_range is not None
            else ()
        ),
        f"- 보수적 진입 상한: {_money(entry) + ' 이하' if entry is not None else '미산출'}",
        f"- 핵심 결론: {profile.conclusion}",
        f"- 판단 근거: {opinion_reason} {probability_note}",
        "",
        "## 2. 투자논리",
    ]
    for title, content, evidence, valuation_link, falsifier in profile.investment_points:
        lines.extend(
            (
                "",
                f"### {title}",
                f"- 관찰과 가정: {content}",
                f"- 근거: {evidence}",
                f"- 가치 연결: {valuation_link}",
                f"- 반증 조건: {falsifier}",
            )
        )
    lines.extend(
        (
            "",
            "## 3. 가치평가와 민감도",
            "",
            "| 시나리오 | 주당가치 | 성립 조건 | 가치 연결 |",
            "|---|---:|---|---|",
            *(
                f"| {scenario_id} | {_money(values[scenario_id])} | "
                f"{scenario_profile[scenario_id][0]} | {scenario_profile[scenario_id][1]} |"
                for scenario_id in ("Down", "Base", "Bull")
            ),
            "",
            f"- 평가방법: {profile.valuation_method}",
            f"- 방법 선택 이유: {profile.method_rationale}",
            f"- 주요 가정: {profile.major_assumptions}",
            f"- 평가 제외 항목: {profile.valuation_exclusions}",
            "",
            "### 사업 민감도와 다음 확인지표",
        )
    )
    for driver, downside, upside, monitor in profile.sensitivity_watch:
        lines.append(
            f"- **{driver}** — 하방: {downside} / 상방: {upside} / 확인: {monitor}"
        )
    lines.extend(("", "### 계산된 진입가격 민감도", ""))
    lines.extend(_distributional_sensitivity_lines(valuation))

    lines.extend(("", "## 4. 사업부별 평가", "", "| 사업부 | 핵심 내용 | 비고 |", "|---|---|---|"))
    lines.extend(
        f"| {segment_id} | {content} | {note} |"
        for segment_id, content, note in profile.segment_notes
    )
    lines.extend(("", "## 5. 위험과 판단 변경 조건", "", "### 주요 위험"))
    lines.extend(f"- {risk}" for risk in profile.risks)
    lines.extend(
        (
            "",
            "### 판단 변경 조건",
            f"- 상방 조건: {profile.upside_condition}",
            f"- 하방 조건: {profile.downside_condition}",
            f"- 행동 가능 조건: {profile.actionable_condition}",
            "",
            "## 6. 증권사·시장 비교",
        )
    )
    street = data.get("street_comparison")
    if street is not None and hasattr(street, "mean_target_price"):
        lines.append(
            f"- 증권사 목표가 평균 {_money(getattr(street, 'mean_target_price'))}, "
            f"범위 {_money(getattr(street, 'min_target_price'))}~{_money(getattr(street, 'max_target_price'))} "
            f"({getattr(street, 'report_count')}건)."
        )
    lines.extend(_street_comparison_lines(data))
    lines.extend(("", "## 7. 원문 자료"))
    lines.extend(
        f"- {source_type}: [{label}]({url})"
        for source_type, label, url in profile.sources
    )
    lines.extend(
        _supplemental_source_lines(
            data,
            linked={url for _, _, url in profile.sources},
        )
    )
    return _validate_public_report("\n".join(lines).rstrip() + "\n")


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

    scenario_profile = _scenario_profile_map(profile)
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
    lines.append(f"- 핵심 결론: {profile.conclusion}")
    lines.append(f"- 판단 근거: {opinion_reason} {probability_note}")
    if partial:
        lines.append(
            "- 평가 범위: 전체 기업가치가 아니라 평가 완료 사업부 기준이며, "
            "미평가 사업부는 0원으로 처리하지 않았습니다."
        )
    lines.extend(("", "## 2. 투자논리"))
    for title, content, evidence, valuation_link, falsifier in profile.investment_points:
        lines.extend(
            (
                f"### {title}",
                "",
                f"- 관찰과 가정: {content}",
                f"- 근거: {evidence}",
                f"- 가치 연결: {valuation_link}",
                f"- 반증 조건: {falsifier}",
                "",
            )
        )
    lines.extend(
        (
            "## 3. 가치평가와 민감도",
            "",
            "| 시나리오 | 주당가치 | 성립 조건 | 가치 연결 |",
            "|---|---:|---|---|",
            *(
                f"| {scenario_id} | {_money(scenarios[scenario_id])} | "
                f"{scenario_profile[scenario_id][0]} | {scenario_profile[scenario_id][1]} |"
                for scenario_id in ("Down", "Base", "Bull")
            ),
            "",
            f"- 평가방법: {profile.valuation_method}",
            f"- 방법 선택 이유: {profile.method_rationale}",
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

    lines.extend(("", "### 사업 민감도와 다음 확인지표", ""))
    for driver, downside, upside, monitor in profile.sensitivity_watch:
        lines.append(
            f"- **{driver}** — 하방: {downside} / 상방: {upside} / 확인: {monitor}"
        )
    lines.extend(("", "### 계산된 가치 민감도", ""))
    lines.extend(_generic_sensitivity_lines(data))

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

    lines.extend(("", "## 5. 위험과 판단 변경 조건", "", "### 주요 위험"))
    lines.extend(f"- {risk}" for risk in profile.risks)
    lines.extend(
        (
            "",
            "### 판단 변경 조건",
            f"- 상방 조건: {profile.upside_condition}",
            f"- 하방 조건: {profile.downside_condition}",
            f"- 행동 가능 조건: {profile.actionable_condition}",
            "",
            "## 6. 증권사·시장 비교",
        )
    )
    lines.extend(_street_comparison_lines(data))
    lines.extend(("", "## 7. 원문 자료"))
    lines.extend(
        f"- {source_type}: [{label}]({url})"
        for source_type, label, url in profile.sources
    )
    lines.extend(
        _supplemental_source_lines(
            data,
            linked={url for _, _, url in profile.sources},
        )
    )
    report = "\n".join(lines).rstrip() + "\n"
    return _validate_public_report(report)


__all__ = [
    "InvestorReportProfile",
    "load_investor_report_profile",
    "probability_weighted_equity_value",
    "render_distributional_investor_report",
    "render_investor_report",
]
