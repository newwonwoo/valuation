from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from math import log
from pathlib import Path

import yaml

from .records import CalibrationStatus


class ForecastOutcomeState(str, Enum):
    OCCURRED = "occurred"
    NOT_OCCURRED = "not_occurred"
    CENSORED = "censored"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class ProbabilityForecast:
    forecast_id: str
    event_key: str
    hypothesis_id: str
    company_id: str
    forecast_class: str
    horizon: str
    event_definition: str
    issued_at: datetime
    evaluation_deadline: date
    probability: Decimal
    displayed_band: str
    evidence_snapshot_hash: str
    model_version: str
    resolution_rule: str
    resolution_source_policy: str
    supersedes_id: str | None = None

    def validate(self) -> None:
        required = (
            self.forecast_id,
            self.event_key,
            self.hypothesis_id,
            self.company_id,
            self.forecast_class,
            self.horizon,
            self.event_definition,
            self.displayed_band,
            self.evidence_snapshot_hash,
            self.model_version,
            self.resolution_rule,
            self.resolution_source_policy,
        )
        if any(not value for value in required):
            raise ValueError("probability forecast requires identity, cohort, definition and resolution contract")
        if self.issued_at.tzinfo is None:
            raise ValueError("issued_at must be timezone-aware")
        if self.evaluation_deadline < self.issued_at.date():
            raise ValueError("evaluation deadline cannot precede forecast issuance")
        if not self.probability.is_finite() or not Decimal("0") < self.probability < Decimal("1"):
            raise ValueError("unresolved probability forecast must be strictly between zero and one")

    @property
    def cohort_key(self) -> str:
        return f"{self.forecast_class}|{self.horizon}"


@dataclass(frozen=True)
class ForecastOutcome:
    forecast_id: str
    observed_at: datetime
    outcome: ForecastOutcomeState
    outcome_evidence_ids: tuple[str, ...]
    resolver_id: str
    rationale: str

    def validate(self) -> None:
        if not self.forecast_id or not self.resolver_id or not self.rationale:
            raise ValueError("forecast outcome requires forecast, resolver and rationale")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.outcome in {ForecastOutcomeState.OCCURRED, ForecastOutcomeState.NOT_OCCURRED} and not self.outcome_evidence_ids:
            raise ValueError("resolved binary outcome requires primary outcome Evidence IDs")


class ProbabilityCalibrationLedger:
    """Append-only forecast/outcome ledger with one effective sample per independent event."""

    def __init__(self) -> None:
        self._forecasts: dict[str, ProbabilityForecast] = {}
        self._outcomes: dict[str, ForecastOutcome] = {}

    def append_forecast(self, forecast: ProbabilityForecast) -> None:
        forecast.validate()
        if forecast.forecast_id in self._forecasts:
            raise ValueError(f"duplicate forecast_id: {forecast.forecast_id}")
        same_event = tuple(item for item in self._forecasts.values() if item.event_key == forecast.event_key)
        if forecast.supersedes_id is None:
            if same_event:
                raise ValueError("repeated forecast for one event must explicitly supersede the prior forecast")
        else:
            prior = self._forecasts.get(forecast.supersedes_id)
            if prior is None:
                raise ValueError("supersedes_id must reference an existing forecast")
            if prior.event_key != forecast.event_key or prior.cohort_key != forecast.cohort_key:
                raise ValueError("superseding forecast must preserve event key, forecast class and horizon")
            if forecast.issued_at <= prior.issued_at:
                raise ValueError("superseding forecast must be issued after the prior forecast")
            if any(item.supersedes_id == prior.forecast_id for item in self._forecasts.values()):
                raise ValueError("a forecast may have only one direct superseding revision")
        self._forecasts[forecast.forecast_id] = forecast

    def append_outcome(self, outcome: ForecastOutcome) -> None:
        outcome.validate()
        forecast = self._forecasts.get(outcome.forecast_id)
        if forecast is None:
            raise ValueError("outcome references unknown forecast")
        if outcome.forecast_id in self._outcomes:
            raise ValueError("forecast outcome is immutable once recorded")
        if outcome.observed_at < forecast.issued_at:
            raise ValueError("outcome observation cannot precede forecast issuance")
        if any(item.supersedes_id == forecast.forecast_id for item in self._forecasts.values()):
            raise ValueError("resolve the terminal forecast revision, not a superseded revision")
        self._outcomes[outcome.forecast_id] = outcome

    @property
    def forecasts(self) -> tuple[ProbabilityForecast, ...]:
        return tuple(self._forecasts.values())

    @property
    def outcomes(self) -> tuple[ForecastOutcome, ...]:
        return tuple(self._outcomes.values())

    def terminal_forecasts(self, *, forecast_class: str, horizon: str) -> tuple[ProbabilityForecast, ...]:
        superseded = {item.supersedes_id for item in self._forecasts.values() if item.supersedes_id}
        return tuple(
            item
            for item in self._forecasts.values()
            if item.forecast_class == forecast_class
            and item.horizon == horizon
            and item.forecast_id not in superseded
        )

    def outcome_for(self, forecast_id: str) -> ForecastOutcome | None:
        return self._outcomes.get(forecast_id)


