"""Three-point value sensitivity on the frozen DCF kernel.

``decision_impact.assess_three_point_value_sensitivity`` already implemented the
comparison contract but nothing in the runtime called it, so a completed run
reported a single intrinsic value and a scenario envelope with no indication of
which single variable the value actually hangs on. When most of enterprise value
sits in the Gordon tail, that is the difference between a reader thinking the
forecast matters most and knowing the discount rate does.

Scope is deliberately limited to the variables the DCF kernel itself owns —
discount rate, terminal growth, and the level of the whole FCFF stream. Operating
drivers (utilisation, realisation, margin) live upstream in provider inputs and
cannot be perturbed without re-running collection, so they are out of scope here
rather than approximated.

Every perturbation reuses the evaluator's published decomposition and the same
discounting kernel, and the base case is recomputed and checked against the
published enterprise value before any perturbed number is reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json

from .actual_units import Measure
from .control_plane import StageStatus
from .decision_impact import assess_three_point_value_sensitivity
from .evaluator_registry import SegmentValuationDiagnostics
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .records import AuditFinding
from .reverse_dcf import ReconstructionStatus, reconstructible_dcf_component
from .sotp import CompanyScenarioEquityValue
from .valuation_execution import GenericValuationResult


_ZERO = Decimal("0")
_ONE = Decimal("1")
_RECONSTRUCTION_TOLERANCE = Decimal("1e-9")

DISCOUNT_RATE = "discount_rate"
TERMINAL_GROWTH = "terminal_growth"
FCFF_LEVEL = "fcff_level"

_VARIABLE_LABEL_KO = {
    DISCOUNT_RATE: "가중평균자본비용",
    TERMINAL_GROWTH: "영구성장률",
    FCFF_LEVEL: "전 기간 현금흐름 수준",
}


class ValuationSensitivityError(ValueError):
    """Raised when sensitivity inputs violate their contract."""


@dataclass(frozen=True)
class SensitivityPolicy:
    discount_rate_delta: Decimal = Decimal("0.005")
    terminal_growth_delta: Decimal = Decimal("0.005")
    fcff_level_delta: Decimal = Decimal("0.10")
    high_sensitivity_pct: Decimal = Decimal("0.15")

    def validate(self) -> None:
        for name, value in (
            ("discount_rate_delta", self.discount_rate_delta),
            ("terminal_growth_delta", self.terminal_growth_delta),
            ("fcff_level_delta", self.fcff_level_delta),
            ("high_sensitivity_pct", self.high_sensitivity_pct),
        ):
            if not value.is_finite() or value <= 0:
                raise ValuationSensitivityError(f"{name} must be finite and positive")
        if self.fcff_level_delta >= _ONE:
            raise ValuationSensitivityError("fcff_level_delta must stay below one")


@dataclass(frozen=True)
class VariableSensitivity:
    variable: str
    label: str
    low_input: Decimal
    base_input: Decimal
    high_input: Decimal
    low_value_per_share: Decimal
    base_value_per_share: Decimal
    high_value_per_share: Decimal
    low_value_pct: Decimal
    high_value_pct: Decimal
    monotonic: bool

    @property
    def max_abs_pct(self) -> Decimal:
        return max(abs(self.low_value_pct), abs(self.high_value_pct))


@dataclass(frozen=True)
class SegmentSensitivity:
    asset_id: str
    contribution_id: str
    attributable_equity_value: Decimal
    attributable_value_per_share: Decimal
    ownership_ratio: Decimal
    variables: tuple[VariableSensitivity, ...]

    @property
    def dominant(self) -> VariableSensitivity | None:
        if not self.variables:
            return None
        return max(self.variables, key=lambda item: item.max_abs_pct)


@dataclass(frozen=True)
class ScenarioSensitivity:
    scenario_id: str
    status: str
    rationale: str
    base_value_per_share: Decimal | None = None
    variables: tuple[VariableSensitivity, ...] = ()
    segments: tuple[SegmentSensitivity, ...] = ()

    @property
    def measured(self) -> bool:
        return (
            self.status == ReconstructionStatus.RECONSTRUCTED
            and bool(self.variables or self.segments)
        )

    @property
    def dominant(self) -> VariableSensitivity | None:
        candidates = list(self.variables)
        candidates.extend(
            variable for segment in self.segments for variable in segment.variables
        )
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.max_abs_pct)


@dataclass(frozen=True)
class ValuationSensitivityReport:
    reporting_unit: str
    scenarios: tuple[ScenarioSensitivity, ...]
    findings: tuple[AuditFinding, ...]
    report_hash: str

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.findings)

    @property
    def warnings(self) -> tuple[AuditFinding, ...]:
        return tuple(item for item in self.findings if not item.passed)

    @property
    def summary_ko(self) -> str:
        measured = tuple(item for item in self.scenarios if item.measured)
        if not measured:
            return "가치 민감도를 측정할 수 있는 시나리오가 없습니다"
        parts: list[str] = []
        for scenario in measured:
            dominant = scenario.dominant
            if dominant is None:
                continue
            asset = next(
                (
                    segment.asset_id
                    for segment in scenario.segments
                    if any(dominant is item for item in segment.variables)
                ),
                None,
            )
            location = (
                f"{scenario.scenario_id}/{asset}"
                if asset is not None
                else scenario.scenario_id
            )
            parts.append(
                f"{location}: {dominant.label} 최대 "
                f"{dominant.max_abs_pct * 100:.1f}%"
            )
        return "지배 변수 — " + " · ".join(parts)


def enterprise_value(
    *,
    fcff_path: tuple[Decimal, ...],
    discount_rate: Decimal,
    terminal_growth: Decimal,
) -> Decimal:
    """Reproduce the explicit-FCFF DCF kernel used by the evaluator."""
    if not fcff_path:
        raise ValuationSensitivityError("FCFF path is required")
    if discount_rate <= terminal_growth:
        raise ValuationSensitivityError("discount rate must exceed terminal growth")
    present_value = _ZERO
    for year, fcff in enumerate(fcff_path, start=1):
        present_value += fcff / (_ONE + discount_rate) ** year
    terminal = (
        fcff_path[-1]
        * (_ONE + terminal_growth)
        / (discount_rate - terminal_growth)
        / (_ONE + discount_rate) ** len(fcff_path)
    )
    return present_value + terminal


def _per_share(
    *,
    enterprise: Decimal,
    ev_to_equity_bridge: Decimal,
    ownership: Decimal,
    diluted_shares: Decimal,
) -> Decimal:
    return ownership * (enterprise + ev_to_equity_bridge) / diluted_shares


def _variable(
    *,
    variable: str,
    low_input: Decimal,
    base_input: Decimal,
    high_input: Decimal,
    low_value: Decimal,
    base_value: Decimal,
    high_value: Decimal,
    expected_direction: str,
) -> VariableSensitivity | None:
    if min(low_value, base_value, high_value) <= 0:
        return None
    assessment = assess_three_point_value_sensitivity(
        "DETERMINISTIC_VALUATION",
        variable=variable,
        low_input=float(low_input),
        base_input=float(base_input),
        high_input=float(high_input),
        low_value=float(low_value),
        base_value=float(base_value),
        high_value=float(high_value),
        expected_direction=expected_direction,
    )
    return VariableSensitivity(
        variable=variable,
        label=_VARIABLE_LABEL_KO[variable],
        low_input=low_input,
        base_input=base_input,
        high_input=high_input,
        low_value_per_share=low_value,
        base_value_per_share=base_value,
        high_value_per_share=high_value,
        low_value_pct=Decimal(str(assessment.downside_value_pct)),
        high_value_pct=Decimal(str(assessment.upside_value_pct)),
        monotonic=assessment.monotonic,
    )


def _sotp_component_sensitivity(
    *,
    component,
    reporting_unit: str,
    company_equity_value: Decimal,
    diluted_shares: Decimal,
    base_value_per_share: Decimal,
    policy: SensitivityPolicy,
) -> SegmentSensitivity | None:
    diagnostics = component.diagnostics
    ownership = component.ownership_ratio
    if (
        not isinstance(diagnostics, SegmentValuationDiagnostics)
        or not isinstance(ownership, Decimal)
        or not ownership.is_finite()
        or ownership <= _ZERO
    ):
        return None
    try:
        diagnostics.validate()
    except (TypeError, ValueError):
        return None
    if diluted_shares <= 0:
        raise ValuationSensitivityError("diluted shares must be positive")

    def to_reporting(amount: Decimal) -> Decimal:
        return Measure(amount, diagnostics.value_unit, "1970-01-01").convert_to(
            reporting_unit
        ).amount

    published = to_reporting(diagnostics.enterprise_value)
    recomputed = to_reporting(
        enterprise_value(
            fcff_path=diagnostics.fcff_path,
            discount_rate=diagnostics.discount_rate,
            terminal_growth=diagnostics.terminal_growth,
        )
    )
    if (
        published <= 0
        or abs(recomputed - published)
        > abs(published) * _RECONSTRUCTION_TOLERANCE
    ):
        return None

    attributable = component.attributable_equity_value.convert_to(
        reporting_unit
    ).amount
    full_equity = attributable / ownership
    bridge = full_equity - published
    other_equity = company_equity_value - attributable

    def value_at(
        *, discount_rate: Decimal, growth: Decimal, level: Decimal
    ) -> Decimal:
        raw = enterprise_value(
            fcff_path=tuple(item * level for item in diagnostics.fcff_path),
            discount_rate=discount_rate,
            terminal_growth=growth,
        )
        perturbed_attributable = ownership * (to_reporting(raw) + bridge)
        return (other_equity + perturbed_attributable) / diluted_shares

    base_replayed = value_at(
        discount_rate=diagnostics.discount_rate,
        growth=diagnostics.terminal_growth,
        level=_ONE,
    )
    if (
        abs(base_replayed - base_value_per_share)
        > max(abs(base_value_per_share), _ONE) * _RECONSTRUCTION_TOLERANCE
    ):
        return None

    rate = diagnostics.discount_rate
    growth = diagnostics.terminal_growth
    variables: list[VariableSensitivity] = []

    rate_low = rate - policy.discount_rate_delta
    rate_high = rate + policy.discount_rate_delta
    if rate_low > growth:
        measured = _variable(
            variable=DISCOUNT_RATE,
            low_input=rate_low,
            base_input=rate,
            high_input=rate_high,
            low_value=value_at(
                discount_rate=rate_low, growth=growth, level=_ONE
            ),
            base_value=base_value_per_share,
            high_value=value_at(
                discount_rate=rate_high, growth=growth, level=_ONE
            ),
            expected_direction="down",
        )
        if measured is not None:
            variables.append(measured)

    growth_low = growth - policy.terminal_growth_delta
    growth_high = growth + policy.terminal_growth_delta
    if growth_high < rate:
        measured = _variable(
            variable=TERMINAL_GROWTH,
            low_input=growth_low,
            base_input=growth,
            high_input=growth_high,
            low_value=value_at(
                discount_rate=rate, growth=growth_low, level=_ONE
            ),
            base_value=base_value_per_share,
            high_value=value_at(
                discount_rate=rate, growth=growth_high, level=_ONE
            ),
            expected_direction="up",
        )
        if measured is not None:
            variables.append(measured)

    level_low = _ONE - policy.fcff_level_delta
    level_high = _ONE + policy.fcff_level_delta
    measured = _variable(
        variable=FCFF_LEVEL,
        low_input=level_low,
        base_input=_ONE,
        high_input=level_high,
        low_value=value_at(discount_rate=rate, growth=growth, level=level_low),
        base_value=base_value_per_share,
        high_value=value_at(discount_rate=rate, growth=growth, level=level_high),
        expected_direction="up",
    )
    if measured is not None:
        variables.append(measured)

    if not variables:
        return None
    return SegmentSensitivity(
        asset_id=component.asset_id,
        contribution_id=component.contribution_id,
        attributable_equity_value=attributable,
        attributable_value_per_share=attributable / diluted_shares,
        ownership_ratio=ownership,
        variables=tuple(variables),
    )


def _sotp_scenario_sensitivity(
    *,
    scenario_id: str,
    reporting_unit: str,
    equity_value: Decimal,
    diluted_shares: Decimal,
    base_value_per_share: Decimal,
    company_value: CompanyScenarioEquityValue,
    policy: SensitivityPolicy,
) -> ScenarioSensitivity:
    segments = tuple(
        measured
        for component in company_value.components
        if (
            measured := _sotp_component_sensitivity(
                component=component,
                reporting_unit=reporting_unit,
                company_equity_value=equity_value,
                diluted_shares=diluted_shares,
                base_value_per_share=base_value_per_share,
                policy=policy,
            )
        )
        is not None
    )
    if not segments:
        return ScenarioSensitivity(
            scenario_id=scenario_id,
            status=ReconstructionStatus.NOT_RECONSTRUCTIBLE,
            rationale=(
                "SOTP 구성요소 중 동결 DCF 커널로 독립 재현 가능한 부문이 없어 "
                "부문별 민감도를 산출하지 않았습니다"
            ),
        )
    return ScenarioSensitivity(
        scenario_id=scenario_id,
        status=ReconstructionStatus.RECONSTRUCTED,
        rationale=(
            f"SOTP {len(company_value.components)}개 구성요소 중 "
            f"{len(segments)}개 DCF 부문을 다른 부문·모회사 조정을 고정한 채 "
            "독립적으로 3점 변동시켰습니다"
        ),
        base_value_per_share=base_value_per_share,
        segments=segments,
    )


def _scenario_sensitivity(
    *,
    scenario_id: str,
    reporting_unit: str,
    equity_value: Decimal,
    diluted_shares: Decimal,
    base_value_per_share: Decimal,
    company_value: CompanyScenarioEquityValue,
    policy: SensitivityPolicy,
) -> ScenarioSensitivity:
    if len(company_value.components) != 1:
        return _sotp_scenario_sensitivity(
            scenario_id=scenario_id,
            reporting_unit=reporting_unit,
            equity_value=equity_value,
            diluted_shares=diluted_shares,
            base_value_per_share=base_value_per_share,
            company_value=company_value,
            policy=policy,
        )
    reconstructible = reconstructible_dcf_component(company_value)
    if reconstructible is None:
        return ScenarioSensitivity(
            scenario_id=scenario_id,
            status=ReconstructionStatus.NOT_RECONSTRUCTIBLE,
            rationale=(
                "시나리오 가치가 단일 DCF 기여분으로 구성되지 않아 변수별 민감도를 "
                "분리할 수 없습니다"
            ),
        )
    diagnostics, ownership = reconstructible
    diagnostics.validate()
    if diluted_shares <= 0:
        raise ValuationSensitivityError("diluted shares must be positive")

    def to_reporting(amount: Decimal) -> Decimal:
        return Measure(amount, diagnostics.value_unit, "1970-01-01").convert_to(
            reporting_unit
        ).amount

    published = to_reporting(diagnostics.enterprise_value)
    recomputed = to_reporting(
        enterprise_value(
            fcff_path=diagnostics.fcff_path,
            discount_rate=diagnostics.discount_rate,
            terminal_growth=diagnostics.terminal_growth,
        )
    )
    if published <= 0 or abs(recomputed - published) > abs(published) * _RECONSTRUCTION_TOLERANCE:
        return ScenarioSensitivity(
            scenario_id=scenario_id,
            status=ReconstructionStatus.NOT_RECONSTRUCTIBLE,
            rationale=(
                "공표된 기업가치를 동일 할인 커널로 재현하지 못해 민감도를 산출하지 "
                "않았습니다"
            ),
        )

    bridge = equity_value / ownership - published

    def value_at(*, discount_rate: Decimal, growth: Decimal, level: Decimal) -> Decimal:
        raw = enterprise_value(
            fcff_path=tuple(item * level for item in diagnostics.fcff_path),
            discount_rate=discount_rate,
            terminal_growth=growth,
        )
        return _per_share(
            enterprise=to_reporting(raw),
            ev_to_equity_bridge=bridge,
            ownership=ownership,
            diluted_shares=diluted_shares,
        )

    rate = diagnostics.discount_rate
    growth = diagnostics.terminal_growth
    variables: list[VariableSensitivity] = []

    rate_low = rate - policy.discount_rate_delta
    rate_high = rate + policy.discount_rate_delta
    if rate_low > growth:
        measured = _variable(
            variable=DISCOUNT_RATE,
            low_input=rate_low,
            base_input=rate,
            high_input=rate_high,
            low_value=value_at(discount_rate=rate_low, growth=growth, level=_ONE),
            base_value=base_value_per_share,
            high_value=value_at(discount_rate=rate_high, growth=growth, level=_ONE),
            expected_direction="down",
        )
        if measured is not None:
            variables.append(measured)

    growth_low = growth - policy.terminal_growth_delta
    growth_high = growth + policy.terminal_growth_delta
    if growth_high < rate:
        measured = _variable(
            variable=TERMINAL_GROWTH,
            low_input=growth_low,
            base_input=growth,
            high_input=growth_high,
            low_value=value_at(discount_rate=rate, growth=growth_low, level=_ONE),
            base_value=base_value_per_share,
            high_value=value_at(discount_rate=rate, growth=growth_high, level=_ONE),
            expected_direction="up",
        )
        if measured is not None:
            variables.append(measured)

    level_low = _ONE - policy.fcff_level_delta
    level_high = _ONE + policy.fcff_level_delta
    measured = _variable(
        variable=FCFF_LEVEL,
        low_input=level_low,
        base_input=_ONE,
        high_input=level_high,
        low_value=value_at(discount_rate=rate, growth=growth, level=level_low),
        base_value=base_value_per_share,
        high_value=value_at(discount_rate=rate, growth=growth, level=level_high),
        expected_direction="up",
    )
    if measured is not None:
        variables.append(measured)

    return ScenarioSensitivity(
        scenario_id=scenario_id,
        status=ReconstructionStatus.RECONSTRUCTED,
        rationale="동결 현금흐름 경로를 고정한 채 커널 변수만 3점 변동시켰습니다",
        base_value_per_share=base_value_per_share,
        variables=tuple(variables),
    )


def _findings(
    scenarios: tuple[ScenarioSensitivity, ...],
    policy: SensitivityPolicy,
) -> tuple[AuditFinding, ...]:
    measured = tuple(item for item in scenarios if item.measured)
    findings = [
        AuditFinding(
            "valuation_sensitivity_measured",
            bool(measured),
            False,
            (
                f"{len(measured)}/{len(scenarios)}개 시나리오에서 커널 변수 민감도를 "
                "산출했습니다"
                if measured
                else "단일 DCF 기여분으로 재구성 가능한 시나리오가 없어 민감도를 산출하지 못했습니다"
            ),
        )
    ]
    if not measured:
        return tuple(findings)

    variable_rows = tuple(
        (scenario.scenario_id, None, item)
        for scenario in measured
        for item in scenario.variables
    ) + tuple(
        (scenario.scenario_id, segment.asset_id, item)
        for scenario in measured
        for segment in scenario.segments
        for item in segment.variables
    )
    non_monotonic = tuple(
        (
            f"{scenario_id}/{asset_id}/{item.variable}"
            if asset_id is not None
            else f"{scenario_id}/{item.variable}"
        )
        for scenario_id, asset_id, item in variable_rows
        if not item.monotonic
    )
    findings.append(
        AuditFinding(
            "valuation_sensitivity_monotonicity",
            not non_monotonic,
            False,
            (
                "모든 변수가 예상 방향으로 단조 반응했습니다"
                if not non_monotonic
                else "예상 방향과 다르게 반응한 변수가 있습니다: " + ", ".join(non_monotonic)
            ),
        )
    )

    concentrated = tuple(
        (scenario_id, asset_id, item)
        for scenario_id, asset_id, item in variable_rows
        if item.max_abs_pct > policy.high_sensitivity_pct
    )
    findings.append(
        AuditFinding(
            "valuation_sensitivity_concentration",
            not concentrated,
            False,
            (
                "단일 커널 변수의 소폭 변동이 가치를 크게 바꾸지 않습니다"
                if not concentrated
                else "소폭 변동만으로 가치가 크게 움직이는 변수가 있습니다: "
                + " · ".join(
                    (
                        f"{scenario_id}/{asset_id} {item.label} "
                        if asset_id is not None
                        else f"{scenario_id} {item.label} "
                    )
                    + f"{item.max_abs_pct * 100:.1f}%"
                    for scenario_id, asset_id, item in concentrated
                )
            ),
        )
    )
    return tuple(findings)


def _report_hash(
    *,
    valuation_hash: str,
    scenarios: tuple[ScenarioSensitivity, ...],
    findings: tuple[AuditFinding, ...],
) -> str:
    payload = {
        "contract": "valuation_sensitivity/v2",
        "valuation_hash": valuation_hash,
        "scenarios": [
            {
                "scenario_id": scenario.scenario_id,
                "status": scenario.status,
                "variables": [
                    {
                        "variable": item.variable,
                        "low_input": str(item.low_input),
                        "high_input": str(item.high_input),
                        "low_value_pct": str(item.low_value_pct),
                        "high_value_pct": str(item.high_value_pct),
                        "monotonic": item.monotonic,
                    }
                    for item in scenario.variables
                ],
                "segments": [
                    {
                        "asset_id": segment.asset_id,
                        "contribution_id": segment.contribution_id,
                        "attributable_equity_value": str(
                            segment.attributable_equity_value
                        ),
                        "ownership_ratio": str(segment.ownership_ratio),
                        "variables": [
                            {
                                "variable": item.variable,
                                "low_input": str(item.low_input),
                                "high_input": str(item.high_input),
                                "low_value_per_share": str(item.low_value_per_share),
                                "base_value_per_share": str(item.base_value_per_share),
                                "high_value_per_share": str(item.high_value_per_share),
                                "low_value_pct": str(item.low_value_pct),
                                "high_value_pct": str(item.high_value_pct),
                                "monotonic": item.monotonic,
                            }
                            for item in segment.variables
                        ],
                    }
                    for segment in scenario.segments
                ],
            }
            for scenario in scenarios
        ],
        "findings": [
            [item.check, item.passed, item.blocking, item.detail] for item in findings
        ],
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def build_valuation_sensitivity_report(
    *,
    valuation: GenericValuationResult,
    policy: SensitivityPolicy | None = None,
) -> ValuationSensitivityReport:
    effective_policy = policy or SensitivityPolicy()
    effective_policy.validate()
    if not valuation.scenarios:
        raise ValuationSensitivityError("valuation has no scenarios")

    by_scenario = {
        item.scenario_id: item for item in valuation.equity_aggregation.scenario_values
    }
    scenarios: list[ScenarioSensitivity] = []
    for per_share in valuation.scenarios:
        company_value = by_scenario.get(per_share.scenario_id)
        if company_value is None:
            raise ValuationSensitivityError(
                f"scenario {per_share.scenario_id} has no aggregation record"
            )
        scenarios.append(
            _scenario_sensitivity(
                scenario_id=per_share.scenario_id,
                reporting_unit=valuation.reporting_unit,
                equity_value=per_share.equity_value_amount,
                diluted_shares=per_share.diluted_shares,
                base_value_per_share=per_share.value_per_share,
                company_value=company_value,
                policy=effective_policy,
            )
        )
    scenario_tuple = tuple(scenarios)
    findings = _findings(scenario_tuple, effective_policy)
    return ValuationSensitivityReport(
        reporting_unit=valuation.reporting_unit,
        scenarios=scenario_tuple,
        findings=findings,
        report_hash=_report_hash(
            valuation_hash=valuation.valuation_hash,
            scenarios=scenario_tuple,
            findings=findings,
        ),
    )


def valuation_sensitivity_adapter(
    *,
    policy: SensitivityPolicy | None = None,
) -> StageAdapter:
    """Publish three-point kernel sensitivity as a non-blocking audit guardrail."""

    def run(context: OrchestratorContext) -> StageExecutionResult:
        valuation = context.data.get("generic_valuation_result")
        if not isinstance(valuation, GenericValuationResult):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "GenericValuationResult is required before sensitivity measurement",
                blocking=True,
            )
        try:
            report = build_valuation_sensitivity_report(
                valuation=valuation,
                policy=policy,
            )
        except (ValuationSensitivityError, TypeError, ValueError) as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"valuation sensitivity measurement failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        outputs = {
            "valuation_sensitivity_report": report,
            "valuation_sensitivity_hash": report.report_hash,
            "valuation_sensitivity_summary": report.summary_ko,
        }
        if report.passed:
            return StageExecutionResult(
                StageStatus.PASS,
                "커널 변수 3점 민감도를 산출했습니다: " + report.summary_ko,
                outputs,
            )
        return StageExecutionResult(
            StageStatus.WARNING,
            "가치가 단일 변수에 민감합니다: " + report.summary_ko,
            outputs,
        )

    return run
