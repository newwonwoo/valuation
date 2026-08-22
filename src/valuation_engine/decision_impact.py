from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from statistics import fmean


class ImpactClassification(str, Enum):
    GUARDRAIL_CRITICAL = "guardrail_critical"
    DECISION_MATERIAL = "decision_material"
    VALUE_MATERIAL = "value_material"
    TIMING_MATERIAL = "timing_material"
    ASSUMPTION_ONLY = "assumption_only"
    LOW_OBSERVED_IMPACT = "low_observed_impact"
    INCONCLUSIVE = "inconclusive"


class ResearchIntensity(str, Enum):
    ALWAYS = "always"
    CONDITIONAL = "conditional"
    SAMPLE_ONLY = "sample_only"
    RETIRE_CANDIDATE = "retire_candidate"
    KEEP_GUARDRAIL = "keep_guardrail"


@dataclass(frozen=True)
class DecisionOutcome:
    status: str
    intrinsic_value_per_share: float | None = None
    assumption_hash: str = ""
    route_hash: str = ""
    selected_methods: tuple[str, ...] = ()
    conclusion_tags: tuple[str, ...] = ()
    timing_days: float | None = None
    blocked_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.status:
            raise ValueError("decision outcome status is required")
        if self.intrinsic_value_per_share is not None:
            if not isfinite(self.intrinsic_value_per_share) or self.intrinsic_value_per_share <= 0:
                raise ValueError("intrinsic value must be finite and positive when supplied")
        if self.timing_days is not None and (not isfinite(self.timing_days) or self.timing_days < 0):
            raise ValueError("timing_days must be finite and non-negative")


@dataclass(frozen=True)
class ResearchEffort:
    source_queries: int = 0
    documents_reviewed: int = 0
    llm_calls: int = 0
    elapsed_seconds: float = 0.0

    def __post_init__(self) -> None:
        if min(self.source_queries, self.documents_reviewed, self.llm_calls) < 0:
            raise ValueError("research effort counts must be non-negative")
        if not isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must be finite and non-negative")


@dataclass(frozen=True)
class ModuleImpactTrace:
    module_id: str
    evidence_ids: tuple[str, ...] = ()
    mechanism_ids: tuple[str, ...] = ()
    affected_assumptions: tuple[str, ...] = ()
    affected_decisions: tuple[str, ...] = ()
    economic_path_ids: tuple[str, ...] = ()
    final_output_refs: tuple[str, ...] = ()
    guardrail_only: bool = False

    def validate(self) -> None:
        if not self.module_id:
            raise ValueError("impact trace requires module_id")
        connected = any(
            (
                self.affected_assumptions,
                self.affected_decisions,
                self.economic_path_ids,
                self.final_output_refs,
            )
        )
        if not connected and not self.guardrail_only:
            raise ValueError(
                "active module has no path to assumption, decision, economic path or final output"
            )


@dataclass(frozen=True)
class ImpactPolicy:
    value_materiality_pct: float = 0.01
    timing_materiality_days: float = 30.0
    min_observations_for_downrank: int = 6
    always_material_rate: float = 0.5
    high_effort_documents_per_run: float = 2.0

    def __post_init__(self) -> None:
        if not 0 <= self.value_materiality_pct <= 1:
            raise ValueError("value_materiality_pct must be in [0, 1]")
        if self.timing_materiality_days < 0:
            raise ValueError("timing_materiality_days must be non-negative")
        if self.min_observations_for_downrank < 1:
            raise ValueError("min_observations_for_downrank must be positive")
        if not 0 <= self.always_material_rate <= 1:
            raise ValueError("always_material_rate must be in [0, 1]")
        if self.high_effort_documents_per_run < 0:
            raise ValueError("high_effort_documents_per_run must be non-negative")


@dataclass(frozen=True)
class ModuleImpactAssessment:
    module_id: str
    classification: ImpactClassification
    value_delta_abs: float | None
    value_delta_pct: float | None
    status_changed: bool
    route_changed: bool
    methods_changed: bool
    assumption_changed: bool
    conclusion_changed: bool
    timing_delta_days: float | None
    guardrail_violation_detected: bool
    material: bool
    rationale: str


