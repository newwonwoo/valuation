#!/usr/bin/env python3
"""Compare two prepared PRISM runs and reconcile judgment variance.

The comparison deliberately separates two domains:

* only clean PRISM run inputs committed at the current repository HEAD are
  comparable; external or uncommitted numbers fail closed before execution;
* structural contract differences (segment/method/calibration/risk/source scope)
  are not averaged or decomposed; they immediately require reconciliation;
* judgment differences inside the same underwriting contract are decomposed by
  an ordered exact waterfall: starting from run A, replace one declared
  underwriting row with run B's row, rerun canonical PRISM, and record the
  deterministic valuation delta before replacing the next row.

The waterfall is exact for its disclosed order, not an order-independent Shapley
allocation. Any final residual is surfaced explicitly and never hidden by an
average.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Mapping

import yaml


ROOT = Path(__file__).resolve().parents[1]

STATUS_CONSISTENT = "CONSISTENT"
STATUS_RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
STATUS_EXTERNAL_RUN_NOT_COMPARABLE = "EXTERNAL_RUN_NOT_COMPARABLE"
EXIT_RECONCILIATION_REQUIRED = 3
EXIT_EXTERNAL_RUN_NOT_COMPARABLE = 4

DEFAULT_BASE_THRESHOLD = Decimal("0.20")
DEFAULT_PROBABILITY_THRESHOLD = Decimal("0.10")  # 10 percentage points
DEFAULT_WACC_THRESHOLD = Decimal("0.01")  # 1 percentage point
DEFAULT_RESIDUAL_TOLERANCE = Decimal("0.01")  # KRW/share or reporting currency/share
RUNTIME_INPUT_PATHS = (
    "scripts/compare_runs.py",
    "scripts/run_kr_live.py",
    "src/valuation_engine",
    "config",
)


class RunComparisonError(ValueError):
    pass


@dataclass(frozen=True)
class Outcome:
    target: str
    scenarios: tuple[tuple[str, Decimal], ...]
    probabilities: tuple[tuple[str, Decimal], ...]
    expected_value: Decimal | None
    wacc: Decimal | None

    @property
    def scenario_map(self) -> dict[str, Decimal]:
        return dict(self.scenarios)

    @property
    def probability_map(self) -> dict[str, Decimal]:
        return dict(self.probabilities)

    @property
    def base_value(self) -> Decimal:
        values = self.scenario_map
        for key in ("Base", "Core"):
            if key in values:
                return values[key]
        if not self.scenarios:
            raise RunComparisonError("completed run carries no scenario values")
        return self.scenarios[0][1]


Executor = Callable[[Path], tuple]


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RunComparisonError(f"cannot convert value to Decimal: {value!r}") from exc


def _load_yaml_mapping(path: Path) -> dict:
    if not path.is_file():
        raise RunComparisonError(f"required file is missing: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise RunComparisonError(f"expected YAML mapping: {path}")
    return dict(payload)


def _stable_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: object) -> str:
    return sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(ROOT), *args),
        check=False,
        capture_output=True,
        text=True,
    )


def _provided_absolute(path: Path) -> Path:
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else Path.cwd() / expanded


def _reject_symlink_components(path: Path) -> Path:
    provided = _provided_absolute(path)
    current = Path(provided.anchor)
    for component in provided.parts[1:]:
        if component in ("", "."):
            continue
        if component == "..":
            current = current.parent
            continue
        current = current / component
        if current.is_symlink():
            raise RunComparisonError(f"run inputs may not use symlinks: {current}")
        if not current.exists():
            raise RunComparisonError(f"run input path component does not exist: {current}")
    return current


def _resolved_run_input(path: Path) -> Path:
    return _reject_symlink_components(path).resolve()


def _repository_root() -> Path:
    repository = _git("rev-parse", "--show-toplevel")
    if repository.returncode != 0:
        raise RunComparisonError("comparison runner is not inside a Git repository")
    repository_root = Path(repository.stdout.strip()).resolve()
    if repository_root != ROOT.resolve():
        raise RunComparisonError("comparison runner repository identity is ambiguous")
    return repository_root


def _head_commit() -> str:
    commit = _git("rev-parse", "HEAD")
    if commit.returncode != 0:
        raise RunComparisonError("cannot resolve PRISM repository HEAD")
    return commit.stdout.strip()


def _committed_runtime_receipt() -> dict:
    """Attest the canonical evaluator and runtime registries used by comparison."""

    repository_root = _repository_root()
    status = _git(
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--",
        *RUNTIME_INPUT_PATHS,
    )
    if status.returncode != 0:
        raise RunComparisonError("cannot verify PRISM runtime worktree state")
    if status.stdout:
        raise RunComparisonError(
            "PRISM evaluator or runtime registry differs from repository HEAD"
        )

    committed_tree = _git(
        "ls-tree",
        "-r",
        "--name-only",
        "-z",
        "HEAD",
        "--",
        *RUNTIME_INPUT_PATHS,
    )
    if committed_tree.returncode != 0:
        raise RunComparisonError("cannot enumerate committed PRISM runtime inputs")
    receipts: list[dict[str, str]] = []
    for relative in filter(None, committed_tree.stdout.split("\0")):
        path = repository_root / relative
        if not path.exists():
            raise RunComparisonError(
                f"committed PRISM runtime input is missing from worktree: {relative}"
            )
        if path.is_symlink():
            raise RunComparisonError(f"runtime inputs may not be symlinks: {path}")
        if not path.is_file():
            raise RunComparisonError(f"committed runtime input is not a file: {relative}")
        head_object = _git("rev-parse", f"HEAD:{relative}")
        working_object = _git("hash-object", str(path))
        if head_object.returncode != 0 or working_object.returncode != 0:
            raise RunComparisonError(f"cannot verify committed runtime input: {relative}")
        if head_object.stdout.strip() != working_object.stdout.strip():
            raise RunComparisonError(f"runtime input differs from HEAD: {relative}")
        receipts.append(
            {"path": relative, "sha256": sha256(path.read_bytes()).hexdigest()}
        )
    if not receipts:
        raise RunComparisonError("committed PRISM runtime input tree is empty")
    return {
        "repository": "current_prism_repository",
        "commit": _head_commit(),
        "input_tree_sha256": _digest(receipts),
        "inputs": receipts,
    }


def _committed_run_receipt(run_dir: Path) -> dict:
    """Bind a comparison input to clean files committed in this repository."""

    run_dir = _resolved_run_input(run_dir)
    repository_root = _repository_root()
    if not run_dir.is_dir():
        raise RunComparisonError(f"run directory does not exist: {run_dir}")
    try:
        run_dir.relative_to(repository_root)
    except ValueError as exc:
        raise RunComparisonError(
            f"run directory is outside the PRISM repository: {run_dir}"
        ) from exc

    required = (
        run_dir / "run.yaml",
        run_dir / "declarations" / "underwriting.yaml",
    )
    missing = tuple(path for path in required if not path.is_file())
    if missing:
        raise RunComparisonError(
            "not a prepared PRISM run; missing "
            + ", ".join(str(path.relative_to(run_dir)) for path in missing)
        )

    run_relative = run_dir.relative_to(repository_root).as_posix()
    committed_tree = _git(
        "ls-tree",
        "-r",
        "--name-only",
        "-z",
        "HEAD",
        "--",
        run_relative,
    )
    if committed_tree.returncode != 0:
        raise RunComparisonError(f"cannot enumerate committed run tree: {run_relative}")

    files: set[Path] = set()
    for relative in filter(None, committed_tree.stdout.split("\0")):
        path = repository_root / relative
        within_run = path.relative_to(run_dir).parts
        if within_run and within_run[0] == "out":
            continue
        if not path.exists():
            raise RunComparisonError(
                f"committed run input is missing from worktree: {relative}"
            )
        if path.is_symlink():
            raise RunComparisonError(f"run inputs may not be symlinks: {path}")
        if not path.is_file():
            raise RunComparisonError(f"committed run input is not a file: {relative}")
        files.add(path.resolve())

    for path in run_dir.rglob("*"):
        relative_parts = path.relative_to(run_dir).parts
        if relative_parts and relative_parts[0] == "out":
            continue
        if path.is_symlink():
            raise RunComparisonError(f"run inputs may not be symlinks: {path}")
        if path.is_file():
            files.add(path.resolve())
    files.update(path.resolve() for path in required)
    config = _load_yaml_mapping(run_dir / "run.yaml")
    calibration = config.get("calibration")
    if isinstance(calibration, Mapping):
        for key in ("artifact", "provenance", "conditioning"):
            declared = calibration.get(key)
            if not isinstance(declared, str) or not declared:
                continue
            candidate = Path(declared)
            unresolved = candidate if candidate.is_absolute() else run_dir / candidate
            resolved = _reject_symlink_components(unresolved).resolve()
            try:
                resolved.relative_to(repository_root)
            except ValueError as exc:
                raise RunComparisonError(
                    f"calibration {key} is outside the PRISM repository: {declared}"
                ) from exc
            if not resolved.is_file():
                raise RunComparisonError(f"calibration {key} is missing: {declared}")
            files.add(resolved)

    receipts: list[dict[str, str]] = []
    for path in sorted(files):
        try:
            relative = path.relative_to(repository_root).as_posix()
        except ValueError as exc:
            raise RunComparisonError(
                f"run input resolves outside the PRISM repository: {path}"
            ) from exc
        committed = _git("cat-file", "-e", f"HEAD:{relative}")
        if committed.returncode != 0:
            raise RunComparisonError(f"run input is not committed at HEAD: {relative}")
        head_object = _git("rev-parse", f"HEAD:{relative}")
        working_object = _git("hash-object", str(path))
        if head_object.returncode != 0 or working_object.returncode != 0:
            raise RunComparisonError(f"cannot verify committed run input: {relative}")
        if head_object.stdout.strip() != working_object.stdout.strip():
            raise RunComparisonError(f"run input differs from HEAD: {relative}")
        receipts.append(
            {"path": relative, "sha256": sha256(path.read_bytes()).hexdigest()}
        )

    return {
        "repository": "current_prism_repository",
        "run_path": run_dir.relative_to(repository_root).as_posix(),
        "commit": _head_commit(),
        "input_tree_sha256": _digest(receipts),
        "inputs": receipts,
    }


def _tree_digest(path: Path) -> str | None:
    if not path.is_dir():
        return None
    receipts: list[tuple[str, str]] = []
    for item in sorted(path.rglob("*")):
        if item.is_file():
            receipts.append(
                (
                    item.relative_to(path).as_posix(),
                    sha256(item.read_bytes()).hexdigest(),
                )
            )
    return _digest(receipts)


def _optional_yaml_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    return _digest(yaml.safe_load(path.read_text(encoding="utf-8")))


def _file_binding_signature(run_dir: Path, value: object) -> dict | None:
    if not isinstance(value, str) or not value:
        return None
    declared = value
    candidate = Path(declared)
    resolved = candidate if candidate.is_absolute() else (run_dir / candidate)
    return {
        "path": declared,
        "sha256": sha256(resolved.read_bytes()).hexdigest() if resolved.is_file() else None,
    }


def _normalize_contract(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _normalize_contract(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_contract(item) for item in value]
    return value


def _calibration_signature(run_dir: Path, config: Mapping) -> dict:
    calibration = config.get("calibration")
    if not isinstance(calibration, Mapping):
        return {"present": False}
    return {
        "present": True,
        "contract": _normalize_contract(calibration),
        "artifact_binding": _file_binding_signature(run_dir, calibration.get("artifact")),
        "provenance_binding": _file_binding_signature(run_dir, calibration.get("provenance")),
        "conditioning_binding": _file_binding_signature(run_dir, calibration.get("conditioning")),
    }


def _method_signature(config: Mapping) -> tuple[tuple[str, str], ...]:
    filing = config.get("filing")
    filing = filing if isinstance(filing, Mapping) else {}
    segments = config.get("segments")
    if isinstance(segments, list) and segments:
        rows: list[tuple[str, str]] = []
        for row in segments:
            if not isinstance(row, Mapping):
                raise RunComparisonError("run.yaml segments must be mappings")
            rows.append((str(row.get("segment_id") or ""), str(row.get("method") or "")))
        return tuple(rows)
    return (
        (
            str(filing.get("segment_id", "core")),
            str(config.get("method") or ""),
        ),
    )


def _segment_declaration_signature(run_dir: Path) -> dict | None:
    path = run_dir / "declarations" / "segments.yaml"
    if not path.is_file():
        return None
    return _load_yaml_mapping(path)


def _underwriting_payload(run_dir: Path) -> dict:
    return _load_yaml_mapping(run_dir / "declarations" / "underwriting.yaml")


def _underwriting_header_contract(run_dir: Path) -> dict:
    payload = _underwriting_payload(run_dir)
    return {
        "target_id": str(payload.get("target_id") or ""),
        "as_of": str(payload.get("as_of") or ""),
    }


def _flatten_underwriting(payload: Mapping) -> dict[str, dict]:
    declarations = payload.get("declarations")
    if not isinstance(declarations, Mapping) or not declarations:
        raise RunComparisonError("underwriting requires a declarations mapping")
    flattened: dict[str, dict] = {}
    for metric, entry in declarations.items():
        metric_name = str(metric)
        if isinstance(entry, list):
            if not entry:
                raise RunComparisonError(f"underwriting metric {metric_name} has no rows")
            for row in entry:
                if not isinstance(row, Mapping):
                    raise RunComparisonError(f"underwriting metric {metric_name} row is invalid")
                segment = str(row.get("segment") or "")
                if not segment:
                    raise RunComparisonError(
                        f"multi-row underwriting metric {metric_name} requires segment"
                    )
                identity = f"{metric_name}[segment={segment}]"
                if identity in flattened:
                    raise RunComparisonError(f"duplicate underwriting row: {identity}")
                flattened[identity] = {
                    "metric": metric_name,
                    "segment": segment,
                    "multi": True,
                    "row": deepcopy(dict(row)),
                }
        else:
            if not isinstance(entry, Mapping):
                raise RunComparisonError(f"underwriting metric {metric_name} is invalid")
            segment = str(entry.get("segment", "core"))
            flattened[metric_name] = {
                "metric": metric_name,
                "segment": segment,
                "multi": False,
                "row": deepcopy(dict(entry)),
            }
    return flattened


def _underwriting_contract(run_dir: Path) -> tuple[tuple[str, bool, str, str], ...]:
    flattened = _flatten_underwriting(_underwriting_payload(run_dir))
    return tuple(
        sorted(
            (
                identity,
                bool(item["multi"]),
                str(item["segment"]),
                str(item["row"].get("unit") or ""),
            )
            for identity, item in flattened.items()
        )
    )


def _structural_signature(run_dir: Path) -> dict:
    config = _load_yaml_mapping(run_dir / "run.yaml")
    filing = config.get("filing")
    filing = filing if isinstance(filing, Mapping) else {}
    return {
        "as_of": str(config.get("as_of") or ""),
        "jurisdiction": str(config.get("jurisdiction", "KR")),
        "scenario_ids": tuple(config.get("scenario_ids") or ()),
        "forecast_years": int(config.get("forecast_years", 5)),
        "filing": {
            "business_year": str(filing.get("business_year") or ""),
            "report_code": str(filing.get("report_code", "11011")),
            "fs_div": str(filing.get("fs_div", "CFS")),
            "fiscal_period_end": str(filing.get("fiscal_period_end") or ""),
            "segment_id": str(filing.get("segment_id", "core")),
        },
        "methods": _method_signature(config),
        "segments": _segment_declaration_signature(run_dir),
        "calibration": _calibration_signature(run_dir, config),
        "risk_pack_hash": _optional_yaml_digest(run_dir / "declarations" / "risk_pack.yaml"),
        "raw_source_hash": _tree_digest(run_dir / "raw"),
        "underwriting_header_contract": _underwriting_header_contract(run_dir),
        "underwriting_contract": _underwriting_contract(run_dir),
    }


def _structural_findings(a: Mapping, b: Mapping) -> list[dict]:
    findings: list[dict] = []
    labels = {
        "as_of": "KNOWLEDGE_TIME_MISMATCH",
        "jurisdiction": "JURISDICTION_MISMATCH",
        "scenario_ids": "SCENARIO_CONTRACT_MISMATCH",
        "forecast_years": "FORECAST_HORIZON_MISMATCH",
        "filing": "FILING_SCOPE_MISMATCH",
        "methods": "METHOD_CONTRACT_MISMATCH",
        "segments": "SEGMENT_CONTRACT_MISMATCH",
        "calibration": "CALIBRATION_CONTRACT_MISMATCH",
        "risk_pack_hash": "WACC_INPUT_CONTRACT_MISMATCH",
        "raw_source_hash": "PRIMARY_SOURCE_SNAPSHOT_MISMATCH",
        "underwriting_header_contract": "UNDERWRITING_HEADER_CONTRACT_MISMATCH",
        "underwriting_contract": "UNDERWRITING_CONTRACT_MISMATCH",
    }
    for key, code in labels.items():
        if a.get(key) != b.get(key):
            findings.append(
                {"code": code, "field": key, "a": a.get(key), "b": b.get(key)}
            )
    return findings


def _load_runner_executor() -> Executor:
    path = ROOT / "scripts" / "run_kr_live.py"
    spec = importlib.util.spec_from_file_location("prism_compare_run_kr_live", path)
    if spec is None or spec.loader is None:
        raise RunComparisonError("cannot load scripts/run_kr_live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.execute_run


def _extract_outcome(result: object) -> Outcome:
    data = getattr(result, "data", None)
    if not isinstance(data, Mapping):
        raise RunComparisonError("canonical run result carries no data mapping")
    valuation = data.get("generic_valuation_result")
    scenario_rows = tuple(getattr(valuation, "scenarios", ()) or ())
    if not scenario_rows:
        raise RunComparisonError("canonical run carries no generic valuation scenarios")
    scenarios = tuple(
        (str(getattr(item, "scenario_id")), _decimal(getattr(item, "value_per_share")))
        for item in scenario_rows
    )
    expected_raw = getattr(valuation, "expected_value_per_share", None)
    expected = None if expected_raw is None else _decimal(expected_raw)

    scenario_set = data.get("bound_scenario_set")
    probability_rows = tuple(getattr(scenario_set, "scenarios", ()) or ())
    probabilities: list[tuple[str, Decimal]] = []
    for item in probability_rows:
        probability = getattr(item, "probability", None)
        if probability is not None:
            probabilities.append((str(getattr(item, "scenario_id")), _decimal(probability)))

    wacc = None
    wacc_stage = data.get("live_wacc_result")
    wacc_result = getattr(wacc_stage, "wacc_result", None)
    if wacc_result is not None and getattr(wacc_result, "wacc", None) is not None:
        wacc = _decimal(getattr(wacc_result, "wacc"))

    target = str(data.get("ticker") or data.get("target_id") or data.get("company") or "")
    if not target:
        raise RunComparisonError("canonical run result carries no resolved target identity")
    return Outcome(
        target=target,
        scenarios=scenarios,
        probabilities=tuple(probabilities),
        expected_value=expected,
        wacc=wacc,
    )


def _execute_completed(run_dir: Path, executor: Executor) -> tuple[Outcome, object]:
    response = executor(run_dir)
    if not isinstance(response, tuple) or len(response) != 4:
        raise RunComparisonError("executor must return (reached, stop_stage, stop_reason, result)")
    _reached, stop_stage, stop_reason, result = response
    if stop_stage is not None:
        raise RunComparisonError(
            f"run {run_dir} did not complete: {stop_stage}: {stop_reason}"
        )
    return _extract_outcome(result), result


def _relative_gap(a: Decimal, b: Decimal) -> Decimal:
    denominator = abs(a)
    if denominator == 0:
        return Decimal("0") if b == 0 else Decimal("Infinity")
    return abs(b - a) / denominator


def _outcome_summary(outcome: Outcome) -> dict:
    return {
        "target": outcome.target,
        "base_value_per_share": str(outcome.base_value),
        "expected_value_per_share": (
            None if outcome.expected_value is None else str(outcome.expected_value)
        ),
        "scenario_values": {key: str(value) for key, value in outcome.scenarios},
        "probabilities": {key: str(value) for key, value in outcome.probabilities},
        "wacc": None if outcome.wacc is None else str(outcome.wacc),
    }


def _threshold_findings(
    a: Outcome,
    b: Outcome,
    *,
    base_threshold: Decimal,
    probability_threshold: Decimal,
    wacc_threshold: Decimal,
) -> list[dict]:
    findings: list[dict] = []
    base_gap = _relative_gap(a.base_value, b.base_value)
    if base_gap >= base_threshold:
        findings.append(
            {
                "code": "BASE_VALUE_VARIANCE_EXCEEDED",
                "actual": str(base_gap),
                "threshold": str(base_threshold),
            }
        )

    a_probs = a.probability_map
    b_probs = b.probability_map
    if bool(a_probs) != bool(b_probs):
        findings.append(
            {
                "code": "PROBABILITY_AVAILABILITY_MISMATCH",
                "a": bool(a_probs),
                "b": bool(b_probs),
            }
        )
    elif a_probs:
        if set(a_probs) != set(b_probs):
            findings.append(
                {
                    "code": "PROBABILITY_SCENARIO_MISMATCH",
                    "a": sorted(a_probs),
                    "b": sorted(b_probs),
                }
            )
        else:
            max_gap = max(abs(a_probs[key] - b_probs[key]) for key in a_probs)
            if max_gap > probability_threshold:
                findings.append(
                    {
                        "code": "PROBABILITY_VARIANCE_EXCEEDED",
                        "actual": str(max_gap),
                        "threshold": str(probability_threshold),
                    }
                )

    if (a.wacc is None) != (b.wacc is None):
        findings.append(
            {"code": "WACC_AVAILABILITY_MISMATCH", "a": str(a.wacc), "b": str(b.wacc)}
        )
    elif a.wacc is not None and b.wacc is not None:
        gap = abs(a.wacc - b.wacc)
        if gap >= wacc_threshold:
            findings.append(
                {
                    "code": "WACC_VARIANCE_EXCEEDED",
                    "actual": str(gap),
                    "threshold": str(wacc_threshold),
                }
            )
    return findings


def _declaration_differences(run_a: Path, run_b: Path) -> list[dict]:
    payload_a = _underwriting_payload(run_a)
    payload_b = _underwriting_payload(run_b)
    a = _flatten_underwriting(payload_a)
    b = _flatten_underwriting(payload_b)
    if set(a) != set(b):
        raise RunComparisonError("underwriting contract changed after structural validation")
    differences = []
    source_a = str(payload_a.get("source_ref") or "")
    source_b = str(payload_b.get("source_ref") or "")
    if source_a != source_b:
        differences.append(
            {
                "identity": "__underwriting_header__.source_ref",
                "metric": "__underwriting_header__",
                "segment": "header",
                "multi": False,
                "header_field": "source_ref",
                "a_value": source_a,
                "b_value": source_b,
                "unit": "provenance_ref",
                "metadata_only": True,
            }
        )
    for identity in sorted(a):
        row_a = a[identity]["row"]
        row_b = b[identity]["row"]
        if _stable_json(row_a) == _stable_json(row_b):
            continue
        differences.append(
            {
                "identity": identity,
                "metric": a[identity]["metric"],
                "segment": a[identity]["segment"],
                "multi": bool(a[identity]["multi"]),
                "a_value": row_a.get("value"),
                "b_value": row_b.get("value"),
                "unit": row_a.get("unit"),
                "metadata_only": (
                    row_a.get("value") == row_b.get("value")
                    and row_a.get("unit") == row_b.get("unit")
                    and str(row_a.get("segment", "core"))
                    == str(row_b.get("segment", "core"))
                ),
                "b_row": deepcopy(row_b),
            }
        )
    return differences


def _replace_underwriting_row(payload: dict, difference: Mapping) -> None:
    header_field = difference.get("header_field")
    if header_field:
        payload[str(header_field)] = deepcopy(difference.get("b_value"))
        return
    declarations = payload.get("declarations")
    if not isinstance(declarations, dict):
        raise RunComparisonError("working underwriting declarations are invalid")
    metric = str(difference["metric"])
    replacement = deepcopy(difference["b_row"])
    if not difference["multi"]:
        declarations[metric] = replacement
        return
    rows = declarations.get(metric)
    if not isinstance(rows, list):
        raise RunComparisonError(f"working underwriting metric {metric} lost multi-row shape")
    segment = str(difference["segment"])
    matches = [index for index, row in enumerate(rows) if str(row.get("segment") or "") == segment]
    if len(matches) != 1:
        raise RunComparisonError(
            f"working underwriting row {metric}[segment={segment}] cannot be located exactly once"
        )
    rows[matches[0]] = replacement


def _copy_run_for_waterfall(source: Path, destination: Path) -> None:
    for child in source.iterdir():
        if child.name == "out":
            continue
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target)
        else:
            shutil.copy2(child, target)


def _delta_map(previous: Outcome, current: Outcome) -> dict[str, str]:
    previous_map = previous.scenario_map
    current_map = current.scenario_map
    if set(previous_map) != set(current_map):
        raise RunComparisonError("scenario values changed shape during attribution")
    return {
        key: str(current_map[key] - previous_map[key])
        for key in previous_map
    }


def _optional_delta(previous: Decimal | None, current: Decimal | None) -> str | None:
    if previous is None and current is None:
        return None
    if previous is None or current is None:
        raise RunComparisonError("expected-value availability changed during attribution")
    return str(current - previous)


def _waterfall(
    run_a: Path,
    run_b: Path,
    *,
    executor: Executor,
    outcome_a: Outcome,
    outcome_b: Outcome,
    residual_tolerance: Decimal,
) -> dict:
    differences = _declaration_differences(run_a, run_b)
    if not differences:
        residual_scenarios = _delta_map(outcome_a, outcome_b)
        residual_expected = _optional_delta(outcome_a.expected_value, outcome_b.expected_value)
        residual_base = outcome_b.base_value - outcome_a.base_value
        expected_material = (
            residual_expected is not None
            and abs(_decimal(residual_expected)) > residual_tolerance
        )
        scenario_material = any(
            abs(_decimal(value)) > residual_tolerance
            for value in residual_scenarios.values()
        )
        return {
            "decomposition_order": [],
            "judgment_differences": [],
            "attribution": [],
            "residual": {
                "base_value_per_share": str(residual_base),
                "expected_value_per_share": residual_expected,
                "scenario_values": residual_scenarios,
            },
            "residual_material": (
                abs(residual_base) > residual_tolerance
                or bool(expected_material)
                or scenario_material
            ),
            "attribution_error": None,
        }

    parent = run_a.parent
    if not parent.is_dir():
        raise RunComparisonError(f"run A parent directory does not exist: {parent}")
    attribution: list[dict] = []
    previous = outcome_a
    final = outcome_a
    error = None
    with tempfile.TemporaryDirectory(prefix=".prism-compare-", dir=str(parent)) as tmp:
        working_dir = Path(tmp)
        _copy_run_for_waterfall(run_a, working_dir)
        underwriting_path = working_dir / "declarations" / "underwriting.yaml"
        working_payload = _underwriting_payload(working_dir)
        for difference in differences:
            _replace_underwriting_row(working_payload, difference)
            underwriting_path.write_text(
                yaml.safe_dump(working_payload, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            try:
                current, _ = _execute_completed(working_dir, executor)
            except Exception as exc:  # surfaced as reconciliation, never hidden
                error = {
                    "code": "ATTRIBUTION_RUN_FAILED",
                    "identity": difference["identity"],
                    "detail": str(exc),
                }
                break
            attribution.append(
                {
                    "identity": difference["identity"],
                    "metric": difference["metric"],
                    "segment": difference["segment"],
                    "a_value": difference["a_value"],
                    "b_value": difference["b_value"],
                    "unit": difference["unit"],
                    "metadata_only": difference["metadata_only"],
                    "base_delta_per_share": str(current.base_value - previous.base_value),
                    "expected_delta_per_share": _optional_delta(
                        previous.expected_value, current.expected_value
                    ),
                    "scenario_delta_per_share": _delta_map(previous, current),
                }
            )
            previous = current
            final = current

    residual_base = outcome_b.base_value - final.base_value
    residual_expected = _optional_delta(final.expected_value, outcome_b.expected_value)
    residual_scenarios = _delta_map(final, outcome_b)
    expected_material = (
        residual_expected is not None
        and abs(_decimal(residual_expected)) > residual_tolerance
    )
    scenario_material = any(
        abs(_decimal(value)) > residual_tolerance for value in residual_scenarios.values()
    )
    return {
        "decomposition_order": [item["identity"] for item in differences],
        "judgment_differences": [
            {key: value for key, value in item.items() if key != "b_row"}
            for item in differences
        ],
        "attribution": attribution,
        "residual": {
            "base_value_per_share": str(residual_base),
            "expected_value_per_share": residual_expected,
            "scenario_values": residual_scenarios,
        },
        "residual_material": (
            abs(residual_base) > residual_tolerance
            or bool(expected_material)
            or scenario_material
        ),
        "attribution_error": error,
    }


def compare_run_directories(
    run_a: str | Path,
    run_b: str | Path,
    *,
    executor: Executor | None = None,
    base_threshold: Decimal = DEFAULT_BASE_THRESHOLD,
    probability_threshold: Decimal = DEFAULT_PROBABILITY_THRESHOLD,
    wacc_threshold: Decimal = DEFAULT_WACC_THRESHOLD,
    residual_tolerance: Decimal = DEFAULT_RESIDUAL_TOLERANCE,
) -> dict:
    a_dir = _provided_absolute(Path(run_a))
    b_dir = _provided_absolute(Path(run_b))
    receipts: dict[str, Mapping] = {}
    comparability_findings: list[dict[str, str]] = []
    try:
        receipts["runtime"] = _committed_runtime_receipt()
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        comparability_findings.append(
            {
                "code": "PRISM_COMMITTED_RUNTIME_REQUIRED",
                "run": "runtime",
                "detail": str(exc),
            }
        )
    for label, run_dir in (("run_a", a_dir), ("run_b", b_dir)):
        try:
            run_dir = _resolved_run_input(run_dir)
            if label == "run_a":
                a_dir = run_dir
            else:
                b_dir = run_dir
            receipts[label] = _committed_run_receipt(run_dir)
        except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
            comparability_findings.append(
                {
                    "code": "PRISM_COMMITTED_RUN_REQUIRED",
                    "run": label,
                    "detail": str(exc),
                }
            )
    if comparability_findings:
        return {
            "status": STATUS_EXTERNAL_RUN_NOT_COMPARABLE,
            "run_a": str(a_dir),
            "run_b": str(b_dir),
            "comparability_findings": comparability_findings,
            "repository_receipts": receipts,
            "structural_findings": [],
            "threshold_findings": [],
            "judgment_differences": [],
            "attribution": [],
            "residual": None,
            "decomposition_order": [],
            "note": "only clean PRISM run inputs committed at repository HEAD are comparable",
        }

    try:
        signature_a = _structural_signature(a_dir)
        signature_b = _structural_signature(b_dir)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        return {
            "status": STATUS_EXTERNAL_RUN_NOT_COMPARABLE,
            "run_a": str(a_dir),
            "run_b": str(b_dir),
            "comparability_findings": [
                {
                    "code": "PRISM_RUN_CONTRACT_INVALID",
                    "run": "comparison_input",
                    "detail": str(exc),
                }
            ],
            "repository_receipts": receipts,
            "structural_findings": [],
            "threshold_findings": [],
            "judgment_differences": [],
            "attribution": [],
            "residual": None,
            "decomposition_order": [],
            "note": "a committed path is not comparable unless it satisfies the PRISM run contract",
        }
    structural = _structural_findings(signature_a, signature_b)
    if structural:
        return {
            "status": STATUS_RECONCILIATION_REQUIRED,
            "run_a": str(a_dir),
            "run_b": str(b_dir),
            "repository_receipts": receipts,
            "structural_findings": structural,
            "threshold_findings": [],
            "judgment_differences": [],
            "attribution": [],
            "residual": None,
            "decomposition_order": [],
            "note": "structural disagreement is not averaged or decomposed",
        }

    execute = executor or _load_runner_executor()
    outcome_a, _result_a = _execute_completed(a_dir, execute)
    outcome_b, _result_b = _execute_completed(b_dir, execute)
    if outcome_a.target != outcome_b.target:
        return {
            "status": STATUS_RECONCILIATION_REQUIRED,
            "run_a": str(a_dir),
            "run_b": str(b_dir),
            "repository_receipts": receipts,
            "outcome_a": _outcome_summary(outcome_a),
            "outcome_b": _outcome_summary(outcome_b),
            "structural_findings": [
                {
                    "code": "TARGET_MISMATCH",
                    "field": "resolved_target",
                    "a": outcome_a.target,
                    "b": outcome_b.target,
                }
            ],
            "threshold_findings": [],
            "judgment_differences": [],
            "attribution": [],
            "residual": None,
            "decomposition_order": [],
        }

    threshold_findings = _threshold_findings(
        outcome_a,
        outcome_b,
        base_threshold=base_threshold,
        probability_threshold=probability_threshold,
        wacc_threshold=wacc_threshold,
    )
    waterfall = _waterfall(
        a_dir,
        b_dir,
        executor=execute,
        outcome_a=outcome_a,
        outcome_b=outcome_b,
        residual_tolerance=residual_tolerance,
    )
    if waterfall["residual_material"]:
        threshold_findings.append(
            {
                "code": "UNATTRIBUTED_VALUATION_RESIDUAL",
                "actual": waterfall["residual"],
                "threshold": str(residual_tolerance),
            }
        )
    if waterfall["attribution_error"] is not None:
        threshold_findings.append(waterfall["attribution_error"])

    return {
        "status": (
            STATUS_RECONCILIATION_REQUIRED
            if threshold_findings
            else STATUS_CONSISTENT
        ),
        "run_a": str(a_dir),
        "run_b": str(b_dir),
        "repository_receipts": receipts,
        "outcome_a": _outcome_summary(outcome_a),
        "outcome_b": _outcome_summary(outcome_b),
        "base_gap_ratio": str(_relative_gap(outcome_a.base_value, outcome_b.base_value)),
        "structural_findings": [],
        "threshold_findings": threshold_findings,
        **waterfall,
        "thresholds": {
            "base_gap_ratio": str(base_threshold),
            "probability_gap": str(probability_threshold),
            "wacc_gap": str(wacc_threshold),
            "residual_per_share": str(residual_tolerance),
        },
        "attribution_policy": (
            "ordered_exact_cumulative_waterfall; contributions are exact for the "
            "reported order and are not an order-independent Shapley allocation"
        ),
    }


def _format_money(value: object) -> str:
    if value is None:
        return "-"
    try:
        return f"{_decimal(value):,.0f}"
    except Exception:
        return str(value)


def _format_precise(value: object) -> str:
    if value is None:
        return "-"
    try:
        return f"{_decimal(value):,f}"
    except Exception:
        return str(value)


def render_text_report(result: Mapping) -> str:
    lines = [f"STATUS: {result['status']}"]
    if result.get("comparability_findings"):
        lines.append("COMPARABILITY:")
        for item in result["comparability_findings"]:
            lines.append(
                f"  - {item['code']} ({item.get('run', '')}): "
                f"{item.get('detail', '')}"
            )
    if result.get("outcome_a") and result.get("outcome_b"):
        a = result["outcome_a"]
        b = result["outcome_b"]
        lines.extend(
            [
                f"TARGET: {a['target']}",
                (
                    "BASE: "
                    f"A {_format_money(a['base_value_per_share'])} -> "
                    f"B {_format_money(b['base_value_per_share'])} "
                    f"(gap {Decimal(str(result.get('base_gap_ratio', '0'))) * 100:.1f}%)"
                ),
                (
                    "EXPECTED: "
                    f"A {_format_money(a['expected_value_per_share'])} -> "
                    f"B {_format_money(b['expected_value_per_share'])}"
                ),
                f"WACC: A {a['wacc']} -> B {b['wacc']}",
            ]
        )
    if result.get("structural_findings"):
        lines.append("STRUCTURAL:")
        for item in result["structural_findings"]:
            lines.append(f"  - {item['code']}: {item.get('field', '')}")
        lines.append("  attribution skipped: structural disagreement is never averaged")
    if result.get("threshold_findings"):
        lines.append("RECONCILIATION TRIGGERS:")
        for item in result["threshold_findings"]:
            lines.append(f"  - {item['code']}")
    attribution = result.get("attribution") or []
    if attribution:
        lines.append("JUDGMENT WATERFALL (A -> B, deterministic key order):")
        lines.append("  key | Base delta/share | Expected delta/share")
        for item in attribution:
            lines.append(
                "  "
                f"{item['identity']} | {_format_money(item['base_delta_per_share'])} | "
                f"{_format_money(item['expected_delta_per_share'])}"
            )
    if result.get("residual") is not None:
        residual = result["residual"]
        lines.append(
            "RESIDUAL: Base "
            + _format_precise(residual["base_value_per_share"])
            + " / Expected "
            + _format_precise(residual["expected_value_per_share"])
        )
        scenario_values = residual.get("scenario_values") or {}
        if scenario_values:
            lines.append(
                "RESIDUAL SCENARIOS: "
                + ", ".join(
                    f"{key}={_format_precise(value)}"
                    for key, value in sorted(scenario_values.items())
                )
            )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "exit codes: 0 CONSISTENT, 3 RECONCILIATION_REQUIRED, "
            "4 EXTERNAL_RUN_NOT_COMPARABLE, 1 comparison/execution error; "
            "argparse usage errors remain 2"
        ),
    )
    parser.add_argument("run_a")
    parser.add_argument("run_b")
    parser.add_argument(
        "--base-threshold-pct",
        type=Decimal,
        default=DEFAULT_BASE_THRESHOLD * 100,
        help="Base value gap in percent (default: 20)",
    )
    parser.add_argument(
        "--probability-threshold-pp",
        type=Decimal,
        default=DEFAULT_PROBABILITY_THRESHOLD * 100,
        help="scenario probability gap in percentage points (default: 10)",
    )
    parser.add_argument(
        "--wacc-threshold-pp",
        type=Decimal,
        default=DEFAULT_WACC_THRESHOLD * 100,
        help="WACC gap in percentage points (default: 1)",
    )
    parser.add_argument(
        "--residual-tolerance", type=Decimal, default=DEFAULT_RESIDUAL_TOLERANCE
    )
    parser.add_argument("--json-out")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = compare_run_directories(
            args.run_a,
            args.run_b,
            base_threshold=args.base_threshold_pct / Decimal("100"),
            probability_threshold=args.probability_threshold_pp / Decimal("100"),
            wacc_threshold=args.wacc_threshold_pp / Decimal("100"),
            residual_tolerance=args.residual_tolerance,
        )
    except Exception as exc:
        print(f"ERROR [COMPARE_RUNS] {exc}", file=sys.stderr)
        return 1
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text_report(result))
    if result["status"] == STATUS_RECONCILIATION_REQUIRED:
        return EXIT_RECONCILIATION_REQUIRED
    if result["status"] == STATUS_EXTERNAL_RUN_NOT_COMPARABLE:
        return EXIT_EXTERNAL_RUN_NOT_COMPARABLE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
