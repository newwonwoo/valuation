from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .records import RunManifest, RunStatus


_SAFE_TICKER = re.compile(r"^[A-Za-z0-9._-]+$")


class StateStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def load_current(self, ticker: str) -> dict[str, Any] | None:
        path = self._state_dir(ticker) / "current_state.json"
        return None if not path.exists() else json.loads(path.read_text(encoding="utf-8"))

    def save_run(self, manifest: RunManifest, artifacts: dict[str, Any]) -> Path:
        run_dir = self._run_dir(manifest.ticker, manifest.run_id)
        if run_dir.exists():
            raise FileExistsError(f"run is immutable and already exists: {manifest.run_id}")
        run_dir.mkdir(parents=True)
        self._write_json(run_dir / "manifest.json", _jsonable(asdict(manifest)))
        for filename, payload in artifacts.items():
            path = run_dir / filename
            if isinstance(payload, str):
                path.write_text(payload, encoding="utf-8")
            else:
                self._write_json(path, _jsonable(payload))
        return run_dir

    def promote_current(self, manifest: RunManifest, current_state: dict[str, Any]) -> None:
        if manifest.status is not RunStatus.COMPLETED or not manifest.audit_passed:
            raise ValueError("only completed, audit-passed runs may become current state")
        state_dir = self._state_dir(manifest.ticker)
        state_dir.mkdir(parents=True, exist_ok=True)
        target = state_dir / "current_state.json"
        temporary = state_dir / f".{manifest.run_id}.tmp"
        temporary.write_text(
            json.dumps(_jsonable(current_state), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, target)

    def _state_dir(self, ticker: str) -> Path:
        return self.root / "state" / self._safe(ticker)

    def _run_dir(self, ticker: str, run_id: str) -> Path:
        return self.root / "runs" / self._safe(ticker) / self._safe(run_id)

    @staticmethod
    def _safe(value: str) -> str:
        if not _SAFE_TICKER.fullmatch(value):
            raise ValueError(f"unsafe state path component: {value!r}")
        return value

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def thesis_delta(previous: str, current: str) -> dict[str, list[str]]:
    previous_lines = {line.strip() for line in previous.splitlines() if line.strip()}
    current_lines = {line.strip() for line in current.splitlines() if line.strip()}
    return {
        "strengthened_or_new": sorted(current_lines - previous_lines),
        "weakened_or_removed": sorted(previous_lines - current_lines),
        "unchanged": sorted(previous_lines & current_lines),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value.value if hasattr(value, "value") else value