@dataclass(frozen=True)
class ModuleHistoryEntry:
    assessment: ModuleImpactAssessment
    effort: ResearchEffort
    applicable: bool = True
    research_performed: bool = True
    mandatory_guardrail: bool = False


def compare_module_counterfactual(
    module_id: str,
    *,
    baseline: DecisionOutcome,
    counterfactual: DecisionOutcome,
    guardrail_violation_detected: bool = False,
    policy: ImpactPolicy | None = None,
) -> ModuleImpactAssessment:
    """Compare the real run with a counterfactual where one module/gate is neutralized.

    Baseline is the canonical run with the module. Counterfactual must be produced without
    using target-market information to tune assumptions.
    """
    policy = policy or ImpactPolicy()
    status_changed = baseline.status != counterfactual.status
    route_changed = baseline.route_hash != counterfactual.route_hash
    methods_changed = baseline.selected_methods != counterfactual.selected_methods
    assumption_changed = baseline.assumption_hash != counterfactual.assumption_hash
    conclusion_changed = (
        baseline.conclusion_tags != counterfactual.conclusion_tags
        or baseline.blocked_reasons != counterfactual.blocked_reasons
    )

    value_delta_abs: float | None = None
    value_delta_pct: float | None = None
    if baseline.intrinsic_value_per_share is not None and counterfactual.intrinsic_value_per_share is not None:
        value_delta_abs = baseline.intrinsic_value_per_share - counterfactual.intrinsic_value_per_share
        value_delta_pct = value_delta_abs / counterfactual.intrinsic_value_per_share

    timing_delta: float | None = None
    if baseline.timing_days is not None and counterfactual.timing_days is not None:
        timing_delta = baseline.timing_days - counterfactual.timing_days

    value_material = value_delta_pct is not None and abs(value_delta_pct) >= policy.value_materiality_pct
    timing_material = timing_delta is not None and abs(timing_delta) >= policy.timing_materiality_days
    decision_material = status_changed or route_changed or methods_changed or conclusion_changed

    if guardrail_violation_detected:
        classification = ImpactClassification.GUARDRAIL_CRITICAL
        material = True
        rationale = "counterfactual removal permits a prohibited/invalid state"
    elif decision_material:
        classification = ImpactClassification.DECISION_MATERIAL
        material = True
        rationale = "module changes status, route, method, conclusion or blocking decision"
    elif value_material:
        classification = ImpactClassification.VALUE_MATERIAL
        material = True
        rationale = "module changes intrinsic value beyond the configured materiality threshold"
    elif timing_material:
        classification = ImpactClassification.TIMING_MATERIAL
        material = True
        rationale = "module changes timing beyond the configured materiality threshold"
    elif assumption_changed:
        classification = ImpactClassification.ASSUMPTION_ONLY
        material = False
        rationale = "module changes compiled assumptions but observed final decision/value impact is below threshold"
    elif (
        baseline.intrinsic_value_per_share is None
        and counterfactual.intrinsic_value_per_share is None
        and not any((status_changed, route_changed, methods_changed, conclusion_changed))
    ):
        classification = ImpactClassification.INCONCLUSIVE
        material = False
        rationale = "neither run produced a comparable decision/value signal"
    else:
        classification = ImpactClassification.LOW_OBSERVED_IMPACT
        material = False
        rationale = "no material value, timing, route, method, status or conclusion change observed"

    return ModuleImpactAssessment(
        module_id=module_id,
        classification=classification,
        value_delta_abs=value_delta_abs,
        value_delta_pct=value_delta_pct,
        status_changed=status_changed,
        route_changed=route_changed,
        methods_changed=methods_changed,
        assumption_changed=assumption_changed,
        conclusion_changed=conclusion_changed,
        timing_delta_days=timing_delta,
        guardrail_violation_detected=guardrail_violation_detected,
        material=material,
        rationale=rationale,
    )


