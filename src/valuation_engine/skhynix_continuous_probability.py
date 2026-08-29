from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import csv
import gzip
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import fmean
from typing import Iterable

from .continuous_financial_path_probability import (
    ContinuousDriverDependence,
    ContinuousDriverPosterior,
    ScenarioFinancialPath,
    simulate_continuous_financial_paths,
)
from .continuous_probability_snapshot import (
    ContinuousOOSDriverDiagnostic,
    ContinuousProbabilityCalibrationSnapshot,
)
from .hierarchical_continuous_calibration import (
    ContinuousSummaryEvidence,
    NormalInverseGammaPosterior,
    build_hierarchical_continuous_posterior,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_PATH = (
    _REPO_ROOT / "data" / "skhynix_continuous_financial_cases_418.csv.gz"
)
EXPECTED_DATASET_SHA256 = "7d80665881a571c862722a6f0b7eada6184a5a3b6ca8131eea7954865b7e28e2"
COHORT_KEY = "semiconductor.memory|9y_path_from_12m_transitions|continuous_v1"
FORECAST_CLASS = "semiconductor.memory.continuous_financial_path"
HORIZON = "9y_path_from_12m_transitions"
METHOD_VERSION = "probability_engine_v3.2_continuous_financial_path_v1"
MAPPING_VERSION = "skhynix_continuous_joint_centroid_v1"
DEPENDENCE_VERSION = "skhynix_continuous_residual_correlation_v1"
DRIVER_IDS = (
    "revenue_growth",
    "operating_margin",
    "cash_conversion",
    "capex_intensity",
)
SCENARIOS = ("Down", "Core", "Bull")
FORECAST_YEARS = 9


@dataclass(frozen=True)
class TransitionFit:
    driver_id: str
    intercept: float
    slope: float
    x_lower: float
    x_upper: float
    y_lower: float
    y_upper: float
    residuals: tuple[float, ...]
    source_hash: str


@dataclass(frozen=True)
class HistoricalCase:
    ticker: str
    company: str
    subindustry: str
    origin: str
    outcome: str
    split: str
    x: tuple[tuple[str, float | None], ...]
    y: tuple[tuple[str, float], ...]
    origin_first_filed_on: str
    origin_final_source_ref: str
    outcome_first_filed_on: str
    outcome_final_source_ref: str
    origin_numeric_source_url: str
    origin_numeric_first_seen_at: str
    origin_numeric_snapshot_sha256: str
    outcome_numeric_source_url: str
    outcome_numeric_first_seen_at: str
    outcome_numeric_snapshot_sha256: str

    def x_for(self, driver_id: str) -> float | None:
        return dict(self.x)[driver_id]

    def y_for(self, driver_id: str) -> float:
        return dict(self.y)[driver_id]


@dataclass(frozen=True)
class CurrentConditioning:
    revenue_growth: Decimal
    operating_margin: Decimal
    cash_conversion: Decimal
    capex_intensity: Decimal
    source_ref: str
    first_seen_at: str
    source_hash: str

    def as_map(self) -> dict[str, Decimal]:
        return {
            "revenue_growth": self.revenue_growth,
            "operating_margin": self.operating_margin,
            "cash_conversion": self.cash_conversion,
            "capex_intensity": self.capex_intensity,
        }

    def validate(self) -> None:
        if not self.source_ref.startswith("http"):
            raise ValueError("current continuous conditioning requires an HTTP source")
        if not self.first_seen_at or not self.source_hash:
            raise ValueError("current continuous conditioning requires first-seen time and source hash")
        if any(not value.is_finite() for value in self.as_map().values()):
            raise ValueError("current continuous conditioning contains non-finite values")
        if self.capex_intensity < 0:
            raise ValueError("current capex intensity cannot be negative")


def _to_float(value: str, *, required: bool = True) -> float | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise ValueError("required continuous financial value is missing")
        return None
    number = float(text)
    if not math.isfinite(number):
        raise ValueError("continuous financial value must be finite")
    return number


def _ratio(value: str, *, required: bool = True) -> float | None:
    number = _to_float(value, required=required)
    return None if number is None else number / 100.0


def _case_from_row(row: dict[str, str]) -> HistoricalCase:
    origin_margin = _ratio(row["origin_opm"])
    origin_cash = _ratio(row["origin_fcfm"])
    origin_capex = _ratio(row["origin_capex_intensity"])
    if origin_margin is None or origin_cash is None or origin_capex is None:
        raise ValueError("required origin financial state is missing")
    revenue_growth = _ratio(row["revenue_change"])
    margin_delta = _ratio(row["opm_delta"])
    cash_delta = _ratio(row["fcfm_delta"])
    capex_delta = _ratio(row["capex_intensity_delta"])
    if any(value is None for value in (revenue_growth, margin_delta, cash_delta, capex_delta)):
        raise ValueError("required outcome financial transition is missing")
    return HistoricalCase(
        ticker=str(row["ticker"]).zfill(6),
        company=str(row["company"]),
        subindustry=str(row["primary_subindustry"]),
        origin=str(row["origin"]),
        outcome=str(row["outcome"]),
        split=str(row["split"]),
        x=(
            ("revenue_growth", _ratio(row["origin_revenue_yoy"], required=False)),
            ("operating_margin", origin_margin),
            ("cash_conversion", origin_cash),
            ("capex_intensity", origin_capex),
        ),
        y=(
            ("revenue_growth", float(revenue_growth)),
            ("operating_margin", origin_margin + float(margin_delta)),
            ("cash_conversion", origin_cash + float(cash_delta)),
            ("capex_intensity", origin_capex + float(capex_delta)),
        ),
        origin_first_filed_on=str(row["origin_first_filed_on"]),
        origin_final_source_ref=str(row["origin_final_source_ref"]),
        outcome_first_filed_on=str(row["outcome_first_filed_on"]),
        outcome_final_source_ref=str(row["outcome_final_source_ref"]),
        origin_numeric_source_url=str(row["origin_numeric_source_url"]),
        origin_numeric_first_seen_at=str(row["origin_numeric_first_seen_at"]),
        origin_numeric_snapshot_sha256=str(row["origin_numeric_snapshot_sha256"]),
        outcome_numeric_source_url=str(row["outcome_numeric_source_url"]),
        outcome_numeric_first_seen_at=str(row["outcome_numeric_first_seen_at"]),
        outcome_numeric_snapshot_sha256=str(row["outcome_numeric_snapshot_sha256"]),
    )


def _load_cases(
    path: str | Path = DEFAULT_DATASET_PATH,
) -> tuple[tuple[HistoricalCase, ...], str, tuple[str, ...]]:
    raw = Path(path).read_bytes()
    decompressed = gzip.decompress(raw)
    dataset_hash = sha256(decompressed).hexdigest()
    text = decompressed.decode("utf-8")
    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames is None:
        raise ValueError("continuous financial calibration dataset has no header")
    forbidden_legacy = {
        "demand_adverse",
        "margin_adverse",
        "cash_conversion_adverse",
        "capex_burden_adverse",
        "adverse_factor_count",
        "bull_joint_state",
        "scenario",
    }
    integrity: list[str] = []
    if dataset_hash != EXPECTED_DATASET_SHA256:
        integrity.append("DATASET_HASH_MISMATCH")
    leaked = sorted(forbidden_legacy.intersection(reader.fieldnames))
    if leaked:
        integrity.append("LEGACY_BINARY_SCENARIO_FIELDS_PRESENT:" + ",".join(leaked))
    rows: list[HistoricalCase] = []
    for index, row in enumerate(reader, start=2):
        try:
            case = _case_from_row(row)
        except Exception as exc:
            integrity.append(f"ROW_PARSE_FAILED:{index}:{type(exc).__name__}")
            continue
        rows.append(case)
        if case.ticker == "000660":
            integrity.append(f"TARGET_LEAKAGE:{case.origin}")
        if not (
            case.origin_final_source_ref.startswith("https://dart.fss.or.kr/")
            and case.outcome_final_source_ref.startswith("https://dart.fss.or.kr/")
        ):
            integrity.append(f"DART_PROVENANCE_MISSING:{case.ticker}:{case.origin}")
        if not (
            case.origin_numeric_source_url.startswith("http")
            and case.outcome_numeric_source_url.startswith("http")
            and case.origin_numeric_first_seen_at
            and case.outcome_numeric_first_seen_at
            and case.origin_numeric_snapshot_sha256
            and case.outcome_numeric_snapshot_sha256
        ):
            integrity.append(f"NUMERIC_SNAPSHOT_PROVENANCE_MISSING:{case.ticker}:{case.origin}")
        if case.origin_first_filed_on > case.outcome_first_filed_on:
            integrity.append(f"PUBLICATION_ORDER_INVALID:{case.ticker}:{case.origin}")
    if len(rows) != 418:
        integrity.append(f"CASE_COUNT_MISMATCH:{len(rows)}")
    if len({case.company for case in rows}) != 29:
        integrity.append("COMPANY_BREADTH_MISMATCH")
    return tuple(rows), dataset_hash, tuple(dict.fromkeys(integrity))


def _quantile(values: Iterable[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile requires values")
    if len(ordered) == 1:
        return ordered[0]
    location = (len(ordered) - 1) * q
    low = int(math.floor(location))
    high = int(math.ceil(location))
    if low == high:
        return ordered[low]
    weight = location - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _clip(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def _fit_transition(
    cases: tuple[HistoricalCase, ...],
    driver_id: str,
) -> TransitionFit:
    pairs = tuple(
        (case.x_for(driver_id), case.y_for(driver_id))
        for case in cases
        if case.x_for(driver_id) is not None
    )
    if len(pairs) < 20:
        raise ValueError(f"continuous transition {driver_id} has insufficient observations")
    xs = tuple(float(x) for x, _ in pairs if x is not None)
    ys = tuple(float(y) for _, y in pairs)
    x_lower, x_upper = _quantile(xs, 0.01), _quantile(xs, 0.99)
    y_lower, y_upper = _quantile(ys, 0.01), _quantile(ys, 0.99)
    clipped_x = tuple(_clip(value, x_lower, x_upper) for value in xs)
    clipped_y = tuple(_clip(value, y_lower, y_upper) for value in ys)
    mean_x = fmean(clipped_x)
    mean_y = fmean(clipped_y)
    denominator = sum((value - mean_x) ** 2 for value in clipped_x)
    slope = (
        sum(
            (x_value - mean_x) * (y_value - mean_y)
            for x_value, y_value in zip(clipped_x, clipped_y)
        )
        / denominator
        if denominator > 1e-15
        else 0.0
    )
    intercept = mean_y - slope * mean_x
    residuals = tuple(
        y_value - (intercept + slope * x_value)
        for x_value, y_value in zip(clipped_x, clipped_y)
    )
    payload = {
        "driver_id": driver_id,
        "n": len(pairs),
        "intercept": intercept,
        "slope": slope,
        "bounds": [x_lower, x_upper, y_lower, y_upper],
        "residual_summary": [
            fmean(residuals),
            sum(value * value for value in residuals),
        ],
    }
    return TransitionFit(
        driver_id=driver_id,
        intercept=intercept,
        slope=slope,
        x_lower=x_lower,
        x_upper=x_upper,
        y_lower=y_lower,
        y_upper=y_upper,
        residuals=residuals,
        source_hash=sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    )


def _continuous_oos_skill(
    cases: tuple[HistoricalCase, ...],
    driver_id: str,
) -> tuple[Decimal, ...]:
    windows = (
        ("VALIDATION", ("TRAIN",)),
        ("HOLDOUT", ("TRAIN", "VALIDATION")),
        ("FINAL_OOS", ("TRAIN", "VALIDATION", "HOLDOUT")),
    )
    values: list[Decimal] = []
    for test_split, training_splits in windows:
        training = tuple(case for case in cases if case.split in training_splits)
        test = tuple(
            case
            for case in cases
            if case.split == test_split and case.x_for(driver_id) is not None
        )
        fit = _fit_transition(training, driver_id)
        if not test:
            values.append(Decimal("0"))
            continue
        model_errors: list[float] = []
        baseline_errors: list[float] = []
        for case in test:
            x_value = case.x_for(driver_id)
            if x_value is None:
                continue
            y_value = case.y_for(driver_id)
            prediction = fit.intercept + fit.slope * x_value
            model_errors.append((prediction - y_value) ** 2)
            baseline_errors.append((x_value - y_value) ** 2)
        model_mse = fmean(model_errors)
        baseline_mse = fmean(baseline_errors)
        skill = 0.0 if baseline_mse <= 1e-15 else 1.0 - model_mse / baseline_mse
        values.append(Decimal(str(skill)))
    return tuple(values)


def _saturation(observed: int, target: int) -> float:
    return 0.0 if observed <= 0 else 1.0 - math.exp(-observed / target)


def _regime_similarity(
    cases: tuple[HistoricalCase, ...],
    driver_id: str,
    current: float,
) -> Decimal:
    values = tuple(
        float(value)
        for case in cases
        for value in (case.x_for(driver_id),)
        if value is not None
    )
    lower, upper = _quantile(values, 0.01), _quantile(values, 0.99)
    clipped = tuple(_clip(value, lower, upper) for value in values)
    mean = fmean(clipped)
    variance = sum((value - mean) ** 2 for value in clipped) / max(1, len(clipped) - 1)
    scale = math.sqrt(max(variance, 1e-12))
    z_score = abs(current - mean) / scale
    return Decimal(str(math.exp(-0.5 * (z_score / 2.0) ** 2)))


def _continuous_weight(
    *,
    skill_windows: tuple[Decimal, ...],
    resolved_cases: int,
    company_count: int,
    quarter_count: int,
    regime_similarity: Decimal,
) -> tuple[Decimal, Decimal]:
    mean_skill = float(sum(skill_windows, Decimal("0")) / Decimal(len(skill_windows)))
    skill_component = 1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, mean_skill / 0.15))))
    raw = (
        0.35 * skill_component
        + 0.20 * _saturation(resolved_cases, 100)
        + 0.10 * _saturation(company_count, 20)
        + 0.10 * _saturation(quarter_count, 8)
        + 0.10 * _saturation(len(skill_windows), 4)
        + 0.15 * float(regime_similarity)
    )
    weight = Decimal(str(min(1.0, max(0.05, 0.05 + 0.95 * raw))))
    uncertainty = Decimal("1") + (Decimal("1") - weight) * Decimal("2")
    return weight, uncertainty