@dataclass(frozen=True)
class ReliabilityBin:
    lower: Decimal
    upper: Decimal
    count: int
    mean_probability: Decimal | None
    observed_frequency: Decimal | None


@dataclass(frozen=True)
class CalibrationPolicy:
    version: str
    base_rate: Decimal
    min_resolved_events: int = 200
    min_companies: int = 20
    min_quarters: int = 8
    min_per_displayed_band: int = 30
    min_oos_windows: int = 2
    max_ece: Decimal = Decimal("0.08")
    max_ambiguous_censored_rate: Decimal = Decimal("0.10")
    fixed_bin_edges: tuple[Decimal, ...] = (
        Decimal("0"), Decimal("0.2"), Decimal("0.4"), Decimal("0.6"), Decimal("0.8"), Decimal("1"),
    )

    def validate(self) -> None:
        if not self.version or not self.base_rate.is_finite() or not Decimal("0") < self.base_rate < Decimal("1"):
            raise ValueError("calibration policy requires version and base_rate within (0,1)")
        if min(self.min_resolved_events, self.min_companies, self.min_quarters, self.min_per_displayed_band, self.min_oos_windows) < 1:
            raise ValueError("calibration policy minimum counts must be positive")
        if not Decimal("0") <= self.max_ece <= Decimal("1"):
            raise ValueError("max_ece must be within [0,1]")
        if not Decimal("0") <= self.max_ambiguous_censored_rate <= Decimal("1"):
            raise ValueError("max ambiguous/censored rate must be within [0,1]")
        if self.fixed_bin_edges[0] != Decimal("0") or self.fixed_bin_edges[-1] != Decimal("1"):
            raise ValueError("fixed probability bins must span zero to one")
        if any(left >= right for left, right in zip(self.fixed_bin_edges, self.fixed_bin_edges[1:])):
            raise ValueError("fixed probability bins must be strictly increasing")


@dataclass(frozen=True)
class CalibrationCertificate:
    cohort_key: str
    forecast_class: str
    horizon: str
    policy_version: str
    mapping_version: str
    snapshot_hash: str
    status: CalibrationStatus

    def validate_for_weighting(self) -> None:
        if self.status is not CalibrationStatus.CALIBRATED:
            raise PermissionError("only a CALIBRATED certificate may authorize intrinsic probability weighting")
        if not all((self.cohort_key, self.forecast_class, self.horizon, self.policy_version, self.mapping_version, self.snapshot_hash)):
            raise ValueError("calibration certificate is incomplete")


@dataclass(frozen=True)
class CalibrationSnapshot:
    cohort_key: str
    forecast_class: str
    horizon: str
    cutoff: datetime
    raw_sample_count: int
    effective_sample_count: int
    company_count: int
    quarter_count: int
    band_counts: tuple[tuple[str, int], ...]
    brier_score: Decimal | None
    brier_skill_score: Decimal | None
    log_loss: Decimal | None
    ece: Decimal | None
    outcome_coverage: Decimal
    ambiguous_censored_rate: Decimal
    reliability_bins: tuple[ReliabilityBin, ...]
    oos_brier_skill_windows: tuple[Decimal, ...]
    mapping_version: str
    policy_version: str
    status: CalibrationStatus
    gate_failures: tuple[str, ...]
    snapshot_hash: str

    def certificate(self) -> CalibrationCertificate:
        if self.status is not CalibrationStatus.CALIBRATED:
            raise PermissionError("calibration snapshot has not passed the promotion gate")
        certificate = CalibrationCertificate(
            self.cohort_key,
            self.forecast_class,
            self.horizon,
            self.policy_version,
            self.mapping_version,
            self.snapshot_hash,
            self.status,
        )
        certificate.validate_for_weighting()
        return certificate


