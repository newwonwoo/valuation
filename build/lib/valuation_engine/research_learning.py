from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any

from .ablation import (
    AblationBatchResult,
    LoadoutAction,
    ResearchLoadoutRecommendation,
)
from .decision_impact import (
    ImpactClassification,
    ModuleHistoryEntry,
    ModuleImpactAssessment,
    ResearchEffort,
    ResearchIntensity,
)


_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class ResearchLearningRecordRef:
    ticker: str
    run_id: str
    path: str
    content_hash: str
    recorded_at: str


class ResearchLearningStore:
    """Append-only module-impact history used only for future research deployment.

    Historical observations never mutate a completed valuation run. Each run is stored as an
    immutable file. Only measured assessments enter the statistical prior history; explicit
    NOT_MEASURABLE/NOT_APPLICABLE/FAILED states remain in the raw record for audit.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save_batch(
        self,
        *,
        ticker: str,
        run_id: str,
        batch: AblationBatchResult,
        recorded_at: str | None = None,
    ) -> ResearchLearningRecordRef:
        if not isinstance(batch, AblationBatchResult):
            raise TypeError("research learning requires AblationBatchResult")
        safe_ticker = self._safe(ticker)
        safe_run = self._safe(run_id)
        recorded = recorded_at or datetime.now(timezone.utc).isoformat()
        payload = {
            "schema_version": "1.0",
            "ticker": ticker,
            "run_id": run_id,
            "recorded_at": recorded,
            "batch": _jsonable(asdict(batch)),
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        digest = sha256(encoded).hexdigest()
        directory = self.root / "learning" / safe_ticker / "module-impact"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{safe_run}.json"
        if target.exists():
            raise FileExistsError(f"research learning record is immutable: {run_id}")
        temporary = directory / f".{safe_run}.tmp"
        temporary.write_bytes(encoded)
        os.replace(temporary, target)
        return ResearchLearningRecordRef(ticker, run_id, str(target), digest, recorded)

    def load_prior_history(self, ticker: str) -> dict[str, tuple[ModuleHistoryEntry, ...]]:
        grouped: dict[str, list[ModuleHistoryEntry]] = {}
        for payload in self._payloads(ticker):
            observations = payload.get("batch", {}).get("module_observations", [])
            if not isinstance(observations, list):
                raise ValueError("module-impact history has invalid observations")
            for row in observations:
                if not isinstance(row, dict) or row.get("assessment") is None:
                    continue
                assessment = _parse_assessment(row["assessment"])
                effort = _parse_effort(row.get("effort", {}))
                entry = ModuleHistoryEntry(
                    assessment=assessment,
                    effort=effort,
                    applicable=bool(row.get("applicable", True)),
                    research_performed=True,
                    mandatory_guardrail=bool(row.get("mandatory_guardrail", False)),
                )
                grouped.setdefault(assessment.module_id, []).append(entry)
        return {module_id: tuple(entries) for module_id, entries in grouped.items()}

    def load_latest_recommendations(self, ticker: str) -> tuple[ResearchLoadoutRecommendation, ...]:
        payloads = self._payloads(ticker)
        if not payloads:
            return ()
        rows = payloads[-1].get("batch", {}).get("loadout_recommendations", [])
        if not isinstance(rows, list):
            raise ValueError("research learning record has invalid recommendations")
        return tuple(_parse_recommendation(row) for row in rows)

    def record_count(self, ticker: str) -> int:
        return len(self._payloads(ticker))

    def _payloads(self, ticker: str) -> list[dict[str, Any]]:
        directory = self.root / "learning" / self._safe(ticker) / "module-impact"
        if not directory.exists():
            return []
        payloads: list[dict[str, Any]] = []
        for path in directory.glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("ticker") != ticker:
                raise ValueError(f"research learning ticker mismatch: {path}")
            payloads.append(payload)
        payloads.sort(key=lambda row: (str(row.get("recorded_at", "")), str(row.get("run_id", ""))))
        return payloads

    @staticmethod
    def _safe(value: str) -> str:
        if not isinstance(value, str) or not _SAFE_COMPONENT.fullmatch(value):
            raise ValueError(f"unsafe learning path component: {value!r}")
        return value


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _parse_effort(row: dict[str, Any]) -> ResearchEffort:
    return ResearchEffort(
        source_queries=int(row.get("source_queries", 0)),
        documents_reviewed=int(row.get("documents_reviewed", 0)),
        llm_calls=int(row.get("llm_calls", 0)),
        elapsed_seconds=float(row.get("elapsed_seconds", 0.0)),
    )


def _parse_assessment(row: dict[str, Any]) -> ModuleImpactAssessment:
    if not isinstance(row, dict):
        raise ValueError("module impact assessment must be an object")
    return ModuleImpactAssessment(
        module_id=str(row["module_id"]),
        classification=ImpactClassification(str(row["classification"])),
        value_delta_abs=_optional_float(row.get("value_delta_abs")),
        value_delta_pct=_optional_float(row.get("value_delta_pct")),
        status_changed=bool(row.get("status_changed", False)),
        route_changed=bool(row.get("route_changed", False)),
        methods_changed=bool(row.get("methods_changed", False)),
        assumption_changed=bool(row.get("assumption_changed", False)),
        conclusion_changed=bool(row.get("conclusion_changed", False)),
        timing_delta_days=_optional_float(row.get("timing_delta_days")),
        guardrail_violation_detected=bool(row.get("guardrail_violation_detected", False)),
        material=bool(row.get("material", False)),
        rationale=str(row.get("rationale", "")),
    )


def _parse_recommendation(row: dict[str, Any]) -> ResearchLoadoutRecommendation:
    if not isinstance(row, dict):
        raise ValueError("loadout recommendation must be an object")
    return ResearchLoadoutRecommendation(
        module_id=str(row["module_id"]),
        intensity=ResearchIntensity(str(row["intensity"])),
        action=LoadoutAction(str(row["action"])),
        rationale=str(row.get("rationale", "")),
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_jsonable(item) for item in value]
    return value.value if hasattr(value, "value") else value
