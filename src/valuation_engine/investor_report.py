"""Compact investor-facing report rendered from a completed valuation run.

The controlled run keeps its full audit bundle.  This module renders the
separate publication view: decision-useful valuation, business-unit coverage,
risks, decision-change conditions and public sources only.  Runtime hashes,
stage traces, evidence IDs and model-role explanations are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext
from html import escape
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from .post_freeze import MarketComparisonBundle
from .records import MarketObservation
from .ledger import EvidenceLedger
from .source_reporting import broker_reference_source_links, canonical_verification_url
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
class InvestorReportBrief:
    """Host-authored editorial selection bound to an exact full public report.

    This is not financial input or an automatic semantic-verification receipt.
    An operator reviews coverage; the formatter only checks binding/structure.
    """

    source_report_sha256: str
    conclusion: str
    key_assumptions: tuple[str, ...]
    key_evidence: tuple[str, ...]
    change_reason: str
    risks: tuple[str, ...]
    scope_limits: tuple[str, ...]

    def validate(self) -> None:
        if not isinstance(self.source_report_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", self.source_report_sha256):
            raise ValueError("decision brief requires the full source report SHA-256")
        fields = [self.conclusion, self.change_reason]
        for name in ("key_assumptions", "key_evidence", "risks", "scope_limits"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not 1 <= len(values) <= 4:
                raise ValueError(f"decision brief {name} requires one to four complete items")
            fields.extend(values)
        for text in fields:
            if (not isinstance(text, str) or not text.strip()
                    or "\n" in text or "\r" in text
                    or not text.rstrip().endswith((".", "!", "?", "。"))):
                raise ValueError("decision brief fields require complete single-line sentences")
            _validate_public_report(text)


def investor_report_brief_from_dict(payload: Mapping[str, object]) -> InvestorReportBrief:
    allowed = {"source_report_sha256", "conclusion", "key_assumptions", "key_evidence",
               "change_reason", "risks", "scope_limits"}
    if not isinstance(payload, Mapping) or set(payload) != allowed:
        raise ValueError("decision brief requires exactly the editorial fields; no financial override fields")
    lists: dict[str, tuple[str, ...]] = {}
    for name in ("key_assumptions", "key_evidence", "risks", "scope_limits"):
        value = payload[name]
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"decision brief {name} must be a list")
        lists[name] = tuple(value)
    brief = InvestorReportBrief(
        source_report_sha256=payload["source_report_sha256"], conclusion=payload["conclusion"],
        change_reason=payload["change_reason"], **lists,
    )
    brief.validate()
    return brief


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
    decision_brief: InvestorReportBrief | None = None

    def validate(self) -> None:
        if self.decision_brief is not None:
            if not isinstance(self.decision_brief, InvestorReportBrief):
                raise ValueError("decision brief must be typed")
            self.decision_brief.validate()
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
        "decision_brief",
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
        decision_brief=(
            investor_report_brief_from_dict(payload["decision_brief"])
            if payload.get("decision_brief") is not None else None
        ),
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
    if data.get("broker_comparison") is not None:
        return _broker_comparison_lines(data)
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


def broker_reference_summary(data: Mapping[str, object]) -> str | None:
    """Summarize rich source availability without inventing a named consensus.

    ``None`` preserves the caller's existing Street fallback. Counts here are
    original documents or declared aggregate samples, never independent brokers.
    """
    from .broker_comparison import BrokerComparisonResult, BrokerReferenceKind

    result = data.get("broker_comparison")
    if result is None:
        return None
    if not isinstance(result, BrokerComparisonResult):
        raise ValueError("public broker summary requires a typed result")
    named = tuple(ref for ref in result.references
                  if ref.reference_kind is BrokerReferenceKind.NAMED_REPORT)
    targets = tuple(ref for ref in named if ref.target_price is not None)
    parts = ([f"개별 목표가 원문 {len(targets)}건(시점·기준별 참고)"] if targets
             else ["확인된 개별 목표가 없음"])
    nr = tuple(ref for ref in named
               if str(ref.rating or "").strip().upper().replace(" ", "_") in {"NR", "NOT_RATED"})
    if nr:
        with_claims = sum(bool(ref.claims) for ref in nr)
        parts.append(f"NR 원문 {len(nr)}건 " + ("근거 비교" if with_claims == len(nr)
                                                 else "확인(일부 근거만 구조화)" if with_claims
                                                 else "확인(메타데이터)"))
    other = tuple(ref for ref in named if ref not in nr and ref.target_price is None)
    if other:
        parts.append(f"목표가 없는 원문 {len(other)}건 확인")
    for ref in result.references:
        if ref.reference_kind is not BrokerReferenceKind.AGGREGATE_SNAPSHOT:
            continue
        provider = _broker_text(ref.provider_name)
        if ref.target_price is None:
            parts.append(f"{provider} 공개 집계 확인(숫자 목표가 미확인)")
            continue
        amount = (_broker_number(ref.target_price) + "원" if ref.target_currency == "KRW"
                  else _broker_quantity(ref.target_price, ref.target_currency))
        limited = (ref.target_share_basis == "UNKNOWN" or ref.original_published_at is None
                   or ref.valuation_method is None or ref.broker is None)
        qualifier = "기준 미확인·조건부 참고" if limited else "조건부 참고"
        parts.append(f"{provider} 공개 집계 {amount}(표본 {ref.aggregate_report_count}건, {qualifier})")
    return " · ".join(parts)


def _broker_text(value: object) -> str:
    """Render source-derived text as text, not table or HTML instructions."""
    text = escape(" ".join(str(value).split()), quote=False)
    for character in ("\\", "|", "[", "]", "`"):
        text = text.replace(character, "\\" + character)
    return text


def _broker_number(value: object) -> str:
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("broker comparison cannot display a non-finite number")
    # Presentation precision, not a change to the immutable comparison. Keep
    # tiny disclosed values exact rather than turning them into a false zero.
    if abs(number) >= Decimal("0.001"):
        with localcontext() as context:
            context.prec = max(28, len(number.as_tuple().digits) + abs(number.adjusted()) + 5)
            places = max(3, 5 - number.adjusted())
            number = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    formatted = f"{number:,f}"
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def _broker_quantity(value: object, unit: object, currency: object = "") -> str:
    label = {
        "KRW_billion": "십억원", "KRW billion": "십억원",
        "KRW_million": "백만원", "KRW_thousand": "천원",
        "KRW": "원", "KRW/share": "원/주", "shares": "주",
        "USD": "달러", "USD_million": "백만 달러", "USD billion": "십억 달러",
        "percent": "%", "ratio": "비율",
    }.get(str(unit), str(unit) or str(currency))
    return f"{_broker_number(value)} {_broker_text(label)}".rstrip()


def _broker_metric_value(metric: Mapping[str, object]) -> str:
    basis = metric.get("basis", {})
    if not isinstance(basis, Mapping):
        raise ValueError("broker comparison metric basis is missing")
    unit, currency = basis.get("unit", ""), basis.get("currency", "")
    if metric.get("value") is not None:
        return _broker_quantity(metric["value"], unit, currency)
    if metric.get("low") is not None and metric.get("high") is not None:
        return (
            _broker_quantity(metric["low"], unit, currency)
            + " ~ " + _broker_quantity(metric["high"], unit, currency)
        )
    return "수치 미공개"


def _broker_basis_label(basis: Mapping[str, object]) -> str:
    metric = {
        "revenue": "매출", "total_revenue": "총매출",
        "consolidated_revenue": "연결 매출", "operating_profit": "영업이익",
        "operating_income": "영업이익", "consolidated_operating_profit": "연결 영업이익",
        "lease_adjusted_ebit": "리스 조정 영업이익", "fcff": "기업잉여현금흐름",
        "net_income": "순이익", "EPS": "주당순이익",
        "scope_revenue": "SCOPE 매출", "oncology_revenue": "종양학 사업 매출",
        "screening_revenue": "검진 사업 매출",
    }.get(str(basis.get("metric", "")), str(basis.get("metric", "지표 미확인")))
    start, end = basis.get("period_start", "UNKNOWN"), basis.get("period_end", "UNKNOWN")
    period = "기간 미확인" if "UNKNOWN" in {start, end} else f"{start}~{end}"
    frequency = {"annual": "연간", "ANNUAL": "연간", "quarterly": "분기", "QUARTERLY": "분기"}.get(
        str(basis.get("frequency", "")), str(basis.get("frequency", ""))
    )
    return _broker_text(f"{metric} · {period} · {frequency}".replace("UNKNOWN", "미확인"))


def _broker_basis_context(basis: Mapping[str, object]) -> str:
    labels = {"CONSOLIDATED": "연결", "STANDALONE": "별도",
              "GROUP_INCLUDING_SUBSIDIARIES": "자회사 포함 전사",
              "IFRS_REPORTED": "공시 IFRS", "UNKNOWN": "미확인"}
    return " · ".join(
        f"{label}: {_broker_text(labels.get(str(basis.get(key)), str(basis.get(key, '미확인'))))}"
        for key, label in (("scope", "연결·별도"), ("perimeter", "사업 범위"),
                           ("accounting_basis", "회계 기준"), ("currency", "통화"))
    )


def _broker_reason(reason: object) -> str:
    text = str(reason)
    phrases = {
        "No same-definition own frozen metric was supplied; no proxy substituted.": "동결 모형에 같은 정의의 지표가 없어 대체 지표를 넣지 않았습니다",
        "No same-period own metric; annual/quarterly or neighboring-year proxies are not substituted.": "동일 기간의 동결 지표가 없어 연간·분기 또는 인접 연도 값으로 대체하지 않았습니다",
        "Target share/corporate-action basis is unknown; arithmetic is a conditional reference, not a normalized target.": "주식 수·자본변동 조정 기준이 미확인으로, 이 산술 비교는 동일 주식 기준으로 정규화된 목표가 비교가 아닌 조건부 참고입니다",
        "Original report publication is unknown; the declared snapshot/observation time is not a report publication date.": "원 보고서 발행일은 미확인이며, 집계·관측 시각을 원 보고서 발행일로 간주하지 않았습니다",
        "Original valuation method is undisclosed.": "원 평가방법이 공개되지 않았습니다",
        "The declared target horizon has expired.": "명시된 목표가 적용 기간이 지났습니다",
        "No frozen valuation supplied.": "동결 가치가 제공되지 않아 수치 비교를 하지 않았습니다",
        "No FX conversion was authorized.": "통화가 달라 수치 차이를 산출하지 않았습니다. 환율 변환을 임의로 적용하지 않았습니다",
        "A segment subtotal is not a whole-company target.": "사업부 소계를 전사 목표가와 비교하지 않았습니다",
        "Some forecasts are only juxtaposed because metric/period/basis evidence is incomplete or incompatible.": "지표·기간·기준 근거가 불완전하거나 서로 달라 일부 전망은 차이를 계산하지 않고 나란히 제시했습니다",
        "later comparison evidence; not knowable at the intrinsic cutoff and not retroactive valuation input.": "내재가치 기준시점 이후 확인된 비교 자료이며 과거 가치평가 입력에 소급 반영하지 않았습니다",
        "original publication date unknown.": "원 보고서 발행일 미확인",
    }
    if text.startswith("Underlying source family "):
        return "여러 보고서가 동일 원자료를 이용했습니다. 증권사 수를 독립 근거 수로 세지 않았습니다"
    for before, after in phrases.items():
        text = text.replace(before, after)
    for before, after in (
        ("period_start", "기간 시작"), ("period_end", "기간 종료"),
        ("accounting_basis", "회계 기준"), ("frequency", "측정 주기"),
        ("perimeter", "사업 범위"), ("scope", "연결·별도 범위"),
        ("currency", "통화"), ("unit", "단위"), ("metric", "지표 정의"),
        ("broker=", "증권사="), ("own=", "동결 모형="), ("UNKNOWN", "미확인"),
    ):
        text = text.replace(before, after)
    return _broker_text(text)


def _broker_comparison_lines(data: Mapping[str, object]) -> list[str]:
    """Display the verified post-freeze comparison, not a new valuation claim."""
    from .broker_comparison import BrokerComparisonResult, canonical_broker_payload

    result = data.get("broker_comparison")
    if not isinstance(result, BrokerComparisonResult):
        raise ValueError("public broker comparison requires a typed result")
    payload = canonical_broker_payload(result)
    reference_labels = {
        reference["reference_id"]: (
            reference["provider_name"] + " 공개 집계"
            if reference["reference_kind"] == "AGGREGATE_SNAPSHOT"
            else reference.get("broker") or reference["provider_name"]
        )
        for reference in payload["references"]
    }

    def reason_text(reason: object) -> str:
        text = str(reason)
        for reference_id, label in reference_labels.items():
            text = text.replace(reference_id + ":", label + ":")
        return _broker_reason(text)
    lines = [
        "### 자료와 계산된 비교",
        f"- 내재가치 기준일: {_broker_text(payload['intrinsic_as_of'])} · "
        f"사후 비교 기준시각: {_broker_text(payload['comparison_as_of'])}.",
        "- 목표가 유무와 투자의견은 별개입니다. 전망·가정의 비교 결과는 동결된 내재가치나 확률을 바꾸지 않습니다.",
    ]
    targets = {row["reference_id"]: row for row in payload["target_comparisons"]}
    for reference in payload["references"]:
        is_aggregate = reference["reference_kind"] == "AGGREGATE_SNAPSHOT"
        provider = _broker_text(reference["provider_name"])
        broker = _broker_text(reference.get("broker") or "원 증권사 미확인")
        name = f"{provider} 공개 집계" if is_aggregate else broker
        published = _broker_text(reference.get("original_published_at") or "미확인")
        observed = _broker_text(reference["observed_at"])
        lines.extend(("", f"#### {name}", f"- 원 보고서 발행일: {published} · 자료 확인시각: {observed}."))
        if is_aggregate:
            lines.append(f"- 귀속: {broker}. 이 집계를 특정 증권사의 개별 보고서로 간주하지 않습니다.")
            if reference.get("snapshot_as_of"):
                lines.append(f"- 집계 기준시각: {_broker_text(reference['snapshot_as_of'])}.")
            if reference.get("last_rating_at"):
                lines.append(f"- 집계에 표시된 마지막 투자의견 시점: {_broker_text(reference['last_rating_at'])} "
                             "(원 보고서 발행일과 동일하다고 확인되지 않았습니다).")
        if reference.get("rating"):
            lines.append(f"- 원문 투자의견: {_broker_text(reference['rating'])}.")
        if reference.get("target_price") is None:
            lines.append("- 숫자 목표가: 미제시·미확인. 목표가 차이는 산출하지 않지만 아래 실적·논리 비교와 구분합니다.")
        else:
            target = _broker_quantity(reference["target_price"], reference.get("target_currency", ""))
            label = "공개 집계 목표가" if is_aggregate else "원문 목표가"
            lines.append(f"- {label}: {target}.")
            if is_aggregate and reference.get("aggregate_report_count") is not None:
                low = _broker_quantity(reference["aggregate_min_target_price"], reference["target_currency"])
                high = _broker_quantity(reference["aggregate_max_target_price"], reference["target_currency"])
                lines.append(f"- 집계 표본: {reference['aggregate_report_count']}건 · 범위 {low}~{high}. "
                             "표본 수가 독립 검증이나 시장 전체의 대표성을 뜻하지 않습니다.")
        if reference.get("valuation_method") or reference.get("valuation_base_year"):
            lines.append(f"- 공개된 평가방법: {_broker_text(reference.get('valuation_method') or '미확인')} · "
                         f"평가 기준연도: {_broker_text(reference.get('valuation_base_year') or '미확인')}.")
        else:
            lines.append("- 평가방법·기준연도: 미확인. 목표가에서 역산해 채우지 않았습니다.")

        target_row = targets.get(reference["reference_id"])
        if target_row and target_row.get("envelope") is not None:
            envelope = target_row["envelope"]
            lines.append("- 조건부 주당가치 차이(비교용 주당가치 − 참고 목표가):")
            for gap in envelope["scenario_gaps"]:
                value = _broker_quantity(gap["gap_per_share"], envelope["currency"])
                percent = Decimal(gap["gap_pct_of_reference"])
                lines.append(f"  - {_broker_text(gap['scenario_id'])}: {value} ({percent:+.1%}).")
                if gap["limited_liability_floor_applied"]:
                    signed = _broker_quantity(gap["intrinsic_value_per_share"], envelope["currency"])
                    compared = _broker_quantity(gap["comparison_equity_value_per_share"], envelope["currency"])
                    lines.append(f"    - 동결 잔여가치 {signed} 그대로 보존. 유한책임 주식의 비교용 값만 {compared}로 제한했습니다.")
            if envelope.get("expected_gap") is None:
                lines.append("  - 확률가중 목표가 차이는 산출하지 않았습니다.")
            else:
                expected = envelope["expected_gap"]
                lines.append("  - 확률가중 차이: " + _broker_quantity(expected["gap_per_share"], envelope["currency"])
                             + f" ({Decimal(expected['gap_pct_of_reference']):+.1%}).")
        elif target_row and target_row.get("intrinsic_reference_low") is not None:
            low = _broker_quantity(target_row["intrinsic_reference_low"], target_row["target_currency"])
            high = _broker_quantity(target_row["intrinsic_reference_high"], target_row["target_currency"])
            lines.append(f"- 동결 가치 참고범위: {low}~{high}. 단일 확률가중 값으로 바꾸지 않았습니다.")
            if target_row.get("target_gap_to_low") is not None and target_row.get("target_gap_to_high") is not None:
                low_gap = _broker_quantity(target_row["target_gap_to_low"], target_row["target_currency"])
                high_gap = _broker_quantity(target_row["target_gap_to_high"], target_row["target_currency"])
                lines.append(f"- 목표가 − 동결 범위 하단: {low_gap}; 목표가 − 동결 범위 상단: {high_gap}.")
        elif target_row and reference.get("target_price") is not None:
            lines.append("- 목표가 차이는 산출하지 않았습니다.")
        if target_row and reference.get("target_price") is not None and target_row["reasons"]:
            lines.append("- 목표가 비교 제한: " + "; ".join(reason_text(x) for x in target_row["reasons"]) + ".")

        rows = [row for row in payload["metric_comparisons"] if row["reference_id"] == reference["reference_id"]]
        if rows:
            lines.extend(("", "| 지표·기간 | 증권사 제시값 | 동결 모형 | 동일기준 차이 |", "|---|---:|---|---|"))
            for row in rows:
                metric = row["broker_metric"]
                own = row.get("own_metric")
                own_text = "동일 지표 미확보" if own is None else (
                    f"{_broker_text(own['scenario_id'])} · {_broker_metric_value(own['metric'])}"
                )
                difference = "기준 차이·미확인으로 미산출"
                if row["status"] == "COMPARABLE":
                    basis = metric["basis"]
                    if row.get("difference") is not None:
                        difference = _broker_quantity(row["difference"], basis["unit"], basis["currency"])
                    elif row.get("difference_low") is not None and row.get("difference_high") is not None:
                        difference = _broker_quantity(row["difference_low"], basis["unit"], basis["currency"]) + " ~ " + _broker_quantity(row["difference_high"], basis["unit"], basis["currency"])
                    else:
                        difference = "차이 미산출"
                lines.append(f"| {_broker_basis_label(metric['basis'])} | {_broker_metric_value(metric)} | {own_text} | {difference} |")
            for row in rows:
                lines.append("- 증권사 지표 기준: " + _broker_basis_context(row["broker_metric"]["basis"]) + ".")
                if row["reasons"]:
                    lines.append("- 비교 제한: " + "; ".join(reason_text(x) for x in row["reasons"]) + ".")
                    own = row.get("own_metric")
                    if own is not None:
                        lines.append(f"  - 동결 모형의 지표·기간: {_broker_basis_label(own['metric']['basis'])}.")
                        lines.append("  - 동결 모형 기준: " + _broker_basis_context(own["metric"]["basis"]) + ".")
        for claim in reference["claims"]:
            lines.append(f"- 원문 근거·논리: {_broker_text(claim['summary'])}")
        if not rows and not reference["claims"]:
            lines.append("- 실적·논리 항목이 구조화되지 않은 자료로, 원문 메타데이터와 공개된 목표가만 확인했습니다.")
        url = canonical_verification_url(reference["source_ref"])
        if url is None:
            raise ValueError("broker comparison requires public source links")
        lines.append(f"- [해당 자료 원문]({url})")

    if payload["warnings"]:
        lines.extend(("", "### 비교의 한계"))
        lines.extend(f"- {reason_text(warning)}" for warning in payload["warnings"])
    lines.extend(("", "### 괴리 해석·재검토"))
    neutral_note = ("괴리는 모형의 누락 또는 시장·증권사 기대의 과도함을 모두 시사할 수 있습니다. "
                    "재검토가 상향·하향·불변 중 어느 결론인지 새 계산 전에 정하지 않습니다.")
    receipt = data.get("broker_review_receipt")
    if receipt is None:
        lines.append("- 근거의 추가 검토가 필요합니다. 이 비교만으로 새 가정이나 가치 변경을 승인하지 않았습니다.")
    else:
        from .broker_judgment import BrokerReviewReceipt
        if not isinstance(receipt, BrokerReviewReceipt):
            raise ValueError("broker interpretation requires a typed review receipt")
        if any(getattr(receipt, field) != getattr(result, field) for field in (
            "target_id", "intrinsic_as_of", "comparison_as_of", "freeze_token_hash",
        )):
            raise ValueError("broker interpretation does not match the frozen comparison")
        status = render_broker_review_status(receipt)
        lines.append("- 판단 현황: " + status)
        summary_budget = 1000 - len(neutral_note) - len(status) - 45
        # Keep the interpretation distinct from the deterministic values above.
        lines.append("- " + render_broker_review_summary(receipt, max_chars=summary_budget))
    lines.append("- " + neutral_note)
    return lines


_REVIEW_LABELS = {
    "REFERENCE_ONLY": "참고 유지",
    "VERIFY_FOR_NEW_RUN": "근거 확인 후 새 평가 검토",
    "CONDITIONAL_HYPOTHESIS": "조건부 가설로 재조사",
    "REJECT": "채택하지 않음",
    "RESEARCH_AND_REVALUE": "근거 재검토 후 재평가",
    "PROPOSE_NEW_RUN_ASSUMPTION": "새 평가 가정 후보 제안",
    "UNRESOLVED": "추가 확인 필요",
}


def render_broker_review_status(receipt: object) -> str:
    """A short factual disposition count, not an economic approval."""
    from .broker_judgment import BrokerReviewReceipt

    if not isinstance(receipt, BrokerReviewReceipt):
        raise ValueError("broker interpretation requires a typed review receipt")
    receipt.validate()
    counts: dict[str, int] = {}
    for decision in receipt.decisions:
        label = _REVIEW_LABELS[decision.disposition]
        counts[label] = counts.get(label, 0) + 1
    return " · ".join(f"{label} {count}건" for label, count in counts.items()) + "."


def render_broker_review_summary(receipt: object, *, max_chars: int = 900) -> str:
    """Render complete source-bound paragraphs within the display budget.

    No word/sentence is cut to fit and HTML escaping is accounted for before
    selection. The full typed receipt remains the complete judgment record.
    """
    from .broker_judgment import BrokerReviewReceipt

    if not isinstance(receipt, BrokerReviewReceipt):
        raise ValueError("broker interpretation requires a typed review receipt")
    receipt.validate()
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or not 250 <= max_chars <= 1000:
        raise ValueError("broker summary budget must be between 250 and 1000")
    suffix = (
        "현재 동결가치·가정은 변경하지 않았습니다. "
        "재검토 결과는 상향·하향·유지 모두 가능하며, 반영에는 새 계산과 감사가 필요합니다."
    )
    detail_note = "나머지 개별 판단과 불확실성은 상세 자료에 보존했습니다."
    priority = {
        "PROPOSE_NEW_RUN_ASSUMPTION": 0, "RESEARCH_AND_REVALUE": 1,
        "CONDITIONAL_HYPOTHESIS": 1, "VERIFY_FOR_NEW_RUN": 1,
        "UNRESOLVED": 2, "REJECT": 2, "REFERENCE_ONLY": 3,
    }
    selected: list[str] = []
    for decision in sorted(receipt.decisions, key=lambda item: priority[item.disposition]):
        paragraph = (
            f"{_REVIEW_LABELS[decision.disposition]}: {decision.rationale} "
            f"(불확실성: {decision.uncertainty})"
        )
        candidate = " ".join((*selected, paragraph, detail_note, suffix))
        if len(_broker_text(candidate)) > max_chars:
            break
        selected.append(paragraph)
    omitted = len(selected) < len(receipt.decisions)
    if not selected:
        selected.append(render_broker_review_status(receipt))
    if omitted or not receipt.decisions:
        selected.append(detail_note)
    rendered = _broker_text(" ".join((*selected, suffix)))
    if len(rendered) > max_chars:
        raise ValueError("complete broker summary exceeds its display budget")
    return rendered


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
    lines = []
    for reference in broker_reference_source_links(data):
        if reference.url not in linked:
            label = _broker_text(" / ".join(reference.labels))
            lines.append(f"- 사후 비교 자료: [{label}]({reference.url})")
            linked.add(reference.url)
    ledger = data.get("evidence_ledger")
    if not isinstance(ledger, EvidenceLedger):
        return lines
    for record in ledger.active():
        if not (
            getattr(record, "research_receipt", None)
            or getattr(record, "business_cashflow_receipt", None)
            or getattr(record, "derived_case_ref", None)
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
    broker_summary = broker_reference_summary(data)
    lines = [
        f"# {company} 투자보고서",
        "",
        "## 1. 투자판단 요약",
        f"- 투자의견: {opinion}",
        f"- 현재가: {current_price}",
        f"- 기준 내재가치: {_money(reference)}",
        f"- 가치평가 범위: {_money(low)}~{_money(high)} ({range_label})",
        *((f"- 증권사 참고자료: {broker_summary}",) if broker_summary is not None else ()),
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
    if data.get("broker_comparison") is None and street is not None and hasattr(street, "mean_target_price"):
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
    broker_summary = broker_reference_summary(data)
    if broker_summary is not None:
        lines.append(f"- 증권사 참고자료: {broker_summary}")
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
        change = (
            f"주당 {_money(abs(delta))} {'증가' if delta > 0 else '감소'}"
            if delta != 0 else "주당가치 변동 없음"
        )
        lines.append(
            "- 변경 요약: 이전 "
            f"{profile.prior_scope_label or '기준가'} {_money(profile.prior_reference_per_share)} → "
            f"수정 {_money(scenarios['Base'])}, {change}."
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


@dataclass(frozen=True)
class InvestorReportDocuments:
    """Two publication views of one valuation; neither recalculates values."""

    main_report: str
    detail_report: str


_DETAIL_HEADINGS = (
    "## 1. 투자판단 요약",
    "## 2. 투자논리",
    "## 3. 가치평가와 민감도",
    "## 4. 사업부별 평가",
    "## 5. 위험과 판단 변경 조건",
    "## 6. 증권사·시장 비교",
    "## 7. 원문 자료",
)


def _report_sections(report: str) -> tuple[str, dict[str, list[str]]]:
    """Accept only the known public renderer contract, never arbitrary prose."""
    lines = report.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError("compact report requires the public report title")
    headings = tuple(line for line in lines if line.startswith("## "))
    if headings != _DETAIL_HEADINGS:
        raise ValueError("compact report requires the complete known seven-section report")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[1:]:
        if line in _DETAIL_HEADINGS:
            current = line
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
        elif line.strip():
            raise ValueError("unexpected public text before report sections")
    return lines[0], sections


def compact_investor_report(
    full_report: str,
    *,
    detail_filename: str,
    brief: InvestorReportBrief | None = None,
) -> str:
    """Create a concise main from a verified public report without recalculation.

    This pure formatter is also the frozen-report display-only path. Selection
    moves complete, labelled fields to the linked detail document; it does not
    summarize source prose, truncate words, choose assumptions or alter values.
    Critical conclusion, scenario conditions, exclusions, all risks and action
    conditions remain in the main. The caller must publish ``full_report`` as
    the linked detail document in the same immutable package.
    """
    if not re.fullmatch(r"[\w][\w.-]*\.md", detail_filename) or ".." in detail_filename:
        raise ValueError("detail filename must be a safe local Markdown filename")
    _validate_public_report(full_report)
    title, sections = _report_sections(full_report)
    summary, thesis, valuation, segments, risks, comparison, sources = (
        sections[heading] for heading in _DETAIL_HEADINGS
    )
    if not any(line.startswith("- 핵심 결론:") for line in summary):
        raise ValueError("compact report requires the complete conclusion")
    if not any(line.startswith("- 현재가:") for line in summary):
        raise ValueError("compact report requires current-price availability and timestamp")
    if brief is not None:
        if not isinstance(brief, InvestorReportBrief):
            raise ValueError("decision brief must be typed")
        brief.validate()
        if sha256(full_report.encode("utf-8")).hexdigest() != brief.source_report_sha256:
            raise ValueError("decision brief is stale or bound to a different full report")
        return _compact_with_brief(title, sections, brief, detail_filename=detail_filename)
    detail_link = f"[상세 근거·계산·원문 전체]({detail_filename})"
    broker_availability = [line for line in summary if line.startswith("- 증권사 참고자료:")]
    lines = [title, "", "## 1. 결론", *(
        line for line in summary if line and not line.startswith("- 증권사 참고자료:")
    ), "", "## 2. 조건별 가치", ""]

    # The main keeps the valuation table's complete conditions, but presents
    # each scenario as a readable bullet instead of a dense four-column table.
    before_sensitivity: list[str] = []
    for line in valuation:
        if line.startswith("### "):
            break
        before_sensitivity.append(line)
    scenario_count = 0
    for line in before_sensitivity:
        if line.startswith("| 시나리오 |") or line.startswith("|---|---:|---|---|"):
            continue
        if any(line.startswith(f"| {name} |") for name in ("Down", "Base", "Bull")):
            cells = [cell.strip() for cell in line.split("|")]
            if len(cells) != 6:
                raise ValueError("scenario conditions must use the known four-field table")
            _, scenario, value, condition, impact, _ = cells
            lines.append(f"- **{scenario} · {value}** — {condition} {impact}")
            scenario_count += 1
        elif line.startswith("- 방법 선택 이유:"):
            continue
        elif line:
            lines.append(line)
    if scenario_count != 3:
        raise ValueError("compact report requires all three conditional values")

    # A partial/unvalued business must never disappear into a detail-only view.
    # Full segment tables belong in detail; explicit unvalued rows stay here.
    unvalued_rows = [line for line in segments if "| 미평가, 추가 확인 필요 |" in line]
    if unvalued_rows:
        lines.extend(("", "미평가 사업부는 0원으로 처리하지 않았습니다."))
        lines.extend(unvalued_rows)
    lines.extend(("", "## 3. 핵심 근거", ""))
    for line in thesis:
        if line.startswith(("### ", "- 관찰과 가정:", "- 근거:", "- 가치 연결:", "- 반증 조건:")):
            lines.append(line)
    lines.extend(("", "## 4. 남은 위험과 판단 조건", *risks))

    lines.extend(("", "## 5. 증권사·시장 비교", *broker_availability))
    selected_comparison = [line for line in comparison if line.startswith((
        "- 내재가치 기준일:", "- 판단 현황:",
        "- 비교 가능한 증권사 자료를 확보하지 못했습니다.",
        "- 근거의 추가 검토가 필요합니다.",
    ))]
    lines.extend(selected_comparison)
    if not broker_availability and not selected_comparison:
        # The legacy Street path has no rich coverage header. Retain the count
        # of original complete rows without suggesting an independent consensus.
        count = sum(line.startswith("- **") for line in comparison)
        lines.append(f"- 증권사 자료 {count}건의 시점·기준별 비교는 상세에 보존했습니다.")
    lines.append(
        "- 목표가·실적 전망의 원문, 동일기준 차이와 미해결 판단은 상세에서 확인할 수 있습니다. "
        "가격 차이만으로 가정을 변경하지 않습니다."
    )
    lines.extend(("", "## 6. 출처와 상세", f"- {detail_link}"))
    # Keep a few directly clickable entry sources; every source and all its
    # claims remain in the detail, not silently discarded by a character limit.
    linked: set[str] = set()
    for line in sources:
        if line.startswith("- ") and "](http" in line and line not in linked:
            lines.append(line)
            linked.add(line)
            if len(linked) == 3:
                break
    lines.append(
        "- 전체 가정·사업부 범위·반증 조건·계산 민감도·비교표·원문 출처는 연결된 상세 자료에 보존했습니다."
    )
    # Remove only exact repeated lines. Different caveats are not semantically
    # deduplicated or treated as equivalent by deterministic string rules.
    seen: set[str] = set()
    clean: list[str] = []
    for line in lines:
        if line and line in seen:
            continue
        if line:
            seen.add(line)
        if not line and (not clean or not clean[-1]):
            continue
        clean.append(line)
    return _validate_public_report("\n".join(clean).rstrip() + "\n")


def _compact_with_brief(
    title: str,
    sections: dict[str, list[str]],
    brief: InvestorReportBrief,
    *,
    detail_filename: str,
) -> str:
    """Combine reviewed prose with financial fields only from the frozen text."""
    summary, _, valuation, segments, risks, comparison, sources = (
        sections[heading] for heading in _DETAIL_HEADINGS
    )
    source_summary = [line.replace("- 현재가:", "- 비교 시세(관측시각):", 1)
                      for line in summary if line and not line.startswith((
        "- 핵심 결론:", "- 증권사 참고자료:", "- 판단 근거:",
    ))]
    judgment = next((line for line in summary if line.startswith("- 판단 근거:")), "")
    # These are the existing deterministic renderer's explicit status fields,
    # not a semantic classifier of source documents or host-authored prose.
    if any(marker in judgment for marker in (
        "보정된 시나리오 확률과 확률가중 기대값이 없습니다.",
        "확률가중 기대값은 산출되지 않았습니다.",
    )):
        source_summary.append("- 확률·기대값: 시나리오 확률·확률가중 기대값 미산정.")
    elif any(line.startswith("- 부분 내재가치:") for line in summary):
        source_summary.append("- 확률·기대값: 부분 평가이며 확률가중 기대값·상승여력 미산정.")
    else:
        statuses = re.findall(
            r"(?:적용 확률은 .*?입니다\.|확률가중 기대값은 .*?입니다\.|모형 경로 중 .*?입니다\.|보정된 성공확률은 산출하지 않았습니다\.)",
            judgment,
        )
        source_summary.extend("- 확률·기대값: " + status for status in statuses)
        if judgment and not statuses:
            # Unknown future renderer shape fails safe: preserve rather than
            # guessing that a missing status means calibrated/no risk.
            source_summary.append(judgment)
    lines = [title, "", "## 1. 결론", *source_summary,
             "- 핵심 결론: " + _broker_text(brief.conclusion), "", "## 2. 가치와 핵심 가정"]
    count = 0
    for line in valuation:
        if line.startswith("### "):
            break
        if any(line.startswith(f"| {name} |") for name in ("Down", "Base", "Bull")):
            cells = [cell.strip() for cell in line.split("|")]
            if len(cells) != 6:
                raise ValueError("scenario conditions must use the known four-field table")
            lines.append(f"- **{cells[1]} · {cells[2]}**")
            count += 1
        elif line.startswith(("- 평가방법:", "- 변경 요약:", "- 매수 규칙:",
                              "- 유한책임 반영 전 확률가중 잔여가치:", "- 음수 잔여가치")):
            lines.append(line)
        elif line.startswith("| 부채 차감 후 주당 잔여가치 |"):
            cells = [cell.strip() for cell in line.split("|")]
            if len(cells) != 6:
                raise ValueError("signed residual disclosure has an unknown shape")
            lines.append("- 부채 차감 후 잔여가치: " + ", ".join(
                f"{name} {value}" for name, value in zip(("Down", "Base", "Bull"), cells[2:5])
            ) + ".")
    if count != 3:
        raise ValueError("compact report requires all three conditional values")
    lines.append("- 변경 이유: " + _broker_text(brief.change_reason))
    lines.extend("- " + _broker_text(text) for text in brief.key_assumptions)
    # Scope prose may be condensed by the reviewed brief, but an unvalued
    # material business is a deterministic fact and cannot be editorially lost.
    for line in segments:
        if "| 미평가, 추가 확인 필요 |" in line:
            lines.append(line)
    lines.extend(("", "## 3. 핵심 근거"))
    lines.extend("- " + _broker_text(text) for text in brief.key_evidence)
    lines.extend(("", "## 4. 위험과 해석 범위"))
    lines.extend("- " + _broker_text(text) for text in (*brief.risks, *brief.scope_limits))
    lines.extend(line for line in risks if line.startswith("- 행동 가능 조건:"))
    lines.extend(("", "## 5. 비교와 출처"))
    lines.extend(line for line in summary if line.startswith("- 증권사 참고자료:"))
    lines.extend(line.split(" · 사후 비교 기준시각:", 1)[0] if line.startswith("- 내재가치 기준일:") else line
                 for line in comparison if line.startswith((
        "- 내재가치 기준일:", "- 판단 현황:",
        "- 비교 가능한 증권사 자료를 확보하지 못했습니다.",
        "- 근거의 추가 검토가 필요합니다.",
    )))
    lines.append(f"- [상세 근거·계산·원문 전체]({detail_filename})")
    source_lines = list(dict.fromkeys(line for line in sources if line.startswith("- ") and "](http" in line))
    lines.extend(source_lines[:3])
    return _validate_public_report("\n".join(lines).rstrip() + "\n")


def append_report_detail_links(full_report: str, *, has_broker_review: bool) -> str:
    """Link complete immutable records before binding the editorial brief.

    Idempotent for the same source/record availability. Publication owns checking
    these relative targets exist in the same immutable bundle.
    """
    _validate_public_report(full_report)
    _report_sections(full_report)
    links = [
        "- [계산·감사 상세](final_report.md)",
        "- [전체 계산 입력·가정](compiled_assumptions.json)",
        "- [전체 실행 기록](control_plane_trace.json)",
    ]
    if has_broker_review:
        links.append("- [전체 개별 판단·불확실성 기록](broker_review.json)")
    missing = [line for line in links if line not in full_report.splitlines()]
    if not missing:
        return full_report
    heading = "### 계산·판단 기록"
    addition = ([] if heading in full_report.splitlines() else [heading]) + missing
    return full_report.rstrip() + "\n\n" + "\n".join(addition) + "\n"


def render_investor_report_documents(
    data: Mapping[str, object],
    profile: InvestorReportProfile,
    *,
    detail_filename: str,
) -> InvestorReportDocuments:
    """Default compact publication plus an unabridged public detail document."""
    detailed = (
        render_distributional_investor_report(data, profile)
        if isinstance(data.get("distributional_primary_result"), DistributionalPrimaryValuationResult)
        else render_investor_report(data, profile)
    )
    detailed = append_report_detail_links(
        detailed, has_broker_review=data.get("broker_review_receipt") is not None,
    )
    return InvestorReportDocuments(
        main_report=compact_investor_report(detailed, detail_filename=detail_filename,
                                           brief=profile.decision_brief),
        detail_report=detailed,
    )


__all__ = [
    "InvestorReportProfile",
    "InvestorReportDocuments",
    "InvestorReportBrief",
    "compact_investor_report",
    "append_report_detail_links",
    "load_investor_report_profile",
    "investor_report_brief_from_dict",
    "probability_weighted_equity_value",
    "render_distributional_investor_report",
    "render_investor_report",
    "render_investor_report_documents",
    "render_broker_review_status",
    "render_broker_review_summary",
]
