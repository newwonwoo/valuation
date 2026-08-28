from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math


@dataclass(frozen=True)
class ContinuousWeightPolicy:
    minimum_weight: Decimal = Decimal("0.05")
    skill_temperature: Decimal = Decimal("0.08")
    target_resolved_events: int = 100
    target_companies: int = 20
    target_quarters: int = 8
    target_oos_windows: int = 4
    ece_soft_scale: Decimal = Decimal("0.10")
    uncertainty_inflation_max: Decimal = Decimal("3.0")

    def validate(self) -> None:
        if not Decimal("0") <= self.minimum_weight < Decimal("1"):
            raise ValueError("minimum predictive weight must lie within [0,1)")
        if self.skill_temperature <= 0 or self.ece_soft_scale <= 0:
            raise ValueError("continuous predictive scales must be positive")
        if min(self.target_resolved_events, self.target_companies, self.target_quarters, self.target_oos_windows) <= 0:
            raise ValueError("continuous predictive targets must be positive")
        if self.uncertainty_inflation_max < Decimal("1"):
            raise ValueError("uncertainty inflation maximum cannot be below one")


@dataclass(frozen=True)
class PredictiveEvidenceProfile:
    resolved_events: int
    company_count: int
    quarter_count: int
    brier_skill_windows: tuple[Decimal, ...] = ()
    brier_skill_interval: tuple[Decimal, Decimal] | None = None
    ece: Decimal | None = None
    regime_similarity: Decimal = Decimal("0.50")

    def validate(self) -> None:
        if min(self.resolved_events, self.company_count, self.quarter_count) < 0:
            raise ValueError("predictive evidence counts cannot be negative")
        if any(not value.is_finite() for value in self.brier_skill_windows):
            raise ValueError("Brier skill windows must be finite")
        if self.brier_skill_interval is not None:
            lower, upper = self.brier_skill_interval
            if not lower.is_finite() or not upper.is_finite() or lower > upper:
                raise ValueError("Brier skill interval is invalid")
        if self.ece is not None and (not self.ece.is_finite() or self.ece < 0):
            raise ValueError("ECE must be finite and non-negative")
        if not self.regime_similarity.is_finite() or not Decimal("0") <= self.regime_similarity <= Decimal("1"):
            raise ValueError("regime similarity must lie within [0,1]")


@dataclass(frozen=True)
class PredictiveEvidenceWeight:
    likelihood_weight: Decimal
    uncertainty_inflation: Decimal
    skill_component: Decimal
    sample_component: Decimal
    company_component: Decimal
    quarter_component: Decimal
    oos_component: Decimal
    calibration_component: Decimal
    regime_component: Decimal
    interval_precision_component: Decimal
    mean_brier_skill: Decimal | None


def continuous_predictive_weight(profile: PredictiveEvidenceProfile, policy: ContinuousWeightPolicy = ContinuousWeightPolicy()) -> PredictiveEvidenceWeight:
    profile.validate()
    policy.validate()
    mean_bss = sum(profile.brier_skill_windows, Decimal("0")) / Decimal(len(profile.brier_skill_windows)) if profile.brier_skill_windows else None
    skill_component = _logistic((mean_bss if mean_bss is not None else Decimal("0")) / policy.skill_temperature)
    sample_component = _saturation(profile.resolved_events, policy.target_resolved_events)
    company_component = _saturation(profile.company_count, policy.target_companies)
    quarter_component = _saturation(profile.quarter_count, policy.target_quarters)
    oos_component = _saturation(len(profile.brier_skill_windows), policy.target_oos_windows)
    calibration_component = Decimal("0.50") if profile.ece is None else Decimal(str(math.exp(-float(profile.ece / policy.ece_soft_scale))))
    if profile.brier_skill_interval is None:
        interval_precision_component = Decimal("0.50")
    else:
        lower, upper = profile.brier_skill_interval
        width = max(Decimal("0"), upper - lower)
        interval_precision_component = Decimal(str(math.exp(-float(width / Decimal("0.40")))))
    regime_component = profile.regime_similarity
    raw = (
        Decimal("0.30") * skill_component
        + Decimal("0.18") * sample_component
        + Decimal("0.10") * company_component
        + Decimal("0.10") * quarter_component
        + Decimal("0.10") * oos_component
        + Decimal("0.08") * calibration_component
        + Decimal("0.08") * interval_precision_component
        + Decimal("0.06") * regime_component
    )
    weight = _clip(policy.minimum_weight + (Decimal("1") - policy.minimum_weight) * raw)
    uncertainty_inflation = Decimal("1") + (Decimal("1") - weight) * (policy.uncertainty_inflation_max - Decimal("1"))
    return PredictiveEvidenceWeight(weight, uncertainty_inflation, skill_component, sample_component, company_component, quarter_component, oos_component, calibration_component, regime_component, interval_precision_component, mean_bss)


def _logistic(value: Decimal) -> Decimal:
    x = max(-50.0, min(50.0, float(value)))
    return Decimal(str(1.0 / (1.0 + math.exp(-x))))


def _saturation(observed: int, target: int) -> Decimal:
    if observed <= 0:
        return Decimal("0")
    return Decimal(str(1.0 - math.exp(-float(observed) / float(target))))


def _clip(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("1"), value))
