from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Iterator, Sequence

try:  # pragma: no cover - exercised through the explicit fail-closed branch
    import fcntl
except ImportError:  # pragma: no cover - native Windows
    fcntl = None

from .probability_calibration import (
    ForecastOutcome,
    ForecastOutcomeState,
    ProbabilityCalibrationLedger,
    ProbabilityForecast,
)


_EVENT_SCHEMA = "prism-production-probability-event/v1"
_EXPORT_SCHEMA = "prism-production-probability-ledger-export/v1"
_ZERO_HASH = "0" * 64
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_EVENT_KINDS = frozenset({"forecast", "outcome"})


class ProductionProbabilityHistoryError(RuntimeError):
    """Raised when persistent production probability history is unsafe or invalid."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_aware_datetime(value: str, *, label: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ProductionProbabilityHistoryError(
            f"{label} must be an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProductionProbabilityHistoryError(
            f"{label} must be timezone-aware"
        )
    return parsed


def _parse_date(value: str, *, label: str) -> date:
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError as exc:
        raise ProductionProbabilityHistoryError(
            f"{label} must be an ISO date"
        ) from exc


def _normalize_history_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ProductionProbabilityHistoryError(
            "production probability history path must be absolute"
        )
    return path.resolve(strict=False)


def _event_hash_payload(
    *,
    sequence: int,
    kind: str,
    recorded_at: datetime,
    previous_hash: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": _EVENT_SCHEMA,
        "sequence": sequence,
        "kind": kind,
        "recorded_at": recorded_at.isoformat(),
        "previous_hash": previous_hash,
        "payload": payload,
    }


def _calculate_event_hash(
    *,
    sequence: int,
    kind: str,
    recorded_at: datetime,
    previous_hash: str,
    payload: dict[str, Any],
) -> str:
    encoded = _canonical_json(
        _event_hash_payload(
            sequence=sequence,
            kind=kind,
            recorded_at=recorded_at,
            previous_hash=previous_hash,
            payload=payload,
        )
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ProductionHistoryEvent:
    sequence: int
    kind: str
    recorded_at: datetime
    previous_hash: str
    payload: dict[str, Any]
    event_hash: str

    def validate(self) -> None:
        if self.sequence < 1:
            raise ProductionProbabilityHistoryError(
                "production history sequence must start at one"
            )
        if self.kind not in _EVENT_KINDS:
            raise ProductionProbabilityHistoryError(
                f"unsupported production history event kind: {self.kind}"
            )
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ProductionProbabilityHistoryError(
                "production history recorded_at must be timezone-aware"
            )
        if not _HASH_RE.fullmatch(self.previous_hash):
            raise ProductionProbabilityHistoryError(
                "production history previous_hash must be SHA-256"
            )
        if not isinstance(self.payload, dict):
            raise ProductionProbabilityHistoryError(
                "production history payload must be a mapping"
            )
        expected = _calculate_event_hash(
            sequence=self.sequence,
            kind=self.kind,
            recorded_at=self.recorded_at,
            previous_hash=self.previous_hash,
            payload=self.payload,
        )
        if self.event_hash != expected:
            raise ProductionProbabilityHistoryError(
                f"production history event hash mismatch at sequence {self.sequence}"
            )

    def to_row(self) -> dict[str, Any]:
        return {
            **_event_hash_payload(
                sequence=self.sequence,
                kind=self.kind,
                recorded_at=self.recorded_at,
                previous_hash=self.previous_hash,
                payload=self.payload,
            ),
            "event_hash": self.event_hash,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "ProductionHistoryEvent":
        if not isinstance(row, dict):
            raise ProductionProbabilityHistoryError(
                "production history event row must be a mapping"
            )
        if row.get("schema") != _EVENT_SCHEMA:
            raise ProductionProbabilityHistoryError(
                "unsupported production history event schema"
            )
        try:
            sequence = int(row.get("sequence", 0))
        except (TypeError, ValueError) as exc:
            raise ProductionProbabilityHistoryError(
                "production history sequence must be an integer"
            ) from exc
        payload = row.get("payload")
        event = cls(
            sequence=sequence,
            kind=str(row.get("kind") or ""),
            recorded_at=_parse_aware_datetime(
                str(row.get("recorded_at") or ""),
                label="recorded_at",
            ),
            previous_hash=str(row.get("previous_hash") or ""),
            payload=dict(payload) if isinstance(payload, dict) else payload,
            event_hash=str(row.get("event_hash") or ""),
        )
        event.validate()
        return event


@dataclass(frozen=True)
class ProductionHistorySnapshot:
    path: Path
    events: tuple[ProductionHistoryEvent, ...]
    ledger: ProbabilityCalibrationLedger
    journal_sha256: str
    head_event_hash: str

    def summary(self) -> dict[str, Any]:
        cohort_pairs = sorted(
            {(item.forecast_class, item.horizon) for item in self.ledger.forecasts}
        )
        cohorts: list[dict[str, Any]] = []
        for forecast_class, horizon in cohort_pairs:
            revisions = tuple(
                item
                for item in self.ledger.forecasts
                if item.forecast_class == forecast_class and item.horizon == horizon
            )
            terminal = self.ledger.terminal_forecasts(
                forecast_class=forecast_class,
                horizon=horizon,
            )
            resolved = tuple(
                item
                for item in terminal
                if self.ledger.outcome_for(item.forecast_id) is not None
            )
            companies = {item.company_id for item in terminal}
            cohorts.append(
                {
                    "cohort_key": f"{forecast_class}|{horizon}",
                    "forecast_revisions": len(revisions),
                    "terminal_forecasts": len(terminal),
                    "resolved_terminal": len(resolved),
                    "unresolved_terminal": len(terminal) - len(resolved),
                    "companies": len(companies),
                }
            )
        state_counts = {state.value: 0 for state in ForecastOutcomeState}
        for item in self.ledger.outcomes:
            state_counts[item.outcome.value] += 1
        return {
            "status": "VALID",
            "history_path": str(self.path),
            "event_count": len(self.events),
            "forecast_revisions": len(self.ledger.forecasts),
            "outcomes": len(self.ledger.outcomes),
            "outcome_state_counts": state_counts,
            "cohorts": cohorts,
            "journal_sha256": self.journal_sha256,
            "head_event_hash": self.head_event_hash,
        }


def _forecast_to_row(item: ProbabilityForecast) -> dict[str, Any]:
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
        "first_seen_at": item.first_seen_at.isoformat()
        if item.first_seen_at is not None
        else None,
    }


def _forecast_from_row(row: dict[str, Any]) -> ProbabilityForecast:
    first_seen_raw = row.get("first_seen_at")
    item = ProbabilityForecast(
        forecast_id=str(row.get("forecast_id") or ""),
        event_key=str(row.get("event_key") or ""),
        hypothesis_id=str(row.get("hypothesis_id") or ""),
        company_id=str(row.get("company_id") or ""),
        forecast_class=str(row.get("forecast_class") or ""),
        horizon=str(row.get("horizon") or ""),
        event_definition=str(row.get("event_definition") or ""),
        issued_at=_parse_aware_datetime(
            str(row.get("issued_at") or ""),
            label="issued_at",
        ),
        evaluation_deadline=_parse_date(
            str(row.get("evaluation_deadline") or ""),
            label="evaluation_deadline",
        ),
        probability=Decimal(str(row.get("probability"))),
        displayed_band=str(row.get("displayed_band") or ""),
        evidence_snapshot_hash=str(row.get("evidence_snapshot_hash") or ""),
        model_version=str(row.get("model_version") or ""),
        resolution_rule=str(row.get("resolution_rule") or ""),
        resolution_source_policy=str(
            row.get("resolution_source_policy") or ""
        ),
        supersedes_id=(
            str(row["supersedes_id"])
            if row.get("supersedes_id") is not None
            else None
        ),
        first_seen_at=(
            _parse_aware_datetime(str(first_seen_raw), label="first_seen_at")
            if first_seen_raw is not None
            else None
        ),
    )
    item.validate()
    return item


def _outcome_to_row(item: ForecastOutcome) -> dict[str, Any]:
    return {
        "forecast_id": item.forecast_id,
        "observed_at": item.observed_at.isoformat(),
        "outcome": item.outcome.value,
        "outcome_evidence_ids": list(item.outcome_evidence_ids),
        "resolver_id": item.resolver_id,
        "rationale": item.rationale,
        "first_seen_at": item.first_seen_at.isoformat()
        if item.first_seen_at is not None
        else None,
    }


def _outcome_from_row(row: dict[str, Any]) -> ForecastOutcome:
    first_seen_raw = row.get("first_seen_at")
    try:
        outcome_state = ForecastOutcomeState(str(row.get("outcome") or ""))
    except ValueError as exc:
        raise ProductionProbabilityHistoryError(
            f"unsupported forecast outcome: {row.get('outcome')}"
        ) from exc
    item = ForecastOutcome(
        forecast_id=str(row.get("forecast_id") or ""),
        observed_at=_parse_aware_datetime(
            str(row.get("observed_at") or ""),
            label="observed_at",
        ),
        outcome=outcome_state,
        outcome_evidence_ids=tuple(
            str(value) for value in (row.get("outcome_evidence_ids") or [])
        ),
        resolver_id=str(row.get("resolver_id") or ""),
        rationale=str(row.get("rationale") or ""),
        first_seen_at=(
            _parse_aware_datetime(
                str(first_seen_raw),
                label="outcome first_seen_at",
            )
            if first_seen_raw is not None
            else None
        ),
    )
    item.validate()
    return item


def _event_first_seen(event: ProductionHistoryEvent) -> datetime:
    raw = event.payload.get("first_seen_at")
    if raw is None:
        raise ProductionProbabilityHistoryError(
            "production history events require explicit first_seen_at"
        )
    return _parse_aware_datetime(
        str(raw),
        label=f"event {event.sequence} first_seen_at",
    )


def load_production_history(
    path: str | Path,
    *,
    allow_missing: bool = False,
) -> ProductionHistorySnapshot:
    history_path = _normalize_history_path(path)
    if not history_path.exists():
        if not allow_missing:
            raise ProductionProbabilityHistoryError(
                f"production probability history does not exist: {history_path}"
            )
        raw = b""
    else:
        if not history_path.is_file():
            raise ProductionProbabilityHistoryError(
                "production probability history path must be a regular file"
            )
        raw = history_path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ProductionProbabilityHistoryError(
            "production probability history must end with a newline"
        )

    ledger = ProbabilityCalibrationLedger()
    events: list[ProductionHistoryEvent] = []
    expected_previous = _ZERO_HASH
    expected_sequence = 1
    if raw:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProductionProbabilityHistoryError(
                "production probability history must be UTF-8"
            ) from exc
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                raise ProductionProbabilityHistoryError(
                    f"blank production history row at line {line_number}"
                )
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProductionProbabilityHistoryError(
                    f"invalid JSON in production history line {line_number}"
                ) from exc
            event = ProductionHistoryEvent.from_row(row)
            if event.sequence != expected_sequence:
                raise ProductionProbabilityHistoryError(
                    f"production history sequence gap at line {line_number}: "
                    f"expected {expected_sequence}, got {event.sequence}"
                )
            if event.previous_hash != expected_previous:
                raise ProductionProbabilityHistoryError(
                    f"production history hash chain break at sequence {event.sequence}"
                )
            first_seen = _event_first_seen(event)
            if first_seen != event.recorded_at:
                raise ProductionProbabilityHistoryError(
                    f"production history sequence {event.sequence} must bind "
                    "first_seen_at to the actual journal recorded_at"
                )
            if event.kind == "forecast":
                ledger.append_forecast(_forecast_from_row(event.payload))
            else:
                ledger.append_outcome(_outcome_from_row(event.payload))
            events.append(event)
            expected_sequence += 1
            expected_previous = event.event_hash

    return ProductionHistorySnapshot(
        path=history_path,
        events=tuple(events),
        ledger=ledger,
        journal_sha256=sha256(raw).hexdigest(),
        head_event_hash=events[-1].event_hash if events else _ZERO_HASH,
    )


def _prepare_parent(path: Path) -> None:
    parent = path.parent
    existed = parent.exists()
    parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not parent.is_dir():
        raise ProductionProbabilityHistoryError(
            "production probability history parent must be a directory"
        )
    if not existed:
        os.chmod(parent, 0o700)


@contextmanager
def _exclusive_history_lock(path: Path) -> Iterator[None]:
    if fcntl is None:
        raise ProductionProbabilityHistoryError(
            "production probability history mutation requires POSIX flock support"
        )
    _prepare_parent(path)
    lock_path = path.with_name(path.name + ".lock")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def initialize_production_history(path: str | Path) -> dict[str, Any]:
    history_path = _normalize_history_path(path)
    with _exclusive_history_lock(history_path):
        if not history_path.exists():
            descriptor = os.open(
                history_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        os.chmod(history_path, 0o600)
        snapshot = load_production_history(history_path)
    return {"status": "INITIALIZED", **snapshot.summary()}


def _append_event(
    path: str | Path,
    *,
    kind: str,
    payload: dict[str, Any],
    recorded_at: datetime,
) -> tuple[ProductionHistoryEvent, ProductionHistorySnapshot]:
    history_path = _normalize_history_path(path)
    if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
        raise ProductionProbabilityHistoryError(
            "production history recorded_at must be timezone-aware"
        )
    recorded_at = recorded_at.astimezone(timezone.utc)
    with _exclusive_history_lock(history_path):
        if not history_path.exists():
            descriptor = os.open(
                history_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            os.close(descriptor)
        before = load_production_history(history_path)
        sequence = len(before.events) + 1
        event_hash = _calculate_event_hash(
            sequence=sequence,
            kind=kind,
            recorded_at=recorded_at,
            previous_hash=before.head_event_hash,
            payload=payload,
        )
        event = ProductionHistoryEvent(
            sequence=sequence,
            kind=kind,
            recorded_at=recorded_at,
            previous_hash=before.head_event_hash,
            payload=payload,
            event_hash=event_hash,
        )
        event.validate()
        if _event_first_seen(event) != recorded_at:
            raise ProductionProbabilityHistoryError(
                "first_seen_at must equal the writer-controlled recorded_at"
            )
        if kind == "forecast":
            before.ledger.append_forecast(_forecast_from_row(payload))
        else:
            before.ledger.append_outcome(_outcome_from_row(payload))
        encoded = (_canonical_json(event.to_row()) + "\n").encode("utf-8")
        descriptor = os.open(
            history_path,
            os.O_WRONLY | os.O_APPEND,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            written = os.write(descriptor, encoded)
            if written != len(encoded):
                raise ProductionProbabilityHistoryError(
                    "production history append was incomplete"
                )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        after = load_production_history(history_path)
        if after.head_event_hash != event.event_hash:
            raise ProductionProbabilityHistoryError(
                "production history append verification failed"
            )
    return event, after


def append_production_forecast(
    path: str | Path,
    *,
    forecast_id: str,
    event_key: str,
    hypothesis_id: str,
    company_id: str,
    forecast_class: str,
    horizon: str,
    event_definition: str,
    issued_at: datetime,
    evaluation_deadline: date,
    probability: Decimal,
    displayed_band: str,
    evidence_snapshot_hash: str,
    model_version: str,
    resolution_rule: str,
    resolution_source_policy: str,
    supersedes_id: str | None = None,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    now = (recorded_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    forecast = ProbabilityForecast(
        forecast_id=forecast_id,
        event_key=event_key,
        hypothesis_id=hypothesis_id,
        company_id=company_id,
        forecast_class=forecast_class,
        horizon=horizon,
        event_definition=event_definition,
        issued_at=issued_at,
        evaluation_deadline=evaluation_deadline,
        probability=probability,
        displayed_band=displayed_band,
        evidence_snapshot_hash=evidence_snapshot_hash,
        model_version=model_version,
        resolution_rule=resolution_rule,
        resolution_source_policy=resolution_source_policy,
        supersedes_id=supersedes_id,
        first_seen_at=now,
    )
    forecast.validate()
    event, snapshot = _append_event(
        path,
        kind="forecast",
        payload=_forecast_to_row(forecast),
        recorded_at=now,
    )
    return {
        "status": "FORECAST_APPENDED",
        "event_hash": event.event_hash,
        "forecast_id": forecast.forecast_id,
        "first_seen_at": now.isoformat(),
        **snapshot.summary(),
    }


def append_production_outcome(
    path: str | Path,
    *,
    forecast_id: str,
    observed_at: datetime,
    outcome: ForecastOutcomeState,
    outcome_evidence_ids: tuple[str, ...],
    resolver_id: str,
    rationale: str,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    now = (recorded_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    resolved = ForecastOutcome(
        forecast_id=forecast_id,
        observed_at=observed_at,
        outcome=outcome,
        outcome_evidence_ids=outcome_evidence_ids,
        resolver_id=resolver_id,
        rationale=rationale,
        first_seen_at=now,
    )
    resolved.validate()
    event, snapshot = _append_event(
        path,
        kind="outcome",
        payload=_outcome_to_row(resolved),
        recorded_at=now,
    )
    return {
        "status": "OUTCOME_APPENDED",
        "event_hash": event.event_hash,
        "forecast_id": resolved.forecast_id,
        "first_seen_at": now.isoformat(),
        **snapshot.summary(),
    }


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    if not path.is_absolute():
        raise ProductionProbabilityHistoryError(
            "production probability export path must be absolute"
        )
    _prepare_parent(path)
    encoded = (_canonical_json(payload) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        written = os.write(descriptor, encoded)
        if written != len(encoded):
            raise ProductionProbabilityHistoryError(
                "production probability export write was incomplete"
            )
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def export_production_ledger(
    history_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    snapshot = load_production_history(history_path)
    output = _normalize_history_path(output_path)
    ledger_payload = snapshot.ledger.to_payload()
    _atomic_json_write(output, ledger_payload)
    return {
        "status": "EXPORTED",
        "schema": _EXPORT_SCHEMA,
        "history_path": str(snapshot.path),
        "output_path": str(output),
        "history_sha256": snapshot.journal_sha256,
        "head_event_hash": snapshot.head_event_hash,
        "output_sha256": sha256(output.read_bytes()).hexdigest(),
        "forecast_revisions": len(snapshot.ledger.forecasts),
        "outcomes": len(snapshot.ledger.outcomes),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Operate PRISM's hash-chained production probability forecast/outcome history"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def history_argument(command: argparse.ArgumentParser) -> None:
        command.add_argument("--history", required=True)

    initialize = subparsers.add_parser("init")
    history_argument(initialize)

    validate = subparsers.add_parser("validate")
    history_argument(validate)

    summary = subparsers.add_parser("summary")
    history_argument(summary)

    export = subparsers.add_parser("export")
    history_argument(export)
    export.add_argument("--output", required=True)

    forecast = subparsers.add_parser("append-forecast")
    history_argument(forecast)
    forecast.add_argument("--forecast-id", required=True)
    forecast.add_argument("--event-key", required=True)
    forecast.add_argument("--hypothesis-id", required=True)
    forecast.add_argument("--company-id", required=True)
    forecast.add_argument("--forecast-class", required=True)
    forecast.add_argument("--horizon", required=True)
    forecast.add_argument("--event-definition", required=True)
    forecast.add_argument("--issued-at", required=True)
    forecast.add_argument("--evaluation-deadline", required=True)
    forecast.add_argument("--probability", required=True)
    forecast.add_argument("--displayed-band", required=True)
    forecast.add_argument("--evidence-snapshot-hash", required=True)
    forecast.add_argument("--model-version", required=True)
    forecast.add_argument("--resolution-rule", required=True)
    forecast.add_argument("--resolution-source-policy", required=True)
    forecast.add_argument("--supersedes-id")

    outcome = subparsers.add_parser("append-outcome")
    history_argument(outcome)
    outcome.add_argument("--forecast-id", required=True)
    outcome.add_argument("--observed-at", required=True)
    outcome.add_argument(
        "--outcome",
        required=True,
        choices=tuple(item.value for item in ForecastOutcomeState),
    )
    outcome.add_argument("--evidence-id", action="append", default=[])
    outcome.add_argument("--resolver-id", required=True)
    outcome.add_argument("--rationale", required=True)
    return parser


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "init":
        return initialize_production_history(args.history)
    if args.command in {"validate", "summary"}:
        return load_production_history(args.history).summary()
    if args.command == "export":
        return export_production_ledger(args.history, args.output)
    if args.command == "append-forecast":
        return append_production_forecast(
            args.history,
            forecast_id=args.forecast_id,
            event_key=args.event_key,
            hypothesis_id=args.hypothesis_id,
            company_id=args.company_id,
            forecast_class=args.forecast_class,
            horizon=args.horizon,
            event_definition=args.event_definition,
            issued_at=_parse_aware_datetime(args.issued_at, label="issued_at"),
            evaluation_deadline=_parse_date(
                args.evaluation_deadline,
                label="evaluation_deadline",
            ),
            probability=Decimal(args.probability),
            displayed_band=args.displayed_band,
            evidence_snapshot_hash=args.evidence_snapshot_hash,
            model_version=args.model_version,
            resolution_rule=args.resolution_rule,
            resolution_source_policy=args.resolution_source_policy,
            supersedes_id=args.supersedes_id,
        )
    return append_production_outcome(
        args.history,
        forecast_id=args.forecast_id,
        observed_at=_parse_aware_datetime(
            args.observed_at,
            label="observed_at",
        ),
        outcome=ForecastOutcomeState(args.outcome),
        outcome_evidence_ids=tuple(args.evidence_id),
        resolver_id=args.resolver_id,
        rationale=args.rationale,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = _dispatch(args)
    except (
        ProductionProbabilityHistoryError,
        ValueError,
        OSError,
    ) as exc:
        print(f"ERROR [PROBABILITY_HISTORY] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
