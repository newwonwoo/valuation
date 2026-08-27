from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any

from .ledger import EvidenceLedger
from .probability_calibration import (
    ForecastOutcome,
    ProbabilityCalibrationLedger,
    ProbabilityForecast,
)
from .records import (
    CalibrationStatus,
    EvidenceSourceLayer,
    EvidenceStatus,
    HypothesisRecord,
)


_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")
_DISPLAY_STEP = Decimal("0.05")


@dataclass(frozen=True)
class ScenarioLikelihoodInput:
    scenario_id: str
    relative_score: Decimal
    rationale: str
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.scenario_id or not self.rationale:
            raise ValueError("scenario likelihood requires scenario_id and rationale")
        if not self.relative_score.is_finite() or self.relative_score <= 0:
            raise ValueError("scenario relative_score must be finite and positive")
        if not self.supporting_evidence_ids:
            raise ValueError("scenario likelihood requires supporting Evidence IDs")


@dataclass(frozen=True)
class ScenarioLikelihoodSpec:
    forecast_class: str
    horizon: str
    as_of_date: str
    method_version: str
    inputs: tuple[ScenarioLikelihoodInput, ...]

    def validate(self) -> None:
        if not all(
            (self.forecast_class, self.horizon, self.as_of_date, self.method_version)
        ):
            raise ValueError("scenario likelihood spec is incomplete")
        date.fromisoformat(self.as_of_date)
        if not self.inputs:
            raise ValueError("scenario likelihood spec requires inputs")
        for item in self.inputs:
            item.validate()
        ids = tuple(item.scenario_id for item in self.inputs)
        if len(ids) != len(set(ids)):
            raise ValueError("scenario likelihood scenario IDs must be unique")


@dataclass(frozen=True)
class ScenarioProbabilityRow:
    scenario_id: str
    relative_score: Decimal
    probability: Decimal
    displayed_probability: Decimal
    rationale: str
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioProbabilityAssessment:
    forecast_class: str
    horizon: str
    as_of_date: str
    method_version: str
    status: CalibrationStatus
    numeric_weighting_allowed: bool
    rows: tuple[ScenarioProbabilityRow, ...]
    assessment_hash: str


