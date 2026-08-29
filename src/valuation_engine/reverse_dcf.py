"""Post-freeze reverse DCF / market-implied expectations.

Doctrine (``references/methods/reverse-dcf.md``): reverse DCF is a market-comparison
tool. It runs only after ``INTRINSIC_VALUE_FREEZE``, it holds the frozen model
structure constant, it solves one clearly identified variable at a time, and its
result can never mutate the same frozen intrinsic run.

Two solves are provided, both closed-form and deterministic:

``implied_terminal_growth``
    Holding the frozen explicit FCFF path and WACC constant, the perpetual growth
    rate the observed market value would require. This is the single-variable solve
    the doctrine asks for; the explicit forecast is *not* re-fitted.

``implied_fcff_scale``
    Holding growth and WACC constant, the uniform multiple the whole frozen FCFF
    stream would require. Enterprise value is linear in the FCFF path under a fixed
    discount rate and terminal growth, so this is exact rather than iterative.

Both consume the decomposition published by the evaluator that actually performed
the discounting (``SegmentValuationDiagnostics``). This module never re-infers a
DCF kernel from compiled assumption keys: a scenario whose value did not come from
a single reconstructible DCF contribution is reported as NOT_RECONSTRUCTIBLE
instead of being answered with an approximation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json

from .actual_units import Measure
from .evaluator_registry import SegmentValuationDiagnostics
from .records import AuditFinding
from .sotp import CompanyScenarioEquityValue
from .valuation_execution import GenericValuationResult


_ONE = Decimal("1")


class ReverseDCFError(ValueError):
    """Raised when reverse-DCF inputs violate their contract."""


@dataclass(frozen=True)
class ReverseDCFPolicy:
    """Reporting thresholds for market-implied expectations.

    These thresholds never block. A frozen intrinsic result is final; reverse DCF
    only tells the reader which requirements the market is carrying that the frozen
    model is not.
    """

    max_terminal_value_share: Decimal = Decimal("0.75")
    max_implied_growth_gap: Decimal = Decimal("0.01")
    max_implied_fcff_scale_gap: Decimal = Decimal("0.25")
    max_implied_reinvestment_rate: Decimal = Decimal("1")

    def validate(self) -> None:
        if not Decimal("0") < self.max_terminal_value_share <= _ONE:
            raise ReverseDCFError("max_terminal_value_share must be within (0, 1]")
        if self.max_implied_growth_gap <= 0:
            raise ReverseDCFError("max_implied_growth_gap must be positive")
        if self.max_implied_fcff_scale_gap <= 0:
            raise ReverseDCFError("max_implied_fcff_scale_gap must be positive")
        if self.max_implied_reinvestment_rate <= 0:
            raise ReverseDCFError("max_implied_reinvestment_rate must be positive")


class ReconstructionStatus(str):
    RECONSTRUCTED = "RECONSTRUCTED"
    NOT_RECONSTRUCTIBLE = "NOT_RECONSTRUCTIBLE"


@dataclass(frozen=True)
class ScenarioReverseDCF:
    """Market-implied requirement for one frozen scenario."""

    scenario_id: str
    reporting_unit: str
    model_equity_value: Decimal
    market_equity_value: Decimal
    status: str
    rationale: str
    ownership_ratio: Decimal | None = None
    model_enterprise_value: Decimal | None = None
    market_enterprise_value: Decimal | None = None
    present_value_explicit: Decimal | None = None
    present_value_terminal: Decimal | None = None
    terminal_value_share: Decimal | None = None
    discount_rate: Decimal | None = None
    model_terminal_growth: Decimal | None = None
    model_terminal_roic: Decimal | None = None
    implied_terminal_growth: Decimal | None = None
    implied_terminal_growth_gap: Decimal | None = None
    implied_terminal_reinvestment_rate: Decimal | None = None
    implied_fcff_scale: Decimal | None = None

    @property
    def reconstructed(self) -> bool:
        return self.status == ReconstructionStatus.RECONSTRUCTED


@dataclass(frozen=True)
class ScenarioProbabilityRequirement:
    """Which scenario mix the observed price is currently sitting on.

    This is the doctrine's "scenario probability" solve. It is a two-point
    requirement, not a calibrated probability, and it never feeds back into
    intrinsic weighting.
    """

    position: str
    market_value_per_share: Decimal
    lower_scenario_id: str | None = None
    upper_scenario_id: str | None = None
    lower_value_per_share: Decimal | None = None
    upper_value_per_share: Decimal | None = None
    lower_weight: Decimal | None = None
    upper_weight: Decimal | None = None

    def validate(self) -> None:
        if self.position not in {"INSIDE", "BELOW_ALL", "ABOVE_ALL"}:
            raise ReverseDCFError("unknown scenario position")
        if self.position != "INSIDE":
            return
        if (
            self.lower_scenario_id is None
            or self.upper_scenario_id is None
            or self.lower_weight is None
            or self.upper_weight is None
        ):
            raise ReverseDCFError("bracketed requirement is incomplete")
        total = self.lower_weight + self.upper_weight
        if abs(total - _ONE) > Decimal("1e-12"):
            raise ReverseDCFError("two-point scenario weights must sum to one")


@dataclass(frozen=True)
class ReverseDCFResult:
    market_price: Decimal
    market_as_of: str
    reporting_unit: str
    scenarios: tuple[ScenarioReverseDCF, ...]
    probability_requirement: ScenarioProbabilityRequirement
    findings: tuple[AuditFinding, ...]
    result_hash: str

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.findings)

    @property
    def warnings(self) -> tuple[AuditFinding, ...]:
        return tuple(item for item in self.findings if not item.passed)


def implied_terminal_growth(
    *,
    market_enterprise_value: Decimal,
    present_value_explicit: Decimal,
    terminal_fcff: Decimal,
    discount_rate: Decimal,
    forecast_years: int,
) -> Decimal | None:
    """Solve the perpetual growth rate the market value requires.

    ``EV = PV(explicit) + [f_N (1+g) / (w - g)] / (1+w)^N`` rearranges to a closed
    form in ``g``. Let ``A = (EV - PV(explicit)) (1+w)^N / f_N``; then
    ``g = (A w - 1) / (1 + A)``, and ``w - g = (1 + w) / (1 + A) > 0`` for ``A > 0``,
    so a solved growth rate can never reach or exceed the discount rate.

    Returns ``None`` when the market value leaves no positive terminal value to
    solve against — the requirement is then outside this single-variable model
    rather than an extreme number.
    """
    if forecast_years < 1:
        raise ReverseDCFError("forecast_years must be positive")
    if not discount_rate.is_finite() or discount_rate <= 0:
        raise ReverseDCFError("discount_rate must be finite and positive")
    if not terminal_fcff.is_finite() or terminal_fcff <= 0:
        raise ReverseDCFError("terminal FCFF must be finite and positive")
    required_terminal_pv = market_enterprise_value - present_value_explicit
    if required_terminal_pv <= 0:
        return None
    factor = (_ONE + discount_rate) ** forecast_years
    ratio = required_terminal_pv * factor / terminal_fcff
    if ratio <= 0:
        return None
    return (ratio * discount_rate - _ONE) / (_ONE + ratio)


def implied_fcff_scale(
    *,
    market_enterprise_value: Decimal,
    model_enterprise_value: Decimal,
) -> Decimal:
    """Uniform multiple the frozen FCFF stream would require.

    Enterprise value is linear in the FCFF path when the discount rate and terminal
    growth are held constant, so the requirement is an exact ratio.
    """
    if not model_enterprise_value.is_finite() or model_enterprise_value <= 0:
        raise ReverseDCFError("model enterprise value must be finite and positive")
    return market_enterprise_value / model_enterprise_value


def solve_scenario_probability_requirement(
    *,
    market_value_per_share: Decimal,
    scenario_values: tuple[tuple[str, Decimal], ...],
) -> ScenarioProbabilityRequirement:
    """Locate the observed price on the frozen scenario ladder."""
    if not scenario_values:
        raise ReverseDCFError("scenario ladder is empty")
    ordered = tuple(sorted(scenario_values, key=lambda item: item[1]))
    lowest_id, lowest = ordered[0]
    highest_id, highest = ordered[-1]
    if market_value_per_share < lowest:
        result = ScenarioProbabilityRequirement(
            position="BELOW_ALL",
            market_value_per_share=market_value_per_share,
            lower_scenario_id=lowest_id,
            lower_value_per_share=lowest,
        )
        result.validate()
        return result
    if market_value_per_share > highest:
        result = ScenarioProbabilityRequirement(
            position="ABOVE_ALL",
            market_value_per_share=market_value_per_share,
            upper_scenario_id=highest_id,
            upper_value_per_share=highest,
        )
        result.validate()
        return result

    for (lower_id, lower), (upper_id, upper) in zip(ordered, ordered[1:]):
        if lower <= market_value_per_share <= upper:
            span = upper - lower
            if span == 0:
                lower_weight = _ONE
            else:
                lower_weight = (upper - market_value_per_share) / span
            result = ScenarioProbabilityRequirement(
                position="INSIDE",
                market_value_per_share=market_value_per_share,
                lower_scenario_id=lower_id,
                upper_scenario_id=upper_id,
                lower_value_per_share=lower,
                upper_value_per_share=upper,
                lower_weight=lower_weight,
                upper_weight=_ONE - lower_weight,
            )
            result.validate()
            return result

    # A single-scenario ladder equal to the market price falls through the pairwise
    # scan above; treat it as an exact match on that scenario.
    result = ScenarioProbabilityRequirement(
        position="INSIDE",
        market_value_per_share=market_value_per_share,
        lower_scenario_id=lowest_id,
        upper_scenario_id=highest_id,
        lower_value_per_share=lowest,
        upper_value_per_share=highest,
        lower_weight=_ONE,
        upper_weight=Decimal("0"),
    )
    result.validate()
    return result


def _reconstructible_component(
    company_value: CompanyScenarioEquityValue,
) -> tuple[SegmentValuationDiagnostics, Decimal] | None:
    """Return the single DCF contribution a scenario can be reversed against.

    A scenario is reversible only when exactly one aggregation component carries a
    published DCF decomposition and no other component contributes value. Anything
    else (multi-segment SOTP, parent adjustments, non-DCF evaluators) would require
    choosing which component absorbs the market gap, which is a judgement the
    doctrine does not permit this tool to make.
    """
    if len(company_value.components) != 1:
        return None
    component = company_value.components[0]
    diagnostics = component.diagnostics
    ownership = component.ownership_ratio
    if diagnostics is None or ownership is None:
        return None
    if not isinstance(diagnostics, SegmentValuationDiagnostics):
        return None
    if not ownership.is_finite() or ownership <= 0:
        return None
    return diagnostics, ownership


def _scenario_reverse_dcf(
    *,
    scenario_id: str,
    reporting_unit: str,
    model_equity_value: Decimal,
    market_equity_value: Decimal,
    company_value: CompanyScenarioEquityValue,
) -> ScenarioReverseDCF:
    reconstructible = _reconstructible_component(company_value)
    if reconstructible is None:
        return ScenarioReverseDCF(
            scenario_id=scenario_id,
            reporting_unit=reporting_unit,
            model_equity_value=model_equity_value,
            market_equity_value=market_equity_value,
            status=ReconstructionStatus.NOT_RECONSTRUCTIBLE,
            rationale=(
                "시나리오 가치가 단일 DCF 기여분으로 구성되지 않아 시장 함의 "
                "영구성장률을 단일 변수로 역산할 수 없습니다"
            ),
        )
    diagnostics, ownership = reconstructible
    diagnostics.validate()

    as_of = ""
    def to_reporting(amount: Decimal) -> Decimal:
        return Measure(amount, diagnostics.value_unit, as_of or "1970-01-01").convert_to(
            reporting_unit
        ).amount

    model_enterprise_value = to_reporting(diagnostics.enterprise_value)
    present_value_explicit = to_reporting(diagnostics.present_value_explicit)
    present_value_terminal = to_reporting(diagnostics.present_value_terminal)
    terminal_fcff = to_reporting(diagnostics.terminal_fcff)
    if model_enterprise_value <= 0:
        return ScenarioReverseDCF(
            scenario_id=scenario_id,
            reporting_unit=reporting_unit,
            model_equity_value=model_equity_value,
            market_equity_value=market_equity_value,
            status=ReconstructionStatus.NOT_RECONSTRUCTIBLE,
            rationale="모델 기업가치가 양수가 아니어서 역산 기준이 성립하지 않습니다",
        )

    # equity = ownership x (EV + adjustment), so holding the frozen EV-to-equity
    # bridge and ownership constant, the market's enterprise-value requirement is
    # the model EV plus the equity gap grossed up for ownership.
    market_enterprise_value = model_enterprise_value + (
        market_equity_value - model_equity_value
    ) / ownership

    solved_growth = implied_terminal_growth(
        market_enterprise_value=market_enterprise_value,
        present_value_explicit=present_value_explicit,
        terminal_fcff=terminal_fcff,
        discount_rate=diagnostics.discount_rate,
        forecast_years=diagnostics.forecast_years,
    )
    reinvestment = (
        solved_growth / diagnostics.terminal_roic if solved_growth is not None else None
    )
    growth_gap = (
        solved_growth - diagnostics.terminal_growth if solved_growth is not None else None
    )
    return ScenarioReverseDCF(
        scenario_id=scenario_id,
        reporting_unit=reporting_unit,
        model_equity_value=model_equity_value,
        market_equity_value=market_equity_value,
        status=ReconstructionStatus.RECONSTRUCTED,
        rationale=(
            "동결된 명시 현금흐름 경로와 할인율을 고정한 채 영구성장률만 역산했습니다"
        ),
        ownership_ratio=ownership,
        model_enterprise_value=model_enterprise_value,
        market_enterprise_value=market_enterprise_value,
        present_value_explicit=present_value_explicit,
        present_value_terminal=present_value_terminal,
        terminal_value_share=present_value_terminal / model_enterprise_value,
        discount_rate=diagnostics.discount_rate,
        model_terminal_growth=diagnostics.terminal_growth,
        model_terminal_roic=diagnostics.terminal_roic,
        implied_terminal_growth=solved_growth,
        implied_terminal_growth_gap=growth_gap,
        implied_terminal_reinvestment_rate=reinvestment,
        implied_fcff_scale=implied_fcff_scale(
            market_enterprise_value=market_enterprise_value,
            model_enterprise_value=model_enterprise_value,
        ),
    )


def _findings(
    scenarios: tuple[ScenarioReverseDCF, ...],
    requirement: ScenarioProbabilityRequirement,
    policy: ReverseDCFPolicy,
) -> tuple[AuditFinding, ...]:
    findings: list[AuditFinding] = []

    reconstructed = tuple(item for item in scenarios if item.reconstructed)
    findings.append(
        AuditFinding(
            "reverse_dcf_reconstruction",
            bool(reconstructed),
            False,
            (
                f"{len(reconstructed)}/{len(scenarios)}개 시나리오에서 동결 모델을 "
                "역산 기준으로 재구성했습니다"
                if reconstructed
                else "역산 가능한 단일 DCF 시나리오가 없어 시장 함의값을 산출하지 않았습니다"
            ),
        )
    )

    heavy = tuple(
        item
        for item in reconstructed
        if item.terminal_value_share is not None
        and item.terminal_value_share > policy.max_terminal_value_share
    )
    findings.append(
        AuditFinding(
            "reverse_dcf_terminal_value_share",
            not heavy,
            False,
            (
                "영구가치 비중이 기준 이하입니다"
                if not heavy
                else "영구가치 비중이 기준("
                + f"{policy.max_terminal_value_share}"
                + ")을 초과합니다: "
                + ", ".join(
                    f"{item.scenario_id}={item.terminal_value_share:.3f}" for item in heavy
                )
            ),
        )
    )

    gapped = tuple(
        item
        for item in reconstructed
        if item.implied_terminal_growth_gap is not None
        and abs(item.implied_terminal_growth_gap) > policy.max_implied_growth_gap
    )
    findings.append(
        AuditFinding(
            "reverse_dcf_implied_growth_gap",
            not gapped,
            False,
            (
                "시장 함의 영구성장률이 동결 가정과 기준 이내로 일치합니다"
                if not gapped
                else "시장이 동결 가정과 다른 영구성장률을 요구합니다: "
                + ", ".join(
                    f"{item.scenario_id} 모델 {item.model_terminal_growth} → "
                    f"시장 {item.implied_terminal_growth}"
                    for item in gapped
                )
            ),
        )
    )

    infeasible = tuple(
        item
        for item in reconstructed
        if item.implied_terminal_reinvestment_rate is not None
        and item.implied_terminal_reinvestment_rate > policy.max_implied_reinvestment_rate
    )
    findings.append(
        AuditFinding(
            "reverse_dcf_implied_reinvestment_feasibility",
            not infeasible,
            False,
            (
                "시장 함의 영구성장률이 재투자 항등식 안에서 실현 가능합니다"
                if not infeasible
                else "시장 함의 성장률이 동결 ROIC 하에서 재투자율 100%를 초과합니다: "
                + ", ".join(
                    f"{item.scenario_id}={item.implied_terminal_reinvestment_rate:.3f}"
                    for item in infeasible
                )
            ),
        )
    )

    unsolved = tuple(
        item
        for item in reconstructed
        if item.implied_terminal_growth is None
    )
    findings.append(
        AuditFinding(
            "reverse_dcf_terminal_solution_exists",
            not unsolved,
            False,
            (
                "모든 재구성 시나리오에서 시장 함의 영구가치가 양수입니다"
                if not unsolved
                else "시장가가 명시 현금흐름 현재가치에도 못 미쳐 영구성장률 해가 없습니다: "
                + ", ".join(item.scenario_id for item in unsolved)
            ),
        )
    )

    scaled = tuple(
        item
        for item in reconstructed
        if item.implied_fcff_scale is not None
        and abs(item.implied_fcff_scale - _ONE) > policy.max_implied_fcff_scale_gap
    )
    findings.append(
        AuditFinding(
            "reverse_dcf_implied_fcff_scale",
            not scaled,
            False,
            (
                "시장가를 정당화하는 현금흐름 배율이 기준 이내입니다"
                if not scaled
                else "시장가를 정당화하려면 전 기간 현금흐름이 크게 달라져야 합니다: "
                + ", ".join(
                    f"{item.scenario_id}={item.implied_fcff_scale:.3f}배" for item in scaled
                )
            ),
        )
    )

    findings.append(
        AuditFinding(
            "reverse_dcf_scenario_position",
            requirement.position == "INSIDE",
            False,
            (
                f"현재가는 {requirement.lower_scenario_id}~{requirement.upper_scenario_id} "
                f"구간에 있으며 두 시나리오만으로 설명하면 "
                f"{requirement.lower_scenario_id} 비중 {requirement.lower_weight:.3f}입니다"
                if requirement.position == "INSIDE"
                else "현재가가 동결 시나리오 범위를 벗어났습니다: " + requirement.position
            ),
        )
    )
    return tuple(findings)


def _result_hash(
    *,
    valuation_hash: str,
    market_price: Decimal,
    market_as_of: str,
    scenarios: tuple[ScenarioReverseDCF, ...],
    requirement: ScenarioProbabilityRequirement,
    findings: tuple[AuditFinding, ...],
) -> str:
    payload = {
        "contract": "reverse_dcf_expectations/v1",
        "valuation_hash": valuation_hash,
        "market_price": str(market_price),
        "market_as_of": market_as_of,
        "scenarios": [
            {
                "scenario_id": item.scenario_id,
                "status": item.status,
                "market_enterprise_value": str(item.market_enterprise_value),
                "model_enterprise_value": str(item.model_enterprise_value),
                "terminal_value_share": str(item.terminal_value_share),
                "implied_terminal_growth": str(item.implied_terminal_growth),
                "implied_terminal_reinvestment_rate": str(
                    item.implied_terminal_reinvestment_rate
                ),
                "implied_fcff_scale": str(item.implied_fcff_scale),
            }
            for item in scenarios
        ],
        "requirement": {
            "position": requirement.position,
            "lower": requirement.lower_scenario_id,
            "upper": requirement.upper_scenario_id,
            "lower_weight": str(requirement.lower_weight),
        },
        "findings": [
            [item.check, item.passed, item.blocking, item.detail] for item in findings
        ],
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def build_reverse_dcf_result(
    *,
    valuation: GenericValuationResult,
    market_price: Decimal,
    market_as_of: str,
    market_currency: str,
    policy: ReverseDCFPolicy | None = None,
) -> ReverseDCFResult:
    """Derive market-implied requirements from a frozen valuation.

    The frozen model is read-only here. Nothing in this function can change the
    intrinsic result it is describing.
    """
    effective_policy = policy or ReverseDCFPolicy()
    effective_policy.validate()
    if market_currency != valuation.reporting_unit:
        raise ReverseDCFError(
            f"market currency {market_currency} does not match intrinsic reporting unit "
            f"{valuation.reporting_unit}"
        )
    if not market_price.is_finite() or market_price <= 0:
        raise ReverseDCFError("market price must be finite and positive")
    if not valuation.scenarios:
        raise ReverseDCFError("frozen valuation has no scenarios")

    by_scenario = {
        item.scenario_id: item for item in valuation.equity_aggregation.scenario_values
    }
    scenarios: list[ScenarioReverseDCF] = []
    for per_share in valuation.scenarios:
        company_value = by_scenario.get(per_share.scenario_id)
        if company_value is None:
            raise ReverseDCFError(
                f"frozen scenario {per_share.scenario_id} has no aggregation record"
            )
        scenarios.append(
            _scenario_reverse_dcf(
                scenario_id=per_share.scenario_id,
                reporting_unit=valuation.reporting_unit,
                model_equity_value=per_share.equity_value_amount,
                market_equity_value=market_price * per_share.diluted_shares,
                company_value=company_value,
            )
        )

    requirement = solve_scenario_probability_requirement(
        market_value_per_share=market_price,
        scenario_values=tuple(
            (item.scenario_id, item.value_per_share) for item in valuation.scenarios
        ),
    )
    scenario_tuple = tuple(scenarios)
    findings = _findings(scenario_tuple, requirement, effective_policy)
    return ReverseDCFResult(
        market_price=market_price,
        market_as_of=market_as_of,
        reporting_unit=valuation.reporting_unit,
        scenarios=scenario_tuple,
        probability_requirement=requirement,
        findings=findings,
        result_hash=_result_hash(
            valuation_hash=valuation.valuation_hash,
            market_price=market_price,
            market_as_of=market_as_of,
            scenarios=scenario_tuple,
            requirement=requirement,
            findings=findings,
        ),
    )
