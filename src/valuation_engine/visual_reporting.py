from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from html import escape
import re
import textwrap
from typing import Any

from .context_strength_reporting import resolve_context_strength_linkage
from .report_localization import valuation_family_value_term_ko
from .source_reporting import build_source_link_index
from .valuation_execution import (
    CompanyValuationPlan,
    GenericValuationResult,
    IntrinsicValuationScope,
)
from .valuation_plan_compiler import (
    SegmentEvaluatorContract,
    ValuationPlanCompilation,
)


_SAFE_FILE_PART = re.compile(r"[^A-Za-z0-9._-]+")
_CARD_WIDTH = 1200
_CARD_HEIGHT = 1500


@dataclass(frozen=True)
class ReportVisual:
    filename: str
    alt_text: str
    svg: str


def _file_prefix(data: dict[str, Any]) -> str:
    identity = str(data.get("ticker") or data.get("target_id") or "REPORT")
    safe = _SAFE_FILE_PART.sub("_", identity).strip("_.") or "REPORT"
    return f"PRISM_{safe}"


def report_visual_filenames(data: dict[str, Any]) -> tuple[str, str]:
    prefix = _file_prefix(data)
    return (
        f"{prefix}_01_summary.svg",
        f"{prefix}_02_assumptions.svg",
    )


def _svg_text(
    value: object,
    *,
    x: int,
    y: int,
    size: int,
    weight: int = 400,
    fill: str = "#142A3A",
    anchor: str = "start",
) -> str:
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}">{escape(str(value))}</text>'
    )


def _wrapped_text(
    value: object,
    *,
    x: int,
    y: int,
    width: int,
    size: int,
    line_height: int,
    max_lines: int,
    weight: int = 400,
    fill: str = "#344B5A",
) -> tuple[str, int]:
    text = " ".join(str(value).split())
    lines = textwrap.wrap(
        text,
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip("., ") + "…"
    rendered = [
        _svg_text(
            line,
            x=x,
            y=y + index * line_height,
            size=size,
            weight=weight,
            fill=fill,
        )
        for index, line in enumerate(lines)
    ]
    return "\n".join(rendered), y + len(lines) * line_height


def _rect(x: int, y: int, width: int, height: int, *, fill: str, radius: int = 28) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
        f'rx="{radius}" fill="{fill}"/>'
    )


def _fmt_number(value: object, *, decimals: int = 0) -> str:
    number = Decimal(str(value))
    return f"{number:,.{decimals}f}"


def _scenario_label(scenario_id: str) -> str:
    return {
        "Down": "하방",
        "Core": "기준",
        "Bull": "상방",
        "Base": "기준",
    }.get(scenario_id, scenario_id)


def _price_text(value: object, unit: str) -> str:
    suffix = "원" if unit == "KRW" else f" {unit}"
    return f"{_fmt_number(value)}{suffix}"