def calculate_scenario_probability_assessment(
    spec: ScenarioLikelihoodSpec,
    *,
    scenario_ids: tuple[str, ...],
    ledger: EvidenceLedger,
) -> ScenarioProbabilityAssessment:
    """Normalize explicit analyst relative scores without authorizing valuation weights.

    The relative scores and rationales are analyst inputs. Deterministic code validates their
    frozen Evidence lineage, normalizes them, and rounds only the displayed distribution to
    five-percentage-point bands. The resulting assessment remains UNCALIBRATED and cannot
    populate ``BoundScenarioSet.probability`` or expected intrinsic value.
    """

    spec.validate()
    proposed_ids = tuple(item.scenario_id for item in spec.inputs)
    if set(proposed_ids) != set(scenario_ids) or len(proposed_ids) != len(
        scenario_ids
    ):
        raise ValueError(
            "scenario likelihood inputs must match the bound scenario IDs exactly"
        )

    by_id = {item.scenario_id: item for item in spec.inputs}
    ordered = tuple(by_id[scenario_id] for scenario_id in scenario_ids)
    for item in ordered:
        for evidence_id in (
            *item.supporting_evidence_ids,
            *item.contradicting_evidence_ids,
        ):
            evidence = ledger.get(evidence_id)
            if evidence.source_layer is EvidenceSourceLayer.MARKET_COMPARISON:
                raise ValueError(
                    "target-market Evidence cannot enter pre-freeze scenario likelihood"
                )

    total_score = sum((item.relative_score for item in ordered), Decimal("0"))
    probabilities = tuple(item.relative_score / total_score for item in ordered)
    displayed = _round_distribution(probabilities)
    rows = tuple(
        ScenarioProbabilityRow(
            scenario_id=item.scenario_id,
            relative_score=item.relative_score,
            probability=probability,
            displayed_probability=display_probability,
            rationale=item.rationale,
            supporting_evidence_ids=item.supporting_evidence_ids,
            contradicting_evidence_ids=item.contradicting_evidence_ids,
        )
        for item, probability, display_probability in zip(
            ordered, probabilities, displayed
        )
    )
    serialized = json.dumps(
        {
            "forecast_class": spec.forecast_class,
            "horizon": spec.horizon,
            "as_of_date": spec.as_of_date,
            "method_version": spec.method_version,
            "status": CalibrationStatus.UNCALIBRATED.value,
            "numeric_weighting_allowed": False,
            "rows": [
                {
                    "scenario_id": row.scenario_id,
                    "relative_score": str(row.relative_score),
                    "probability": str(row.probability),
                    "displayed_probability": str(row.displayed_probability),
                    "rationale": row.rationale,
                    "supporting_evidence_ids": row.supporting_evidence_ids,
                    "contradicting_evidence_ids": row.contradicting_evidence_ids,
                }
                for row in rows
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ScenarioProbabilityAssessment(
        forecast_class=spec.forecast_class,
        horizon=spec.horizon,
        as_of_date=spec.as_of_date,
        method_version=spec.method_version,
        status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        rows=rows,
        assessment_hash=sha256(serialized.encode("utf-8")).hexdigest(),
    )


def _round_distribution(
    probabilities: tuple[Decimal, ...],
) -> tuple[Decimal, ...]:
    units_total = int(Decimal("1") / _DISPLAY_STEP)
    raw_units = tuple(probability / _DISPLAY_STEP for probability in probabilities)
    floor_units = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in raw_units]
    remaining = units_total - sum(floor_units)
    order = sorted(
        range(len(raw_units)),
        key=lambda index: (raw_units[index] - floor_units[index], -index),
        reverse=True,
    )
    for index in order[:remaining]:
        floor_units[index] += 1
    result = tuple(Decimal(value) * _DISPLAY_STEP for value in floor_units)
    if sum(result, Decimal("0")) != Decimal("1"):
        raise AssertionError("displayed scenario probability distribution must sum to one")
    return result


@dataclass(frozen=True)
class ProbabilityForecastDeclaration:
    hypothesis_id: str
    event_key: str
    forecast_class: str
    horizon: str
    event_definition: str
    evaluation_deadline: date
    model_version: str
    resolution_rule: str
    resolution_source_policy: str

    def validate(self) -> None:
        if not all(
            (
                self.hypothesis_id,
                self.event_key,
                self.forecast_class,
                self.horizon,
                self.event_definition,
                self.model_version,
                self.resolution_rule,
                self.resolution_source_policy,
            )
        ):
            raise ValueError("probability forecast declaration is incomplete")


@dataclass(frozen=True)
class ProbabilityForecastDraft:
    hypothesis_id: str
    event_key: str
    company_id: str
    forecast_class: str
    horizon: str
    event_definition: str
    evaluation_deadline: date
    probability: Decimal
    displayed_band: str
    evidence_snapshot_hash: str
    model_version: str
    resolution_rule: str
    resolution_source_policy: str
    rationale: str
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...]


def build_probability_forecast_drafts(
    declarations: tuple[ProbabilityForecastDeclaration, ...],
    *,
    hypotheses: tuple[HypothesisRecord, ...],
    company_id: str,
    evidence_snapshot_hash: str,
    ledger: EvidenceLedger,
) -> tuple[ProbabilityForecastDraft, ...]:
    if not declarations:
        return ()
    if not company_id or not evidence_snapshot_hash:
        raise ValueError("forecast drafts require company and Evidence snapshot identity")
    hypothesis_map = {item.id: item for item in hypotheses}
    if len(hypothesis_map) != len(hypotheses):
        raise ValueError("forecast drafts require unique hypothesis IDs")
    event_keys: set[str] = set()
    declared_hypotheses: set[str] = set()
    drafts: list[ProbabilityForecastDraft] = []
    for declaration in declarations:
        declaration.validate()
        if declaration.event_key in event_keys:
            raise ValueError("forecast declarations require unique event_key values")
        if declaration.hypothesis_id in declared_hypotheses:
            raise ValueError("one hypothesis may produce only one forecast per run")
        event_keys.add(declaration.event_key)
        declared_hypotheses.add(declaration.hypothesis_id)
        hypothesis = hypothesis_map.get(declaration.hypothesis_id)
        if hypothesis is None:
            raise ValueError(
                f"forecast declaration references unknown hypothesis: {declaration.hypothesis_id}"
            )
        probability = Decimal(str(hypothesis.probability))
        if not Decimal("0") < probability < Decimal("1"):
            raise ValueError("unresolved production forecast probability must be within (0,1)")
        if hypothesis.calibration_status is CalibrationStatus.CALIBRATED:
            raise ValueError(
                "new production forecast capture must preserve its pre-resolution raw probability"
            )
        for evidence_id in (
            *hypothesis.supporting_evidence_ids,
            *hypothesis.contradicting_evidence_ids,
        ):
            evidence = ledger.get(evidence_id)
            if evidence.source_layer is EvidenceSourceLayer.MARKET_COMPARISON:
                raise ValueError("market comparison Evidence cannot support a forecast")
        displayed = (probability / _DISPLAY_STEP).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        ) * _DISPLAY_STEP
        drafts.append(
            ProbabilityForecastDraft(
                hypothesis_id=hypothesis.id,
                event_key=declaration.event_key,
                company_id=company_id,
                forecast_class=declaration.forecast_class,
                horizon=declaration.horizon,
                event_definition=declaration.event_definition,
                evaluation_deadline=declaration.evaluation_deadline,
                probability=probability,
                displayed_band=f"{displayed * 100:.0f}%",
                evidence_snapshot_hash=evidence_snapshot_hash,
                model_version=declaration.model_version,
                resolution_rule=declaration.resolution_rule,
                resolution_source_policy=declaration.resolution_source_policy,
                rationale=hypothesis.statement,
                supporting_evidence_ids=hypothesis.supporting_evidence_ids,
                contradicting_evidence_ids=hypothesis.contradicting_evidence_ids,
            )
        )
    return tuple(drafts)