def _quarter_key(value: datetime) -> str:
    return f"{value.year}Q{((value.month - 1) // 3) + 1}"


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values))


def _reliability_bins(samples: tuple[tuple[Decimal, Decimal], ...], edges: tuple[Decimal, ...]) -> tuple[ReliabilityBin, ...]:
    result: list[ReliabilityBin] = []
    for index, (lower, upper) in enumerate(zip(edges, edges[1:])):
        bucket = tuple(
            (probability, outcome)
            for probability, outcome in samples
            if probability >= lower and (probability < upper or (index == len(edges) - 2 and probability <= upper))
        )
        if not bucket:
            result.append(ReliabilityBin(lower, upper, 0, None, None))
            continue
        result.append(
            ReliabilityBin(
                lower,
                upper,
                len(bucket),
                _mean(tuple(item[0] for item in bucket)),
                _mean(tuple(item[1] for item in bucket)),
            )
        )
    return tuple(result)


def _ece(bins: tuple[ReliabilityBin, ...], total: int) -> Decimal:
    if total == 0:
        return Decimal("0")
    return sum(
        (
            Decimal(item.count) / Decimal(total)
            * abs(item.mean_probability - item.observed_frequency)
            for item in bins
            if item.count and item.mean_probability is not None and item.observed_frequency is not None
        ),
        Decimal("0"),
    )