def recommend_research_intensity(
    history: tuple[ModuleHistoryEntry, ...],
    *,
    policy: ImpactPolicy | None = None,
) -> ResearchIntensity:
    """Down-rank costly research only after repeated applicable, counterfactual observations.

    Mandatory guardrails are retained even when their ordinary value delta is zero.
    Non-applicable runs are excluded from the impact-rate denominator.
    """
    policy = policy or ImpactPolicy()
    applicable = tuple(item for item in history if item.applicable)
    if any(item.mandatory_guardrail for item in applicable):
        return ResearchIntensity.KEEP_GUARDRAIL
    if not applicable:
        return ResearchIntensity.SAMPLE_ONLY

    material = tuple(item for item in applicable if item.assessment.material)
    material_rate = len(material) / len(applicable)

    if material_rate >= policy.always_material_rate:
        return ResearchIntensity.ALWAYS
    if material:
        return ResearchIntensity.CONDITIONAL

    if len(applicable) < policy.min_observations_for_downrank:
        return ResearchIntensity.SAMPLE_ONLY

    researched = tuple(item for item in applicable if item.research_performed)
    mean_documents = fmean(item.effort.documents_reviewed for item in researched) if researched else 0.0
    if mean_documents >= policy.high_effort_documents_per_run:
        return ResearchIntensity.RETIRE_CANDIDATE
    return ResearchIntensity.SAMPLE_ONLY


def wasted_research_entries(history: tuple[ModuleHistoryEntry, ...]) -> tuple[ModuleHistoryEntry, ...]:
    """Research on a known non-applicable module is direct avoidable waste."""
    return tuple(item for item in history if not item.applicable and item.research_performed)


class SensitivityMethod(str, Enum):
    ABLATION = "ablation"
    NUMERIC_PERTURBATION = "numeric_perturbation"
    GATE_COUNTERFACTUAL = "gate_counterfactual"
    TIMING_SHIFT = "timing_shift"
    ROUTE_COUNTERFACTUAL = "route_counterfactual"


@dataclass(frozen=True)
class ModuleSensitivityPlan:
    module_id: str
    methods: tuple[SensitivityMethod, ...]
    expected_impact_paths: tuple[str, ...]
    perturbation_variable: str | None = None
    perturbation_bounds: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        if not self.module_id or not self.methods or not self.expected_impact_paths:
            raise ValueError("sensitivity plan requires module, methods and expected impact paths")
        if self.perturbation_bounds is not None:
            low, high = self.perturbation_bounds
            if not all(isfinite(v) for v in (low, high)) or low >= high:
                raise ValueError("perturbation bounds must be finite and ordered")


@dataclass(frozen=True)
class NumericSensitivityAssessment:
    module_id: str
    variable: str
    low_input: float
    base_input: float
    high_input: float
    low_value: float
    base_value: float
    high_value: float
    downside_value_pct: float
    upside_value_pct: float
    monotonic: bool


def assess_three_point_value_sensitivity(
    module_id: str,
    *,
    variable: str,
    low_input: float,
    base_input: float,
    high_input: float,
    low_value: float,
    base_value: float,
    high_value: float,
    expected_direction: str = "up",
) -> NumericSensitivityAssessment:
    values = (low_input, base_input, high_input, low_value, base_value, high_value)
    if not all(isfinite(v) for v in values):
        raise ValueError("numeric sensitivity values must be finite")
    if not low_input < base_input < high_input:
        raise ValueError("input perturbation must satisfy low < base < high")
    if min(low_value, base_value, high_value) <= 0:
        raise ValueError("valuation outputs must be positive")
    if expected_direction == "up":
        monotonic = low_value <= base_value <= high_value
    elif expected_direction == "down":
        monotonic = low_value >= base_value >= high_value
    elif expected_direction == "non_monotonic":
        monotonic = True
    else:
        raise ValueError("expected_direction must be up, down or non_monotonic")
    return NumericSensitivityAssessment(
        module_id=module_id,
        variable=variable,
        low_input=low_input,
        base_input=base_input,
        high_input=high_input,
        low_value=low_value,
        base_value=base_value,
        high_value=high_value,
        downside_value_pct=low_value / base_value - 1.0,
        upside_value_pct=high_value / base_value - 1.0,
        monotonic=monotonic,
    )