def _summary_evidence(
    residuals: tuple[float, ...],
    *,
    likelihood_weight: Decimal,
) -> ContinuousSummaryEvidence:
    if not residuals:
        raise ValueError("continuous residual summary requires observations")
    mean = fmean(residuals)
    sum_squared = sum((value - mean) ** 2 for value in residuals)
    return ContinuousSummaryEvidence(
        sample_count=len(residuals),
        sample_mean=Decimal(str(mean)),
        sum_squared_deviations=Decimal(str(sum_squared)),
        likelihood_weight=likelihood_weight,
        integrity_passed=True,
    )


def _weak_root_prior() -> NormalInverseGammaPosterior:
    return NormalInverseGammaPosterior(
        mean=Decimal("0"),
        mean_strength=Decimal("0.1"),
        shape=Decimal("2.1"),
        scale=Decimal("0.21"),
    )


def _partial_pooled_residual_posterior(
    all_cases: tuple[HistoricalCase, ...],
    driver_id: str,
    current_value: Decimal,
) -> tuple[TransitionFit, object, ContinuousOOSDriverDiagnostic]:
    parent_cases = tuple(case for case in all_cases if case.subindustry != "memory")
    child_cases = tuple(case for case in all_cases if case.subindustry == "memory")
    fit = _fit_transition(parent_cases, driver_id)
    skill_windows = _continuous_oos_skill(all_cases, driver_id)
    available = tuple(case for case in all_cases if case.x_for(driver_id) is not None)
    regime = _regime_similarity(
        all_cases, driver_id, float(current_value)
    )
    weight, uncertainty = _continuous_weight(
        skill_windows=skill_windows,
        resolved_cases=len(available),
        company_count=len({case.company for case in available}),
        quarter_count=len({case.origin for case in available}),
        regime_similarity=regime,
    )

    parent_residuals = fit.residuals
    child_residuals: list[float] = []
    for case in child_cases:
        x_value = case.x_for(driver_id)
        if x_value is None:
            continue
        x_clipped = _clip(x_value, fit.x_lower, fit.x_upper)
        y_clipped = _clip(case.y_for(driver_id), fit.y_lower, fit.y_upper)
        child_residuals.append(
            y_clipped - (fit.intercept + fit.slope * x_clipped)
        )
    child_company_count = len(
        {
            case.company
            for case in child_cases
            if case.x_for(driver_id) is not None
        }
    )
    breadth = math.sqrt(
        _saturation(len(child_residuals), 50)
        * _saturation(child_company_count, 5)
    )
    child_weight = max(
        Decimal("0.05"),
        weight * Decimal(str(breadth)),
    )
    nodes = [
        (
            "GLOBAL_NON_MEMORY_PARENT",
            _summary_evidence(parent_residuals, likelihood_weight=weight),
        )
    ]
    if child_residuals:
        nodes.append(
            (
                "MEMORY_SMALL_SAMPLE_PARTIAL_POOL",
                _summary_evidence(
                    tuple(child_residuals),
                    likelihood_weight=child_weight,
                ),
            )
        )
    hierarchical = build_hierarchical_continuous_posterior(
        driver_id=driver_id,
        root_prior=_weak_root_prior(),
        hierarchy_nodes=tuple(nodes),
    )
    diagnostic = ContinuousOOSDriverDiagnostic(
        driver_id=driver_id,
        skill_windows=skill_windows,
        likelihood_weight=weight,
        uncertainty_inflation=uncertainty,
        resolved_cases=len(available),
        company_count=len({case.company for case in available}),
        quarter_count=len({case.origin for case in available}),
        regime_similarity=regime,
    )
    return fit, hierarchical, diagnostic