def _source_links(data: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    links = build_source_link_index(data, require_all_http=False)
    return tuple((f"원문 {index}", item.url) for index, item in enumerate(links[:5], start=1))


def _source_footer(data: dict[str, Any], *, y: int) -> str:
    links = _source_links(data)
    lines = [
        _svg_text("출처", x=70, y=y, size=22, weight=700, fill="#D9E7EC"),
        _svg_text(
            "공시 사실과 분석가 가정의 근거 주소는 보고서 본문 ‘정보 출처’에서 확인할 수 있습니다.",
            x=140,
            y=y,
            size=20,
            fill="#D9E7EC",
        ),
    ]
    x = 70
    for label, url in links:
        safe_url = escape(url, quote=True)
        lines.append(
            f'<a href="{safe_url}" target="_blank">'
            + _svg_text(label, x=x, y=y + 42, size=20, weight=700, fill="#FFCB77")
            + "</a>"
        )
        x += 100
    return "\n".join(lines)


def _svg_document(*, title: str, description: str, body: str) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{_CARD_WIDTH}" height="{_CARD_HEIGHT}" viewBox="0 0 {_CARD_WIDTH} {_CARD_HEIGHT}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)}</title>
<desc id="desc">{escape(description)}</desc>
<style>
text {{ font-family: Pretendard, "Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif; }}
</style>
{body}
</svg>
'''


def _summary_card(data: dict[str, Any], filename: str) -> ReportVisual:
    company = str(data.get("company") or data.get("target_id") or "분석 대상")
    valuation = data.get("generic_valuation_result")
    if not isinstance(valuation, GenericValuationResult):
        raise ValueError("최종 요약 이미지에는 GenericValuationResult가 필요합니다")
    partial = valuation.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC

    parts = [
        _rect(0, 0, _CARD_WIDTH, _CARD_HEIGHT, fill="#F3F0E8", radius=0),
        _rect(0, 0, _CARD_WIDTH, 250, fill="#102D3E", radius=0),
        _svg_text("PRISM 최종보고서 · 1/2", x=70, y=70, size=24, weight=700, fill="#FFCB77"),
        _svg_text(company, x=70, y=145, size=54, weight=800, fill="#FFFFFF"),
        _svg_text("회사 강점 · 투자 결론 · 가치평가", x=70, y=202, size=30, weight=500, fill="#D9E7EC"),
    ]

    status, linkages, reason = resolve_context_strength_linkage(data)
    if linkages:
        linkage = linkages[0]
        insight_rows = (
            ("기업 강점", linkage.company_strength),
            ("재평가 연결", linkage.linkage_thesis),
            ("가치 포착", linkage.value_capture_path),
        )
    else:
        insight_rows = (
            ("연결 판단", "이번 실행에서는 환경 변화와 기업 강점의 별도 연결 인사이트가 적용되지 않았습니다."),
            ("검증 범위", reason or "공식 출처·결정론적 가치평가·감사 무결성을 검증했습니다."),
        )

    parts.append(
        _svg_text(
            "인공지능 연결 인사이트 · 가치평가 계산 비관여",
            x=70,
            y=295,
            size=24,
            weight=800,
            fill="#167C72",
        )
    )
    y = 345
    for label, detail in insight_rows:
        parts.append(_svg_text(label, x=70, y=y, size=24, weight=800, fill="#E26643"))
        rendered, y_end = _wrapped_text(
            detail,
            x=230,
            y=y,
            width=51,
            size=23,
            line_height=35,
            max_lines=3,
        )
        parts.append(rendered)
        y = max(y + 70, y_end + 20)

    y = max(y + 5, 650)
    parts.extend(
        (
            _svg_text(
                "평가 완료 사업부 소계" if partial else "결정론적 가치평가 결과",
                x=70,
                y=y,
                size=32,
                weight=800,
                fill="#102D3E",
            ),
            _svg_text("확률가중 전 개별 시나리오", x=1090, y=y, size=20, fill="#607582", anchor="end"),
        )
    )
    y += 35
    scenario_count = len(valuation.scenarios)
    box_width = min(320, (1060 - (scenario_count - 1) * 18) // max(scenario_count, 1))
    for index, scenario in enumerate(valuation.scenarios):
        x = 70 + index * (box_width + 18)
        parts.extend(
            (
                _rect(x, y, box_width, 150, fill="#FFFFFF", radius=20),
                _svg_text(
                    f"{_scenario_label(scenario.scenario_id)} ({scenario.scenario_id})",
                    x=x + 24,
                    y=y + 48,
                    size=22,
                    weight=700,
                    fill="#607582",
                ),
                _svg_text(
                    _price_text(scenario.value_per_share, valuation.reporting_unit),
                    x=x + 24,
                    y=y + 108,
                    size=34,
                    weight=800,
                    fill="#102D3E",
                ),
            )
        )

    market = data.get("market_observation")
    current_price = getattr(market, "price", None)
    market_as_of = getattr(market, "as_of", "")
    y += 200
    parts.extend(
        (
            _rect(70, y, 1060, 170, fill="#DDEAE7", radius=24),
            _svg_text("현재가", x=105, y=y + 52, size=24, weight=800, fill="#167C72"),
            _svg_text(
                _price_text(current_price, valuation.reporting_unit) if current_price is not None else "미확보",
                x=105,
                y=y + 112,
                size=38,
                weight=800,
                fill="#102D3E",
            ),
            _svg_text(
                f"기준일 {market_as_of or '미확보'}",
                x=1095,
                y=y + 112,
                size=21,
                fill="#607582",
                anchor="end",
            ),
        )
    )

    scenario_set = data.get("bound_scenario_set")
    weighted = bool(getattr(scenario_set, "numeric_weighting_allowed", False))
    expected = valuation.expected_value_per_share
    if weighted and expected is not None:
        entry_text = (
            f"확률가중 기대값은 {_price_text(expected, valuation.reporting_unit)}입니다. "
            "다만 별도 매수 규칙이 등록되지 않아 특정 매수가는 제시하지 않습니다."
        )
    else:
        entry_text = (
            "실제 해결 이력 기반 확률 보정이 완료되지 않았습니다. "
            "따라서 특정 매수가는 만들지 않고 현재가는 참고값으로만 표시합니다."
        )
    y += 220
    parts.extend(
        (
            _svg_text("매수 검토 기준", x=70, y=y, size=30, weight=800, fill="#102D3E"),
            _rect(70, y + 28, 1060, 145, fill="#FFF4DE", radius=22),
        )
    )
    rendered, _ = _wrapped_text(
        entry_text,
        x=105,
        y=y + 78,
        width=55,
        size=24,
        line_height=38,
        max_lines=3,
        weight=600,
        fill="#6D4B1F",
    )
    parts.append(rendered)
    parts.extend(
        (
            _rect(0, 1370, _CARD_WIDTH, 130, fill="#102D3E", radius=0),
            _source_footer(data, y=1415),
        )
    )
    return ReportVisual(
        filename=filename,
        alt_text=f"{company} 회사 강점·투자 결론·가치평가 요약",
        svg=_svg_document(
            title=f"{company} 투자 결론과 가치평가",
            description="회사 강점, 시나리오 가치, 현재가 및 매수 검토 기준을 요약한 한국어 카드",
            body="\n".join(parts),
        ),
    )


def _measure_text(assumption: Any) -> str:
    measure = assumption.measure
    amount = Decimal(str(measure.amount))
    return _measure_value_text(amount, measure.unit)


def _measure_value_text(amount: Decimal, unit: str) -> str:
    if unit == "ratio":
        return f"{amount * 100:.1f}%"
    if unit == "multiple":
        return f"{amount:g}배"
    if unit == "KRW_billion":
        return f"{amount * 10:,.0f}억원"
    if unit == "years":
        return f"{amount:g}년"
    if unit == "shares":
        return f"{amount:,.0f}주"
    return f"{amount:g} {unit}"


def _compact_adjustment_text(amount: Decimal, unit: str) -> str:
    """Fit a bridge amount in the narrow final column of the SVG table."""
    if unit == "KRW_billion":
        return f"{amount * 10:,.0f}억"
    return _measure_value_text(amount, unit)


def _scenario_assumption(scenario: Any, key: str) -> str:
    try:
        return _measure_text(scenario.get(key))
    except KeyError:
        return "—"


def _scenario_total_fcff(scenario: Any, year: int) -> str:
    try:
        base = scenario.get(f"fcff_year_{year}")
    except KeyError:
        return "—"
    try:
        incremental = scenario.get(f"uhv_fcff_year_{year}")
    except KeyError:
        return _measure_text(base)
    base_measure = base.measure
    incremental_measure = incremental.measure.convert_to(base_measure.unit)
    return _measure_value_text(
        base_measure.amount + incremental_measure.amount,
        base_measure.unit,
    )


def _multiple_assumption_table(
    scenarios: tuple[Any, ...],
    *,
    evaluator_contracts: tuple[SegmentEvaluatorContract, ...] = (),
    valuation_plan: CompanyValuationPlan | None = None,
) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]] | None:
    if not scenarios:
        return None
    first_keys = tuple(item.key for item in scenarios[0].assumptions)
    multiple_families = {
        "normalized_multiple",
        "normalized_ebitda_multiple",
        "ffo_multiple",
    }
    nav_families = {"net_asset_value"}
    multiple_contracts = tuple(
        item
        for item in evaluator_contracts
        if item.execution_family in multiple_families
    )
    nav_contracts = tuple(
        item
        for item in evaluator_contracts
        if item.execution_family in nav_families
    )
    discounted_contracts = tuple(
        item
        for item in evaluator_contracts
        if item.execution_family not in multiple_families | nav_families
    )
    if (
        evaluator_contracts
        and not multiple_contracts
        and not nav_contracts
        and len(discounted_contracts) == 1
    ):
        # Preserve the established dedicated DCF card for a single-family run.
        # The generalized table is needed when SOTP mixes evaluator families.
        return None

    # Legacy direct-plan callers do not carry a compilation artifact.  Their
    # historical table remains available, but every compiled runtime report
    # below is driven by exact selected evaluator contracts.
    ebitda_keys = (
        tuple(
            item.required_assumption_keys[0]
            for item in multiple_contracts
            if len(item.required_assumption_keys) >= 2
        )
        if evaluator_contracts
        else tuple(
            key for key in first_keys if key.endswith("normalized_ebitda")
        )
    )
    multiple_keys = (
        tuple(
            item.required_assumption_keys[1]
            for item in multiple_contracts
            if len(item.required_assumption_keys) >= 2
        )
        if evaluator_contracts
        else tuple(
            key
            for key in first_keys
            if key.endswith(
                ("normalized_multiple", "normalized_ebitda_multiple")
            )
        )
    )
    if not ebitda_keys or not multiple_keys:
        if not evaluator_contracts:
            return None
    nav_asset_keys = (
        tuple(
            item.required_assumption_keys[0]
            for item in nav_contracts
            if len(item.required_assumption_keys) >= 2
        )
        if evaluator_contracts
        else tuple(
            key for key in first_keys if key.endswith("gross_asset_value")
        )
    )

    def segment_label(segment: str) -> str:
        return {
            "manufacturing": "제조",
            "trading": "수출입",
            "recycling": "기타",
            "transport": "운송",
            "core": "핵심",
            "uhv": "초고압",
        }.get(segment, segment or "핵심")

    def segment_from_key(key: str, suffix: str) -> str:
        return key.removesuffix(suffix).rstrip("_")

    def adjustment_text(scenario: Any) -> str:
        adjustment_keys = (
            tuple(
                item.ev_to_equity_adjustment_key
                for item in valuation_plan.segments
                if item.ev_to_equity_adjustment_key is not None
            )
            if valuation_plan is not None
            else tuple(
                item.key
                for item in scenario.assumptions
                if item.key.endswith("ev_adjustment")
            )
        )
        adjustments = []
        for key in adjustment_keys:
            try:
                adjustments.append(scenario.get(key).measure)
            except KeyError:
                continue
        parent = []
        for item in valuation_plan.parent_adjustments if valuation_plan else ():
            try:
                parent.append(scenario.get(item.assumption_key).measure)
            except KeyError:
                continue
        details: list[str] = []
        if adjustments:
            unit = adjustments[0].unit
            total = sum(
                (item.convert_to(unit).amount for item in adjustments), Decimal(0)
            )
            details.append(
                f"EV {_compact_adjustment_text(total, unit)}"
                if parent
                else _measure_value_text(total, unit)
            )
        if parent:
            unit = parent[0].unit
            total = sum(
                (item.convert_to(unit).amount for item in parent), Decimal(0)
            )
            details.append(f"모 {_compact_adjustment_text(total, unit)}")
        return "\n".join(details) or "—"

    def multiple_values(scenario: Any) -> list[str]:
        values: list[str] = []
        if evaluator_contracts:
            for contract in multiple_contracts:
                if len(contract.required_assumption_keys) < 2:
                    continue
                value_key, multiple_key = contract.required_assumption_keys[:2]
                values.append(
                    f"{segment_label(contract.segment_id)} "
                    f"{_scenario_assumption(scenario, value_key)}"
                    f"×{_scenario_assumption(scenario, multiple_key)}"
                )
            return values
        for key in ebitda_keys:
            prefix = key.removesuffix("normalized_ebitda")
            multiple_key = next(
                (item for item in multiple_keys if item.startswith(prefix)),
                None,
            )
            if multiple_key is None:
                continue
            values.append(
                f"{segment_label(segment_from_key(key, 'normalized_ebitda'))} "
                f"{_scenario_assumption(scenario, key)}"
                f"×{_scenario_assumption(scenario, multiple_key)}"
            )
        return values

    def nav_values(scenario: Any) -> list[str]:
        values: list[str] = []
        if evaluator_contracts:
            contracts_and_keys = tuple(
                (item, item.required_assumption_keys[:2])
                for item in nav_contracts
                if len(item.required_assumption_keys) >= 2
            )
        else:
            contracts_and_keys = tuple(
                (
                    None,
                    (
                        key,
                        f"{key.removesuffix('gross_asset_value')}liabilities",
                    ),
                )
                for key in nav_asset_keys
            )
        for contract, (asset_key, liability_key) in contracts_and_keys:
            try:
                asset = scenario.get(asset_key).measure
                liability = scenario.get(liability_key).measure.convert_to(
                    asset.unit
                )
            except KeyError:
                continue
            label = (
                segment_label(contract.segment_id)
                if contract is not None
                else segment_label(
                    segment_from_key(asset_key, "gross_asset_value")
                )
            )
            values.append(
                f"{label} "
                f"{_measure_value_text(asset.amount - liability.amount, asset.unit)}"
            )
        return values

    def annual_keys(
        contract: SegmentEvaluatorContract,
        token: str,
    ) -> tuple[str, ...]:
        matches = tuple(
            key
            for key in contract.required_assumption_keys
            if token in key and re.search(r"_year_\d+$", key)
        )
        return tuple(
            sorted(
                matches,
                key=lambda key: int(key.rsplit("_year_", 1)[1]),
            )
        )

    def annual_total(
        scenario: Any,
        contract: SegmentEvaluatorContract,
        token: str,
        year: int,
    ) -> str:
        keys = tuple(
            key
            for key in contract.required_assumption_keys
            if token in key and key.endswith(f"_year_{year}")
        )
        measures = []
        for key in keys:
            try:
                measures.append(scenario.get(key).measure)
            except KeyError:
                continue
        if not measures:
            return "—"
        unit = measures[0].unit
        total = sum(
            (measure.convert_to(unit).amount for measure in measures),
            Decimal(0),
        )
        return _measure_value_text(total, unit)

    def discounted_values(scenario: Any) -> tuple[list[str], list[str]]:
        paths: list[str] = []
        terminal: list[str] = []
        for contract in discounted_contracts:
            label = segment_label(contract.segment_id)
            family = contract.execution_family
            if family == "contracted_backlog_dcf":
                backlog_key = next(
                    (
                        key
                        for key in contract.required_assumption_keys
                        if key.endswith("opening_backlog")
                    ),
                    None,
                )
                burns = annual_keys(contract, "backlog_burn_rate")
                orders = annual_keys(contract, "new_orders")
                detail = f"{label} 수주잔고 DCF"
                if backlog_key is not None:
                    detail += f" 잔고 {_scenario_assumption(scenario, backlog_key)}"
                if burns:
                    detail += (
                        f"; 소진 {_scenario_assumption(scenario, burns[0])}"
                        f"→{_scenario_assumption(scenario, burns[-1])}"
                    )
                if orders:
                    detail += (
                        f"; 신규수주 {_scenario_assumption(scenario, orders[0])}"
                        f"→{_scenario_assumption(scenario, orders[-1])}"
                    )
                paths.append(detail)
            else:
                cashflows = annual_keys(contract, "fcff")
                family_label = {
                    "explicit_fcff_dcf": "FCFF DCF",
                    "finite_life_npv": "유한수명 NPV",
                    "gordon_ddm": "배당할인",
                    "justified_pb_roe": "PBR·ROE",
                    "residual_income": "잔여이익",
                    "rate_base_roe": "요금기반 ROE",
                    "calibrated_single_event_rnpv": "확률조정 NPV",
                }.get(family, contract.model_key.method)
                detail = f"{label} {family_label}"
                if cashflows:
                    first_year = int(cashflows[0].rsplit("_year_", 1)[1])
                    last_year = int(cashflows[-1].rsplit("_year_", 1)[1])
                    detail += (
                        f" {annual_total(scenario, contract, 'fcff', first_year)}"
                        f"→{annual_total(scenario, contract, 'fcff', last_year)}"
                    )
                paths.append(detail)

            growth_key = next(
                (
                    key
                    for key in contract.required_assumption_keys
                    if key.endswith("terminal_growth")
                ),
                None,
            )
            roic_key = next(
                (
                    key
                    for key in contract.required_assumption_keys
                    if key.endswith("terminal_roic")
                ),
                None,
            )
            terminal_detail = f"{label} {family}"
            if growth_key is not None:
                terminal_detail = (
                    f"{label} g {_scenario_assumption(scenario, growth_key)}"
                )
            if roic_key is not None:
                terminal_detail += (
                    f"/ROIC {_scenario_assumption(scenario, roic_key)}"
                )
            terminal.append(terminal_detail)
        return paths, terminal

    adjustment_header = (
        "EV→지분\n모회사 조정"
        if valuation_plan is not None and valuation_plan.parent_adjustments
        else "EV→지분 조정"
    )

    if discounted_contracts or (
        not evaluator_contracts
        and any(key.endswith("fcff_year_1") for key in first_keys)
    ):
        headers = (
            "구분",
            "배수평가 부문",
            "NAV 부문",
            "현금흐름/NPV 부문",
            "영구/기타 입력",
            adjustment_header,
        )
        rows = []
        for scenario in scenarios[:3]:
            row_multiples = multiple_values(scenario)
            row_nav = nav_values(scenario)
            if evaluator_contracts:
                dcf_values, terminal_values = discounted_values(scenario)
            else:
                prefixes = tuple(
                    dict.fromkeys(
                        key.removesuffix("fcff_year_1")
                        for key in first_keys
                        if key.endswith("fcff_year_1")
                    )
                )
                dcf_values = [
                    f"{segment_label(prefix.rstrip('_'))} "
                    f"{_scenario_assumption(scenario, f'{prefix}fcff_year_1')}"
                    f"→{_scenario_assumption(scenario, f'{prefix}fcff_year_5')}"
                    for prefix in prefixes
                ]
                terminal_values = [
                    f"{segment_label(prefix.rstrip('_'))} g "
                    f"{_scenario_assumption(scenario, f'{prefix}terminal_growth')}"
                    f"/ROIC {_scenario_assumption(scenario, f'{prefix}terminal_roic')}"
                    for prefix in prefixes
                ]

            rows.append(
                (
                    f"{_scenario_label(scenario.scenario_id)}({scenario.scenario_id})",
                    "; ".join(row_multiples) or "—",
                    "; ".join(row_nav) or "—",
                    "; ".join(dcf_values) or "—",
                    "; ".join(terminal_values) or "—",
                    adjustment_text(scenario),
                )
            )
        return headers, tuple(rows)

    headers = (
        "구분",
        "배수평가 1",
        "배수평가 2",
        "배수평가 3+",
        "NAV 부문",
        adjustment_header,
    )

    rows = []
    for scenario in scenarios[:3]:
        row_multiples = multiple_values(scenario)
        row_nav = nav_values(scenario)
        rows.append(
            (
                f"{_scenario_label(scenario.scenario_id)}({scenario.scenario_id})",
                row_multiples[0] if row_multiples else "—",
                row_multiples[1] if len(row_multiples) > 1 else "—",
                "; ".join(row_multiples[2:]) or "—",
                "; ".join(row_nav) or "—",
                adjustment_text(scenario),
            )
        )
    return headers, tuple(rows)


def _assumptions_card(data: dict[str, Any], filename: str) -> ReportVisual:
    company = str(data.get("company") or data.get("target_id") or "분석 대상")
    scenario_set = data.get("bound_scenario_set")
    scenarios = tuple(getattr(scenario_set, "scenarios", ()))
    beta_result = data.get("live_beta_result")
    wacc_result = data.get("live_wacc_result")
    beta = getattr(beta_result, "target_levered_beta", None)
    wacc = getattr(getattr(wacc_result, "wacc_result", None), "wacc", None)
    calibration = getattr(getattr(scenario_set, "calibration_status", None), "value", "UNCALIBRATED")
    compilation = data.get("valuation_plan_compilation")
    if not isinstance(compilation, ValuationPlanCompilation):
        compilation = None
    valuation_plan = compilation.plan if compilation is not None else None
    evaluator_contracts = (
        compilation.evaluator_contracts if compilation is not None else ()
    )
    multiple_table = _multiple_assumption_table(
        scenarios,
        evaluator_contracts=evaluator_contracts,
        valuation_plan=valuation_plan,
    )
    core = next(
        (item for item in scenarios if item.scenario_id in {"Core", "Base"}),
        scenarios[0] if scenarios else None,
    )

    common_ownership: Decimal | None = None
    core_adjustment = "—"
    parent_adjustment_text = "—"
    core_shares = "—"
    formula = ""
    discounted_families = {
        "contracted_backlog_dcf",
        "explicit_fcff_dcf",
        "finite_life_npv",
        "calibrated_single_event_rnpv",
        "gordon_ddm",
        "justified_pb_roe",
        "residual_income",
        "rate_base_roe",
    }
    dcf_present = (
        any(
            item.execution_family in discounted_families
            for item in evaluator_contracts
        )
        if evaluator_contracts
        else bool(
            core is not None
            and any(
                item.key.endswith("fcff_year_1")
                for item in core.assumptions
            )
        )
    )
    component_sotp_present = bool(
        multiple_table is not None
        and (
            not evaluator_contracts
            or any(
                item.execution_family
                in {
                    "normalized_multiple",
                    "normalized_ebitda_multiple",
                    "ffo_multiple",
                    "net_asset_value",
                }
                for item in evaluator_contracts
            )
        )
    )
    if core is not None and valuation_plan is not None and valuation_plan.parent_adjustments:
        parent = tuple(
            core.get(item.assumption_key).measure
            for item in valuation_plan.parent_adjustments
        )
        unit = parent[0].unit
        parent_total = sum(
            (item.convert_to(unit).amount for item in parent), Decimal(0)
        )
        parent_adjustment_text = _measure_value_text(parent_total, unit)
    if multiple_table is not None and core is not None:
        ownership_keys = (
            tuple(item.ownership_key for item in valuation_plan.segments)
            if valuation_plan is not None
            else tuple(
                item.key
                for item in core.assumptions
                if item.key.endswith("ownership")
            )
        )
        ownership_values = tuple(
            core.get(key).measure.convert_to("ratio").amount
            for key in ownership_keys
        )
        if ownership_values and len(set(ownership_values)) == 1:
            common_ownership = ownership_values[0]
        adjustment_keys = (
            tuple(
                item.ev_to_equity_adjustment_key
                for item in valuation_plan.segments
                if item.ev_to_equity_adjustment_key is not None
            )
            if valuation_plan is not None
            else tuple(
                item.key
                for item in core.assumptions
                if item.key.endswith("ev_adjustment")
            )
        )
        adjustments = tuple(core.get(key).measure for key in adjustment_keys)
        if adjustments:
            unit = adjustments[0].unit
            total = sum(
                (item.convert_to(unit).amount for item in adjustments), Decimal(0)
            )
            core_adjustment = _measure_value_text(total, unit)
        share_key = (
            valuation_plan.diluted_shares_key
            if valuation_plan is not None
            else "diluted_shares"
        )
        try:
            shares = core.get(share_key).measure.convert_to("shares").amount
        except KeyError:
            shares = None
        if shares is not None:
            core_shares = f"{shares / Decimal('1000000'):,.3f}백만주"
        if evaluator_contracts and shares is not None:
            value_terms = tuple(
                dict.fromkeys(
                    valuation_family_value_term_ko(
                        item.execution_family,
                        item.model_key.method,
                    )
                    for item in evaluator_contracts
                )
            )
            if valuation_plan is not None and valuation_plan.parent_adjustments:
                value_terms = (*value_terms, "모회사 조정")
            formula = (
                f"[{'+'.join(value_terms)}]÷{shares:,.0f}주 "
                "(각 부문 EV→지분 조정·귀속률 반영)"
            )
        elif common_ownership is not None and shares is not None:
            nav_present = any(
                item.key.endswith("gross_asset_value")
                for item in core.assumptions
            )
            value_terms = []
            if dcf_present:
                value_terms.append("DCF 가치")
            value_terms.append("부문 EBITDA×배수 합")
            if nav_present:
                value_terms.append("유형자산 NAV")
            if adjustments:
                value_terms.append("EV→지분 조정")
            formula = (
                f"[{'+'.join(value_terms)}]"
                f"×{common_ownership * 100:.4f}%÷{shares:,.0f}주"
            )

    parts = [
        _rect(0, 0, _CARD_WIDTH, _CARD_HEIGHT, fill="#F3F0E8", radius=0),
        _rect(0, 0, _CARD_WIDTH, 250, fill="#102D3E", radius=0),
        _svg_text("PRISM 최종보고서 · 2/2", x=70, y=70, size=24, weight=700, fill="#FFCB77"),
        _svg_text(company, x=70, y=145, size=54, weight=800, fill="#FFFFFF"),
        _svg_text("가치평가 가정 · 위험 · 출처", x=70, y=202, size=30, weight=500, fill="#D9E7EC"),
        _svg_text("핵심 위험 입력", x=70, y=315, size=30, weight=800, fill="#102D3E"),
    ]
    metric_rows = (
        (
            ("DCF 가중평균자본비용", f"{wacc:.2%}" if wacc is not None else "미확보"),
            (
                "지배주주 귀속률",
                f"{common_ownership * 100:.4f}%"
                if common_ownership is not None
                else "개별 적용",
            ),
            ("주당 분모", core_shares),
        )
        if component_sotp_present and dcf_present
        else
        (
            (
                "지배주주 귀속률",
                f"{common_ownership * 100:.4f}%"
                if common_ownership is not None
                else "개별 적용",
            ),
            (
                "모회사 조정"
                if valuation_plan is not None and valuation_plan.parent_adjustments
                else "EV→지분 조정",
                parent_adjustment_text
                if valuation_plan is not None and valuation_plan.parent_adjustments
                else core_adjustment,
            ),
            ("주당 분모", core_shares),
        )
        if component_sotp_present
        else (
            ("계층형 베타", f"{beta:.3f}" if beta is not None else "비적용"),
            ("가중평균자본비용", f"{wacc:.2%}" if wacc is not None else "비적용"),
            (
                "모회사 조정"
                if valuation_plan is not None and valuation_plan.parent_adjustments
                else "확률 보정",
                parent_adjustment_text
                if valuation_plan is not None and valuation_plan.parent_adjustments
                else ("완료" if calibration == "CALIBRATED" else "미완료"),
            ),
        )
    )
    for index, (label, value) in enumerate(metric_rows):
        x = 70 + index * 355
        parts.extend(
            (
                _rect(x, 350, 330, 135, fill="#FFFFFF", radius=20),
                _svg_text(label, x=x + 24, y=397, size=21, weight=700, fill="#607582"),
                _svg_text(value, x=x + 24, y=455, size=34, weight=800, fill="#102D3E"),
            )
        )

    parts.append(_svg_text("시나리오별 핵심 가정", x=70, y=560, size=30, weight=800, fill="#102D3E"))
    table_y = 600
    parts.append(_rect(70, table_y, 1060, 390, fill="#FFFFFF", radius=22))
    headers = (
        multiple_table[0]
        if multiple_table is not None
        else ("구분", "1년 DCF FCFF", "5년 DCF FCFF", "영구성장률", "영구 ROIC", "UHV 5년 증분")
    )
    x_positions = (100, 270, 470, 675, 855, 1015)
    for x, header in zip(x_positions, headers):
        header_lines = str(header).splitlines()
        header_y = table_y + (37 if len(header_lines) > 1 else 48)
        for line_index, line in enumerate(header_lines):
            parts.append(
                _svg_text(
                    line,
                    x=x,
                    y=header_y + line_index * 21,
                    size=16 if len(header_lines) > 1 else 19,
                    weight=800,
                    fill="#607582",
                )
            )
    parts.append(f'<line x1="95" y1="{table_y + 68}" x2="1105" y2="{table_y + 68}" stroke="#D6DFE3" stroke-width="2"/>')
    table_rows = (
        multiple_table[1]
        if multiple_table is not None
        else tuple(
            (
                f"{_scenario_label(scenario.scenario_id)}({scenario.scenario_id})",
                _scenario_total_fcff(scenario, 1),
                _scenario_total_fcff(scenario, 5),
                _scenario_assumption(scenario, "terminal_growth"),
                _scenario_assumption(scenario, "terminal_roic"),
                _scenario_assumption(scenario, "uhv_fcff_year_5"),
            )
            for scenario in scenarios[:3]
        )
    )
    for index, values in enumerate(table_rows):
        row_y = table_y + 125 + index * 85
        for x, value in zip(x_positions, values):
            value_lines = str(value).splitlines()
            value_y = row_y - (11 if len(value_lines) > 1 else 0)
            for line_index, line in enumerate(value_lines):
                parts.append(
                    _svg_text(
                        line,
                        x=x,
                        y=value_y + line_index * 24,
                        size=16 if len(value_lines) > 1 else 21,
                        weight=700 if x == 100 else 500,
                        fill="#142A3A",
                    )
                )
        if index < len(table_rows) - 1:
            parts.append(f'<line x1="95" y1="{row_y + 28}" x2="1105" y2="{row_y + 28}" stroke="#E9EEF0" stroke-width="2"/>')

    capex = "—"
    if core is not None:
        expansion = _scenario_assumption(core, "expansion_capex")
        uhv = _scenario_assumption(core, "uhv_property_capex")
        capex = f"기존 증설 {expansion} + 초고압 부동산 {uhv}"
    methods = tuple(data.get("selected_methods", ()))
    method_text = ", ".join(methods) if methods else "결정론적 등록 평가기"
    capacity = data.get("capacity_commitment_assessment")
    projects = tuple(getattr(capacity, "core_inclusion_required_projects", ()))
    project_text = ", ".join(projects) if projects else "별도 핵심 생산능력 프로젝트 없음"
    detail_rows = (
        (
            ("평가방법", method_text),
            ("정확한 계산식", formula or "부문별 가치·귀속률·주식수로 결정론적 재계산"),
            ("확률 보정", "완료" if calibration == "CALIBRATED" else "미완료"),
            ("매수구간", "확률 보정 및 별도 진입 규칙 미충족 시 자동 산출 금지"),
        )
        if component_sotp_present
        else (
            ("평가방법", method_text),
            ("핵심 자본적지출", capex),
            ("생산능력 반영", project_text),
            ("매수구간", "확률 보정 및 별도 진입 규칙 미충족 시 자동 산출 금지"),
        )
    )
    y = 1050
    for label, detail in detail_rows:
        parts.append(_svg_text(label, x=70, y=y, size=22, weight=800, fill="#E26643"))
        rendered, y_end = _wrapped_text(
            detail,
            x=250,
            y=y,
            width=58,
            size=21,
            line_height=31,
            max_lines=2,
        )
        parts.append(rendered)
        y = max(y + 62, y_end + 12)

    parts.extend(
        (
            _rect(0, 1370, _CARD_WIDTH, 130, fill="#102D3E", radius=0),
            _source_footer(data, y=1415),
        )
    )
    return ReportVisual(
        filename=filename,
        alt_text=f"{company} 가치평가 가정·위험·출처 요약",
        svg=_svg_document(
            title=f"{company} 가치평가 가정",
            description="시나리오별 자유현금흐름, 영구성장률, 자본비용, 위험과 출처를 요약한 한국어 카드",
            body="\n".join(parts),
        ),
    )


def render_report_visuals(data: dict[str, Any]) -> tuple[ReportVisual, ReportVisual]:
    summary_filename, assumptions_filename = report_visual_filenames(data)
    return (
        _summary_card(data, summary_filename),
        _assumptions_card(data, assumptions_filename),
    )