def build_calibration_snapshot(
    ledger: ProbabilityCalibrationLedger,
    *,
    forecast_class: str,
    horizon: str,
    cutoff: datetime,
    policy: CalibrationPolicy,
    mapping_version: str,
    oos_brier_skill_windows: tuple[Decimal, ...] = (),
    prior_status: CalibrationStatus = CalibrationStatus.UNCALIBRATED,
) -> CalibrationSnapshot:
    policy.validate()
    if cutoff.tzinfo is None or not forecast_class or not horizon or not mapping_version:
        raise ValueError("calibration snapshot requires timezone-aware cutoff, cohort and mapping version")
    raw = tuple(
        item
        for item in ledger.forecasts
        if item.forecast_class == forecast_class and item.horizon == horizon and item.issued_at <= cutoff
    )
    terminal = tuple(item for item in ledger.terminal_forecasts(forecast_class=forecast_class, horizon=horizon) if item.issued_at <= cutoff)
    resolved_pairs: list[tuple[ProbabilityForecast, ForecastOutcome]] = []
    ambiguous_or_censored = 0
    observed_count = 0
    for forecast in terminal:
        outcome = ledger.outcome_for(forecast.forecast_id)
        if outcome is None or outcome.observed_at > cutoff:
            continue
        observed_count += 1
        if outcome.outcome in {ForecastOutcomeState.CENSORED, ForecastOutcomeState.AMBIGUOUS}:
            ambiguous_or_censored += 1
            continue
        resolved_pairs.append((forecast, outcome))

    samples = tuple(
        (
            forecast.probability,
            Decimal("1") if outcome.outcome is ForecastOutcomeState.OCCURRED else Decimal("0"),
        )
        for forecast, outcome in resolved_pairs
    )
    effective = len(samples)
    brier: Decimal | None = None
    bss: Decimal | None = None
    log_loss_value: Decimal | None = None
    ece_value: Decimal | None = None
    bins = _reliability_bins(samples, policy.fixed_bin_edges)
    if samples:
        brier = _mean(tuple((probability - outcome) ** 2 for probability, outcome in samples))
        base_brier = _mean(tuple((policy.base_rate - outcome) ** 2 for _, outcome in samples))
        bss = None if base_brier == 0 else Decimal("1") - brier / base_brier
        epsilon = Decimal("1e-12")
        log_losses = []
        for probability, outcome in samples:
            p = min(Decimal("1") - epsilon, max(epsilon, probability))
            value = -(float(outcome) * log(float(p)) + (1.0 - float(outcome)) * log(float(Decimal("1") - p)))
            log_losses.append(Decimal(str(value)))
        log_loss_value = _mean(tuple(log_losses))
        ece_value = _ece(bins, effective)

    coverage = Decimal(observed_count) / Decimal(len(terminal)) if terminal else Decimal("0")
    ambiguous_rate = Decimal(ambiguous_or_censored) / Decimal(observed_count) if observed_count else Decimal("0")
    companies = {forecast.company_id for forecast, _ in resolved_pairs}
    quarters = {_quarter_key(forecast.issued_at) for forecast, _ in resolved_pairs}
    band_count_map: dict[str, int] = {}
    for forecast, _ in resolved_pairs:
        band_count_map[forecast.displayed_band] = band_count_map.get(forecast.displayed_band, 0) + 1
    band_counts = tuple(sorted(band_count_map.items()))

    failures: list[str] = []
    if effective < policy.min_resolved_events:
        failures.append("MIN_RESOLVED_EVENTS")
    if len(companies) < policy.min_companies:
        failures.append("MIN_COMPANIES")
    if len(quarters) < policy.min_quarters:
        failures.append("MIN_QUARTERS")
    if not band_counts or any(count < policy.min_per_displayed_band for _, count in band_counts):
        failures.append("MIN_PER_DISPLAYED_BAND")
    if len(oos_brier_skill_windows) < policy.min_oos_windows or any(value <= 0 for value in oos_brier_skill_windows):
        failures.append("OOS_BRIER_SKILL")
    if ece_value is None or ece_value > policy.max_ece:
        failures.append("ECE")
    if ambiguous_rate > policy.max_ambiguous_censored_rate:
        failures.append("AMBIGUOUS_CENSORED_RATE")

    if not failures:
        status = CalibrationStatus.CALIBRATED
    elif prior_status in {CalibrationStatus.CALIBRATED, CalibrationStatus.DEGRADED}:
        status = CalibrationStatus.DEGRADED
    elif effective == 0:
        status = CalibrationStatus.UNCALIBRATED
    else:
        status = CalibrationStatus.CALIBRATING

    payload = {
        "cohort": f"{forecast_class}|{horizon}",
        "cutoff": cutoff.isoformat(),
        "raw": len(raw),
        "effective": effective,
        "companies": sorted(companies),
        "quarters": sorted(quarters),
        "band_counts": band_counts,
        "brier": str(brier),
        "bss": str(bss),
        "log_loss": str(log_loss_value),
        "ece": str(ece_value),
        "coverage": str(coverage),
        "ambiguous": str(ambiguous_rate),
        "oos": [str(value) for value in oos_brier_skill_windows],
        "mapping_version": mapping_version,
        "policy_version": policy.version,
        "status": status.value,
        "failures": failures,
    }
    snapshot_hash = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return CalibrationSnapshot(
        cohort_key=f"{forecast_class}|{horizon}",
        forecast_class=forecast_class,
        horizon=horizon,
        cutoff=cutoff,
        raw_sample_count=len(raw),
        effective_sample_count=effective,
        company_count=len(companies),
        quarter_count=len(quarters),
        band_counts=band_counts,
        brier_score=brier,
        brier_skill_score=bss,
        log_loss=log_loss_value,
        ece=ece_value,
        outcome_coverage=coverage,
        ambiguous_censored_rate=ambiguous_rate,
        reliability_bins=bins,
        oos_brier_skill_windows=oos_brier_skill_windows,
        mapping_version=mapping_version,
        policy_version=policy.version,
        status=status,
        gate_failures=tuple(failures),
        snapshot_hash=snapshot_hash,
    )


def load_calibration_policy(path: str | Path, *, cohort_key: str) -> CalibrationPolicy:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    version = str(payload.get("version") or "")
    defaults = dict(payload.get("defaults") or {})
    cohorts = payload.get("cohorts") or {}
    cohort = dict(cohorts.get(cohort_key) or {})
    merged = {**defaults, **cohort}
    if "base_rate" not in merged:
        raise ValueError(f"calibration policy has no base_rate for {cohort_key}")
    policy = CalibrationPolicy(
        version=version,
        base_rate=Decimal(str(merged["base_rate"])),
        min_resolved_events=int(merged.get("min_resolved_events", 200)),
        min_companies=int(merged.get("min_companies", 20)),
        min_quarters=int(merged.get("min_quarters", 8)),
        min_per_displayed_band=int(merged.get("min_per_displayed_band", 30)),
        min_oos_windows=int(merged.get("min_oos_windows", 2)),
        max_ece=Decimal(str(merged.get("max_ece", "0.08"))),
        max_ambiguous_censored_rate=Decimal(str(merged.get("max_ambiguous_censored_rate", "0.10"))),
        fixed_bin_edges=tuple(Decimal(str(item)) for item in merged.get("fixed_bin_edges", [0, 0.2, 0.4, 0.6, 0.8, 1])),
    )
    policy.validate()
    return policy