def _driver_path(
    *,
    fit: TransitionFit,
    hierarchical: object,
    diagnostic: ContinuousOOSDriverDiagnostic,
    current_value: Decimal,
    all_cases: tuple[HistoricalCase, ...],
) -> ContinuousDriverPosterior:
    posterior = hierarchical.posterior
    residual_mean = float(posterior.predictive_mean)
    residual_scale = float(posterior.predictive_scale)
    uncertainty_inflation = float(diagnostic.uncertainty_inflation)
    mean_uncertainty_one_year = (
        residual_scale
        / math.sqrt(max(float(posterior.mean_strength), 1e-12))
        * uncertainty_inflation
    )
    mean_path: list[Decimal] = []
    scale_path: list[Decimal] = []
    mean_uncertainty_path: list[Decimal] = []
    state = float(current_value)
    variance_multiplier = 0.0
    for year in range(1, FORECAST_YEARS + 1):
        state = fit.intercept + fit.slope * state + residual_mean
        variance_multiplier += fit.slope ** (2 * (year - 1))
        mean_path.append(Decimal(str(state)))
        scale_path.append(
            Decimal(
                str(
                    residual_scale
                    * math.sqrt(max(variance_multiplier, 1e-12))
                    * uncertainty_inflation
                )
            )
        )
        mean_uncertainty_path.append(
            Decimal(str(mean_uncertainty_one_year * math.sqrt(year)))
        )

    outcomes = tuple(case.y_for(fit.driver_id) for case in all_cases)
    historical_low = _quantile(outcomes, 0.001)
    historical_high = _quantile(outcomes, 0.999)
    low = min(historical_low, float(current_value))
    high = max(historical_high, float(current_value))
    span = max(high - low, 0.10)
    lower = low - 0.25 * span
    upper = high + 0.25 * span
    if fit.driver_id == "capex_intensity":
        lower = max(0.0, lower)
    source_hash = sha256(
        json.dumps(
            {
                "transition": fit.source_hash,
                "hierarchical_snapshot": hierarchical.snapshot_hash,
                "diagnostic": {
                    "skill": [str(value) for value in diagnostic.skill_windows],
                    "weight": str(diagnostic.likelihood_weight),
                    "inflation": str(diagnostic.uncertainty_inflation),
                    "regime": str(diagnostic.regime_similarity),
                },
                "current_value": str(current_value),
                "mean_path": [str(value) for value in mean_path],
                "scale_path": [str(value) for value in scale_path],
                "mean_uncertainty_path": [
                    str(value) for value in mean_uncertainty_path
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return ContinuousDriverPosterior(
        driver_id=fit.driver_id,
        mean_path=tuple(mean_path),
        scale_path=tuple(scale_path),
        mean_uncertainty_path=tuple(mean_uncertainty_path),
        source_hash=source_hash,
        lower_bound=Decimal(str(lower)),
        upper_bound=Decimal(str(upper)),
    )


def _score_and_cluster(
    cases: tuple[HistoricalCase, ...],
) -> tuple[tuple[str, tuple[tuple[str, float], ...]], ...]:
    beneficial = ("revenue_growth", "operating_margin", "cash_conversion")
    centers_by_driver: dict[str, tuple[float, float]] = {}
    for driver_id in beneficial:
        values = tuple(case.y_for(driver_id) for case in cases)
        low, high = _quantile(values, 0.01), _quantile(values, 0.99)
        clipped = tuple(_clip(value, low, high) for value in values)
        center = fmean(clipped)
        variance = sum((value - center) ** 2 for value in clipped) / max(
            1, len(clipped) - 1
        )
        centers_by_driver[driver_id] = (
            center,
            math.sqrt(max(variance, 1e-12)),
        )
    scores = []
    for case in cases:
        score = fmean(
            (
                case.y_for(driver_id) - centers_by_driver[driver_id][0]
            )
            / centers_by_driver[driver_id][1]
            for driver_id in beneficial
        )
        scores.append(score)
    centers = [
        _quantile(scores, 0.20),
        _quantile(scores, 0.50),
        _quantile(scores, 0.80),
    ]
    assignments = [0] * len(scores)
    for _ in range(100):
        new_assignments = [
            min(range(3), key=lambda idx: (abs(score - centers[idx]), idx))
            for score in scores
        ]
        new_centers = []
        for idx in range(3):
            members = [
                score
                for score, assignment in zip(scores, new_assignments)
                if assignment == idx
            ]
            if not members:
                raise ValueError("continuous scenario clustering produced an empty cluster")
            new_centers.append(fmean(members))
        if new_assignments == assignments and all(
            abs(left - right) <= 1e-12
            for left, right in zip(new_centers, centers)
        ):
            assignments = new_assignments
            centers = new_centers
            break
        assignments = new_assignments
        centers = new_centers
    ordered_clusters = sorted(range(3), key=lambda idx: centers[idx])
    result = []
    for scenario_id, cluster_id in zip(SCENARIOS, ordered_clusters):
        members = [
            case
            for case, assignment in zip(cases, assignments)
            if assignment == cluster_id
        ]
        centroid = tuple(
            (driver_id, fmean(case.y_for(driver_id) for case in members))
            for driver_id in DRIVER_IDS
        )
        result.append((scenario_id, centroid))
    return tuple(result)


def _scenario_paths(
    *,
    cases: tuple[HistoricalCase, ...],
    fits: dict[str, TransitionFit],
    residual_means: dict[str, float],
) -> tuple[ScenarioFinancialPath, ...]:
    clusters = _score_and_cluster(cases)
    scenarios: list[ScenarioFinancialPath] = []
    for scenario_id, centroid_pairs in clusters:
        centroid = dict(centroid_pairs)
        paths: list[tuple[str, tuple[Decimal, ...]]] = []
        for driver_id in DRIVER_IDS:
            fit = fits[driver_id]
            state = centroid[driver_id]
            values = [Decimal(str(state))]
            for _ in range(1, FORECAST_YEARS):
                state = (
                    fit.intercept
                    + fit.slope * state
                    + residual_means[driver_id]
                )
                values.append(Decimal(str(state)))
            paths.append((driver_id, tuple(values)))
        scenarios.append(
            ScenarioFinancialPath(
                scenario_id=scenario_id,
                driver_paths=tuple(paths),
                driver_weights=tuple(
                    (driver_id, Decimal("1")) for driver_id in DRIVER_IDS
                ),
            )
        )
    return tuple(scenarios)


def _correlation_matrix(
    *,
    cases: tuple[HistoricalCase, ...],
    fits: dict[str, TransitionFit],
    shrink_weight: Decimal,
) -> tuple[tuple[Decimal, ...], ...]:
    complete = tuple(
        case
        for case in cases
        if all(case.x_for(driver_id) is not None for driver_id in DRIVER_IDS)
    )
    residual_rows: list[tuple[float, ...]] = []
    for case in complete:
        values: list[float] = []
        for driver_id in DRIVER_IDS:
            fit = fits[driver_id]
            x_value = case.x_for(driver_id)
            if x_value is None:
                raise AssertionError("complete residual row lost a driver")
            x_clipped = _clip(x_value, fit.x_lower, fit.x_upper)
            y_clipped = _clip(
                case.y_for(driver_id), fit.y_lower, fit.y_upper
            )
            values.append(
                y_clipped - (fit.intercept + fit.slope * x_clipped)
            )
        residual_rows.append(tuple(values))
    if len(residual_rows) < 20:
        raise ValueError("continuous dependence requires at least 20 complete residual rows")
    columns = list(zip(*residual_rows))
    means = [fmean(column) for column in columns]
    stds = []
    for column, mean in zip(columns, means):
        variance = sum((value - mean) ** 2 for value in column) / max(
            1, len(column) - 1
        )
        stds.append(math.sqrt(max(variance, 1e-12)))
    matrix: list[tuple[Decimal, ...]] = []
    shrink = float(shrink_weight)
    for i, left in enumerate(columns):
        row = []
        for j, right in enumerate(columns):
            if i == j:
                correlation = 1.0
            else:
                covariance = sum(
                    (a - means[i]) * (b - means[j])
                    for a, b in zip(left, right)
                ) / max(1, len(residual_rows) - 1)
                empirical = covariance / (stds[i] * stds[j])
                correlation = shrink * empirical
            row.append(Decimal(str(correlation)))
        matrix.append(tuple(row))
    return tuple(matrix)


def build_skhynix_continuous_probability_snapshot(
    *,
    current: CurrentConditioning,
    as_of_date: str,
    dataset_path: str | Path = DEFAULT_DATASET_PATH,
) -> ContinuousProbabilityCalibrationSnapshot:
    """Build the target-excluded v3.2 continuous financial-path calibration.

    No target market price, Street target, intrinsic value, desired return, or entry
    price is accepted by this contract. Historical binary event/scenario columns are
    rejected at ingestion and never reach the Monte Carlo engine.
    """
    current.validate()
    cases, dataset_hash, integrity = _load_cases(dataset_path)
    current_map = current.as_map()
    fits: dict[str, TransitionFit] = {}
    hierarchical: dict[str, object] = {}
    diagnostics: list[ContinuousOOSDriverDiagnostic] = []
    drivers: list[ContinuousDriverPosterior] = []
    for driver_id in DRIVER_IDS:
        fit, posterior, diagnostic = _partial_pooled_residual_posterior(
            cases,
            driver_id,
            current_map[driver_id],
        )
        fits[driver_id] = fit
        hierarchical[driver_id] = posterior
        diagnostics.append(diagnostic)
        drivers.append(
            _driver_path(
                fit=fit,
                hierarchical=posterior,
                diagnostic=diagnostic,
                current_value=current_map[driver_id],
                all_cases=cases,
            )
        )
    residual_means = {
        driver_id: float(hierarchical[driver_id].posterior.predictive_mean)
        for driver_id in DRIVER_IDS
    }
    scenarios = _scenario_paths(
        cases=cases,
        fits=fits,
        residual_means=residual_means,
    )
    average_weight = sum(
        (item.likelihood_weight for item in diagnostics), Decimal("0")
    ) / Decimal(len(diagnostics))
    correlation_matrix = _correlation_matrix(
        cases=cases,
        fits=fits,
        shrink_weight=average_weight,
    )
    dependence_payload = {
        "version": DEPENDENCE_VERSION,
        "driver_ids": DRIVER_IDS,
        "correlation_matrix": [
            [str(value) for value in row] for row in correlation_matrix
        ],
        "student_t_df": 6,
    }
    dependence_hash = sha256(
        json.dumps(
            dependence_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    dependence = ContinuousDriverDependence(
        version=f"{DEPENDENCE_VERSION}:{dependence_hash[:12]}",
        driver_ids=DRIVER_IDS,
        correlation_matrix=correlation_matrix,
        student_t_df=6,
    )
    simulation = simulate_continuous_financial_paths(
        drivers=tuple(drivers),
        scenarios=scenarios,
        dependence=dependence,
        credible_level=Decimal("0.90"),
        outer_draws=300,
        inner_draws=200,
        seed=20260829,
    )
    driver_hashes = tuple(
        (driver.driver_id, driver.source_hash) for driver in drivers
    )
    return ContinuousProbabilityCalibrationSnapshot.build(
        cohort_key=COHORT_KEY,
        forecast_class=FORECAST_CLASS,
        horizon=HORIZON,
        as_of_date=as_of_date,
        method_version=METHOD_VERSION,
        mapping_version=MAPPING_VERSION,
        estimates=simulation.estimates,
        driver_snapshot_hashes=driver_hashes,
        dependence_hash=dependence_hash,
        simulation_hash=simulation.simulation_hash,
        dataset_hash=dataset_hash,
        oos_diagnostics=tuple(diagnostics),
        integrity_findings=integrity,
    )