@dataclass(frozen=True)
class ProbabilityForecastRunRef:
    ticker: str
    run_id: str
    path: str
    content_hash: str
    recorded_at: str
    forecast_ids: tuple[str, ...]


class ProbabilityForecastHistoryStore:
    """Append-only production forecast/outcome store outside the public repository."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save_forecast_run(
        self,
        *,
        ticker: str,
        run_id: str,
        drafts: tuple[ProbabilityForecastDraft, ...],
        recorded_at: datetime | None = None,
    ) -> ProbabilityForecastRunRef:
        if not drafts:
            raise ValueError("probability forecast run requires at least one draft")
        safe_ticker = self._safe(ticker)
        safe_run = self._safe(run_id)
        directory = self.root / "calibration" / "forecast-runs" / safe_ticker
        target = directory / f"{safe_run}.json"
        if target.exists():
            raise FileExistsError(f"probability forecast run is immutable: {run_id}")
        issued_at = recorded_at or datetime.now(timezone.utc)
        if issued_at.tzinfo is None or issued_at.utcoffset() is None:
            raise ValueError("forecast recorded_at must be timezone-aware")

        ledger = self.load_ledger(ticker)
        superseded = {
            item.supersedes_id
            for item in ledger.forecasts
            if item.supersedes_id is not None
        }
        terminal_by_event = {
            item.event_key: item
            for item in ledger.forecasts
            if item.forecast_id not in superseded
        }
        forecasts: list[ProbabilityForecast] = []
        for draft in drafts:
            forecast_id = f"{run_id}:{draft.hypothesis_id}"
            prior = terminal_by_event.get(draft.event_key)
            forecast = ProbabilityForecast(
                forecast_id=forecast_id,
                event_key=draft.event_key,
                hypothesis_id=draft.hypothesis_id,
                company_id=draft.company_id,
                forecast_class=draft.forecast_class,
                horizon=draft.horizon,
                event_definition=draft.event_definition,
                issued_at=issued_at,
                evaluation_deadline=draft.evaluation_deadline,
                probability=draft.probability,
                displayed_band=draft.displayed_band,
                evidence_snapshot_hash=draft.evidence_snapshot_hash,
                model_version=draft.model_version,
                resolution_rule=draft.resolution_rule,
                resolution_source_policy=draft.resolution_source_policy,
                supersedes_id=prior.forecast_id if prior is not None else None,
                first_seen_at=issued_at,
            )
            ledger.append_forecast(forecast)
            terminal_by_event[draft.event_key] = forecast
            forecasts.append(forecast)

        payload = {
            "schema_version": "1.0",
            "ticker": ticker,
            "run_id": run_id,
            "recorded_at": issued_at.isoformat(),
            "forecasts": [_forecast_payload(item) for item in forecasts],
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")
        digest = sha256(encoded).hexdigest()
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f".{safe_run}.tmp"
        temporary.write_bytes(encoded)
        os.replace(temporary, target)
        return ProbabilityForecastRunRef(
            ticker=ticker,
            run_id=run_id,
            path=str(target),
            content_hash=digest,
            recorded_at=issued_at.isoformat(),
            forecast_ids=tuple(item.forecast_id for item in forecasts),
        )

    def append_outcome(
        self,
        *,
        ticker: str,
        outcome: ForecastOutcome,
        evidence_ledger: EvidenceLedger,
    ) -> str:
        safe_ticker = self._safe(ticker)
        if outcome.first_seen_at is None:
            raise ValueError(
                "production probability outcome requires explicit first_seen_at"
            )
        if not outcome.outcome_evidence_ids:
            raise ValueError(
                "production probability outcome requires primary outcome Evidence IDs"
            )
        allowed_layers = {
            EvidenceSourceLayer.REALIZED_OR_FILING,
            EvidenceSourceLayer.POLICY_PRIMARY_SOURCE,
        }
        active_ids = {item.id for item in evidence_ledger.active()}
        outcome_evidence = []
        for evidence_id in outcome.outcome_evidence_ids:
            evidence = evidence_ledger.get(evidence_id)
            if (
                evidence.id not in active_ids
                or evidence.status is not EvidenceStatus.ACTIVE
            ):
                raise ValueError("production outcome Evidence must be active")
            if evidence.source_layer not in allowed_layers:
                raise ValueError(
                    "production outcome Evidence must be realized/filing or policy primary"
                )
            if not evidence.source_ref.startswith(("https://", "http://")):
                raise ValueError(
                    "production outcome Evidence requires a directly verifiable HTTP(S) source"
                )
            outcome_evidence.append(evidence)
        ledger = self.load_ledger(ticker)
        ledger.append_outcome(outcome)
        filename = sha256(outcome.forecast_id.encode("utf-8")).hexdigest() + ".json"
        directory = self.root / "calibration" / "outcomes" / safe_ticker
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / filename
        if target.exists():
            raise FileExistsError(
                f"probability outcome is immutable: {outcome.forecast_id}"
            )
        payload = {
            "schema_version": "1.0",
            "ticker": ticker,
            "outcome": _outcome_payload(outcome),
            "outcome_evidence": [
                {
                    "id": item.id,
                    "metric": item.metric,
                    "effective_date": item.effective_date,
                    "observed_date": item.observed_date,
                    "source_layer": item.source_layer.value,
                    "source_name": item.source_name,
                    "source_ref": item.source_ref,
                }
                for item in outcome_evidence
            ],
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")
        temporary = directory / f".{filename}.tmp"
        temporary.write_bytes(encoded)
        os.replace(temporary, target)
        return str(target)

    def load_ledger(self, ticker: str) -> ProbabilityCalibrationLedger:
        safe_ticker = self._safe(ticker)
        forecasts: list[dict[str, Any]] = []
        forecast_directory = (
            self.root / "calibration" / "forecast-runs" / safe_ticker
        )
        forecast_payloads: list[dict[str, Any]] = []
        if forecast_directory.exists():
            for path in forecast_directory.glob("*.json"):
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("ticker") != ticker:
                    raise ValueError(f"probability forecast ticker mismatch: {path}")
                forecast_payloads.append(payload)
        forecast_payloads.sort(
            key=lambda row: (
                str(row.get("recorded_at", "")),
                str(row.get("run_id", "")),
            )
        )
        for payload in forecast_payloads:
            rows = payload.get("forecasts")
            if not isinstance(rows, list):
                raise ValueError("probability forecast run has invalid forecasts")
            forecasts.extend(rows)

        outcomes: list[dict[str, Any]] = []
        outcome_directory = self.root / "calibration" / "outcomes" / safe_ticker
        if outcome_directory.exists():
            for path in sorted(outcome_directory.glob("*.json")):
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("ticker") != ticker:
                    raise ValueError(f"probability outcome ticker mismatch: {path}")
                row = payload.get("outcome")
                if not isinstance(row, dict):
                    raise ValueError("probability outcome record is invalid")
                outcomes.append(row)
        return ProbabilityCalibrationLedger.from_payload(
            {
                "version": ProbabilityCalibrationLedger.SERIALIZATION_VERSION,
                "forecasts": forecasts,
                "outcomes": outcomes,
            }
        )

    def forecast_run_count(self, ticker: str) -> int:
        directory = self.root / "calibration" / "forecast-runs" / self._safe(ticker)
        return len(tuple(directory.glob("*.json"))) if directory.exists() else 0

    @staticmethod
    def _safe(value: str) -> str:
        if not isinstance(value, str) or not _SAFE_COMPONENT.fullmatch(value):
            raise ValueError(f"unsafe probability-history path component: {value!r}")
        return value


def _forecast_payload(item: ProbabilityForecast) -> dict[str, Any]:
    return {
        "forecast_id": item.forecast_id,
        "event_key": item.event_key,
        "hypothesis_id": item.hypothesis_id,
        "company_id": item.company_id,
        "forecast_class": item.forecast_class,
        "horizon": item.horizon,
        "event_definition": item.event_definition,
        "issued_at": item.issued_at.isoformat(),
        "evaluation_deadline": item.evaluation_deadline.isoformat(),
        "probability": str(item.probability),
        "displayed_band": item.displayed_band,
        "evidence_snapshot_hash": item.evidence_snapshot_hash,
        "model_version": item.model_version,
        "resolution_rule": item.resolution_rule,
        "resolution_source_policy": item.resolution_source_policy,
        "supersedes_id": item.supersedes_id,
        "first_seen_at": (
            item.first_seen_at.isoformat() if item.first_seen_at is not None else None
        ),
    }


def _outcome_payload(item: ForecastOutcome) -> dict[str, Any]:
    return {
        "forecast_id": item.forecast_id,
        "observed_at": item.observed_at.isoformat(),
        "outcome": item.outcome.value,
        "outcome_evidence_ids": list(item.outcome_evidence_ids),
        "resolver_id": item.resolver_id,
        "rationale": item.rationale,
        "first_seen_at": (
            item.first_seen_at.isoformat() if item.first_seen_at is not None else None
        ),
    }
