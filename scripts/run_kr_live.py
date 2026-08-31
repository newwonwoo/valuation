#!/usr/bin/env python3
"""The runbook runner: one prepared run directory in, one attested report out.

This is the executable half of docs/RUNBOOK_KR_LIVE.md. A live run's materials —
raw DART payloads fetched from public endpoints, the operator's declaration
files, and the staff seats' proposal JSONs — live together in one run
directory, and this runner replays them through the canonical attested
runtime with the engine untouched:

    PYTHONPATH=src python scripts/run_kr_live.py runs/kisco-104700

Directory convention (see the committed runs/kisco-104700 for a live example):

    <run_dir>/
      run.yaml                  the run declaration (company, method, as_of,
                                filing selection, scenarios, optional
                                calibration binding)
      raw/
        corp_search.json        find_company hits (builds the corpCode archive)
        list.json               filings list, raw OpenDART shape
        company.json            company profile, raw OpenDART shape
        fnltt_<year>_<fs>.json  full financial statement, raw OpenDART shape
        filing_<rcept_no>/      original-filing text members (viewer sections)
      declarations/
        underwriting.yaml       operator judgments (required)
        market.yaml             post-freeze market price (optional)
        street.json             authorized street export (optional; an empty
                                reports list declares no coverage)
        risk_pack.yaml          declared risk pack for beta/WACC methods
                                (optional)
        staff/<role>.json       one proposal file per staff seat; a JSON array
                                scripts successive turns of the repair loop

Paths inside run.yaml resolve relative to the run directory, so a run may
point at shared artifacts in config/ (the KR steel calibration does).

The runner is deployment, not engine: it builds the injected network from the
raw files, the transport from the staff files, and the GenericKRRuntimeSpec
from run.yaml — exactly what a chat front end does with live fetches. Replay
of a committed run directory is therefore a full-pipeline regression: the
stage list, the frozen values and the report must all reproduce.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
from hashlib import sha256
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from urllib.parse import parse_qs, urlparse
from zipfile import ZipFile

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from valuation_engine.cli_runtime import LiveAnalysisRequest  # noqa: E402
from valuation_engine.control_plane import StageStatus  # noqa: E402
from valuation_engine.generic_live_providers import (  # noqa: E402
    GenericKRRuntimeSpec,
    build_generic_kr_runtime_factory,
)
from valuation_engine.kr_opendart_provider import (  # noqa: E402
    OpenDartFilingSelection,
    OpenDartNetwork,
)
from valuation_engine.strict_live_runtime import run_prism  # noqa: E402
from valuation_engine.valuation_plan_compiler import SegmentMethodChoice  # noqa: E402


_PASSING = {
    StageStatus.PASS,
    StageStatus.WARNING,
    StageStatus.SKIPPED_NOT_APPLICABLE,
    StageStatus.RECOVERED,
}


class RunbookError(ValueError):
    pass


def _load_run(run_dir: Path) -> dict:
    payload = yaml.safe_load((run_dir / "run.yaml").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RunbookError("run.yaml must be a mapping")
    return payload


def _resolve(run_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (run_dir / path).resolve()


def _build_network(run_dir: Path) -> OpenDartNetwork:
    raw = run_dir / "raw"
    search = json.loads((raw / "corp_search.json").read_text(encoding="utf-8"))
    companies = search.get("companies") or []
    if not companies:
        raise RunbookError("raw/corp_search.json carries no companies")

    def corp_archive() -> bytes:
        rows = "".join(
            "<list>"
            f"<corp_code>{row['corp_code']}</corp_code>"
            f"<corp_name>{row['corp_name']}</corp_name>"
            f"<stock_code>{row.get('stock_code') or ' '}</stock_code>"
            "<modify_date>20260801</modify_date></list>"
            for row in companies
        )
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("CORPCODE.xml", f"<result>{rows}</result>")
        return buffer.getvalue()

    def filing_archive(rcept_no: str) -> bytes:
        directory = raw / f"filing_{rcept_no}"
        members = sorted(directory.glob("*")) if directory.is_dir() else ()
        if not members:
            raise RunbookError(
                f"raw/filing_{rcept_no}/ is missing or empty; fetch the filing's "
                "sections per the runbook before running"
            )
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            for member in members:
                archive.writestr(member.name, member.read_text(encoding="utf-8"))
        return buffer.getvalue()

    def fetch_text(url: str) -> str:
        if "list.json" in url:
            return (raw / "list.json").read_text(encoding="utf-8")
        if "company.json" in url:
            return (raw / "company.json").read_text(encoding="utf-8")
        if "fnlttSinglAcnt" in url:
            params = parse_qs(urlparse(url).query)
            year = (params.get("bsns_year") or [""])[0]
            fs_div = (params.get("fs_div") or [""])[0]
            candidate = raw / f"fnltt_{year}_{fs_div}.json"
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
            return json.dumps({"status": "013", "message": "조회된 데이타가 없습니다."})
        raise RunbookError(f"no raw fixture routes text URL: {url}")

    def fetch_bytes(url: str) -> bytes:
        if "corpCode.xml" in url:
            return corp_archive()
        if "document.xml" in url:
            params = parse_qs(urlparse(url).query)
            rcept_no = (params.get("rcept_no") or [""])[0]
            return filing_archive(rcept_no)
        raise RunbookError(f"no raw fixture routes binary URL: {url}")

    return OpenDartNetwork(
        fetch_text=fetch_text, fetch_bytes=fetch_bytes, api_key="RUNBOOK-PUBLIC"
    )


class _StaffTransport:
    """Per-role proposal files; an array scripts the repair loop's turns.

    The last answer repeats so a rejection surfaces as the engine's own
    contract error, never as transport exhaustion.

    When ``VALUATION_LLM_TRANSPORT`` is set, roles WITHOUT a file are
    delegated to that live transport (same ``module:callable`` contract as
    ``generic_kr_cli``) — a declared file always wins, so a committed run
    replays byte-identically whether or not a live model is configured.
    """

    def __init__(self, staff_dir: Path) -> None:
        self._answers: dict[str, list[str]] = {}
        if staff_dir.is_dir():
            for path in staff_dir.glob("*.json"):
                payload = json.loads(path.read_text(encoding="utf-8"))
                turns = payload if isinstance(payload, list) else [payload]
                self._answers[path.stem] = [
                    json.dumps(turn, ensure_ascii=False) for turn in turns
                ]
        self._counts: dict[str, int] = {}
        self._live = None

    def _live_transport(self):
        if self._live is None:
            from valuation_engine.generic_kr_cli import _load_transport

            self._live = _load_transport()
        return self._live

    def complete(self, *, role: str, prompt: str) -> str:
        answers = self._answers.get(role)
        if not answers:
            if os.environ.get("VALUATION_LLM_TRANSPORT", "").strip():
                return self._live_transport().complete(role=role, prompt=prompt)
            raise RunbookError(
                f"no staff proposal file for role {role!r}; add "
                f"declarations/staff/{role}.json per the runbook, or set "
                "VALUATION_LLM_TRANSPORT to let a live model take the seat"
            )
        index = self._counts.get(role, 0)
        self._counts[role] = index + 1
        return answers[min(index, len(answers) - 1)]


def _calibration_loader(run_dir: Path, calibration: dict):
    from decimal import Decimal

    from valuation_engine.continuous_probability_assembly import (
        ContinuousCalibrationBinding,
        ContinuousConditioning,
        build_continuous_probability_snapshot,
    )

    constants = calibration["constants"]
    conditioning_payload = json.loads(
        _resolve(run_dir, calibration["conditioning"]).read_text(encoding="utf-8")
    )
    binding = ContinuousCalibrationBinding(
        cohort_key=calibration["cohort_key"],
        forecast_class=calibration["forecast_class"],
        horizon=calibration["horizon"],
        method_version=calibration["method_version"],
        mapping_version=calibration["mapping_version"],
        driver_ids=tuple(calibration["driver_ids"]),
        scenario_ids=tuple(calibration["scenario_ids"]),
        path_length=int(calibration["path_length"]),
        artifact_path=_resolve(run_dir, calibration["artifact"]),
        provenance_path=_resolve(run_dir, calibration["provenance"]),
        expected_artifact_sha256=constants["expected_artifact_sha256"],
        expected_provenance_artifact_sha256=constants[
            "expected_provenance_artifact_sha256"
        ],
        expected_dataset_sha256=constants["expected_dataset_sha256"],
        expected_provenance_hash=constants["expected_provenance_hash"],
        expected_source_row_count=int(constants["expected_source_row_count"]),
        expected_source_company_count=int(
            constants["expected_source_company_count"]
        ),
        excluded_ticker=constants["excluded_ticker"],
        credible_level=Decimal(str(calibration.get("credible_level", "0.90"))),
        outer_draws=int(calibration.get("outer_draws", 300)),
        inner_draws=int(calibration.get("inner_draws", 200)),
        seed=int(calibration["seed"]),
    )
    conditioning = ContinuousConditioning(
        readings=tuple(
            sorted(
                (key, Decimal(str(value)))
                for key, value in conditioning_payload["values"].items()
            )
        ),
        source_ref=conditioning_payload["source_ref"],
        first_seen_at=conditioning_payload["first_seen_at"],
        source_hash=conditioning_payload["source_hash"],
    )

    def load(as_of: str):
        snapshot = build_continuous_probability_snapshot(
            binding=binding, conditioning=conditioning, as_of_date=as_of
        )
        return lambda _context: snapshot

    return load


def _optional_path(run_dir: Path, name: str) -> str | None:
    path = run_dir / "declarations" / name
    return str(path) if path.exists() else None


def _safe_artifact_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-")
    if not token:
        raise RunbookError("report artifact token cannot be empty")
    return token


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _run_input_sha256(run_dir: str | Path) -> str:
    """Fingerprint every prepared and runtime input that can affect a replay.

    ``out/`` is deliberately excluded because it is the result store. The
    engine package, registries and this runner are included so an unchanged
    prepared directory is still re-executed after valuation logic changes.
    Existing file paths referenced by ``run.yaml`` are also bound, including
    calibration artifacts that live outside the prepared directory.
    """
    run_dir = Path(run_dir).resolve()
    config = _load_run(run_dir)
    receipts: dict[str, dict[str, object]] = {}

    def add(label: str, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.is_file():
            raise RunbookError(f"run input is not a readable file: {resolved}")
        receipts[label] = {
            "sha256": _file_sha256(resolved),
            "size_bytes": resolved.stat().st_size,
        }

    for path in sorted(run_dir.rglob("*")):
        relative = path.relative_to(run_dir)
        if relative.parts and relative.parts[0] == "out":
            continue
        if path.is_file():
            add(f"run/{relative.as_posix()}", path)

    runtime_roots = (ROOT / "src" / "valuation_engine", ROOT / "config")
    for base in runtime_roots:
        for path in sorted(base.rglob("*")):
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix not in {".pyc", ".pyo"}
            ):
                add(f"repo/{path.relative_to(ROOT).as_posix()}", path)
    for path in (Path(__file__).resolve(), ROOT / "pyproject.toml"):
        add(f"repo/{path.relative_to(ROOT).as_posix()}", path)

    def bind_referenced_files(value: object, pointer: str = "run.yaml") -> None:
        if isinstance(value, dict):
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
                bind_referenced_files(item, f"{pointer}/{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                bind_referenced_files(item, f"{pointer}/{index}")
        elif isinstance(value, str):
            candidate = _resolve(run_dir, value)
            if candidate.is_file():
                add(f"reference/{pointer}", candidate)

    bind_referenced_files(config)
    live_transport_binding = os.environ.get("VALUATION_LLM_TRANSPORT", "").strip()
    live_transport: dict[str, object] | None = None
    if live_transport_binding:
        module_name, separator, callable_name = live_transport_binding.partition(":")
        if not separator or not module_name or not callable_name:
            raise RunbookError(
                "VALUATION_LLM_TRANSPORT must be a module:callable binding"
            )
        module_spec = importlib.util.find_spec(module_name)
        module_origin = Path(str(module_spec.origin)).resolve() if (
            module_spec is not None and module_spec.origin
        ) else None
        if module_origin is None or not module_origin.is_file():
            raise RunbookError(
                f"live transport module cannot be fingerprinted: {module_name}"
            )
        add("live_transport/module", module_origin)
        # Never bind the credential itself. These are the non-secret settings
        # the committed Anthropic transport reads and that can change model
        # proposals for otherwise identical prepared inputs.
        live_transport = {
            "binding": live_transport_binding,
            "model": os.environ.get("VALUATION_LLM_MODEL", "").strip(),
            "base_url": os.environ.get("ANTHROPIC_BASE_URL", "").strip(),
            "max_tokens": os.environ.get("VALUATION_LLM_MAX_TOKENS", "").strip(),
        }
    contract = {
        "schema_version": "kr-live-run-inputs/v1",
        "files": tuple(
            {"path": label, **receipt}
            for label, receipt in sorted(receipts.items())
        ),
        "live_transport": live_transport,
    }
    encoded = json.dumps(
        contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _reference_value_per_share(result) -> Decimal:
    valuation = result.data.get("generic_valuation_result")
    scenarios = tuple(getattr(valuation, "scenarios", ()))
    if not scenarios:
        raise RunbookError("completed run carries no intrinsic scenario values")
    expected = getattr(valuation, "expected_value_per_share", None)
    if expected is not None:
        return Decimal(expected)
    preferred = next(
        (
            item
            for item in scenarios
            if getattr(item, "scenario_id", "") in {"Base", "Core"}
        ),
        scenarios[0],
    )
    return Decimal(getattr(preferred, "value_per_share"))


def _write_json_atomic(path: Path, payload: dict, *, token: str) -> None:
    temporary = path.parent / f".{path.name}.{token}.tmp"
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def publish_report_bundle(
    run_dir: str | Path,
    result,
    *,
    output_dir: str | Path | None = None,
    report_alias: str | Path | None = None,
) -> dict:
    """Persist the complete immutable run bundle plus a hash-bound latest manifest."""
    run_dir = Path(run_dir).resolve()
    output_root = Path(output_dir or run_dir / "out").resolve()
    source_raw = result.data.get("saved_run_dir")
    if not isinstance(source_raw, str) or not source_raw:
        raise RunbookError("completed run carries no saved_run_dir")
    source = Path(source_raw).resolve()
    visuals = tuple(result.data.get("saved_report_visuals") or ())
    required = (
        "manifest.json",
        "control_plane_trace.json",
        "audit.json",
        "final_report.md",
        "execution_attestation.json",
        *visuals,
    )
    missing = tuple(name for name in required if not (source / name).is_file())
    if missing:
        raise RunbookError(
            "completed run bundle is incomplete: " + ", ".join(missing)
        )

    report = (source / "final_report.md").read_text(encoding="utf-8")
    valuation_hash = str(result.data.get("valuation_hash") or "")
    audit_hash = str(result.data.get("audit_hash") or "")
    run_id = str(getattr(result, "run_id", "") or "")
    ticker = str(result.data.get("ticker") or "")
    config = _load_run(run_dir)
    as_of = str(config.get("as_of") or "")
    run_input_sha256 = _run_input_sha256(run_dir)
    if not all((valuation_hash, audit_hash, run_id, ticker, as_of)):
        raise RunbookError("completed run lacks report artifact identities")
    reference = _reference_value_per_share(result)
    reference_token = f"TP{reference.quantize(Decimal('1')):.0f}"
    seed = "|".join(
        (
            "kr-live-report-bundle/v1",
            ticker,
            as_of,
            run_id,
            valuation_hash,
            audit_hash,
            run_input_sha256,
            sha256(report.encode("utf-8")).hexdigest(),
            _file_sha256(source / "manifest.json"),
        )
    )
    short_hash = sha256(seed.encode("utf-8")).hexdigest()[:12].upper()
    artifact_id = "-".join(
        (
            _safe_artifact_token(ticker),
            _safe_artifact_token(as_of.replace("-", "")),
            reference_token,
            short_hash,
        )
    )
    filename_base = artifact_id.replace("-", "_")
    bundle_relative = Path("bundles") / filename_base
    bundle_dir = output_root / bundle_relative
    if bundle_dir.exists():
        raise RunbookError(f"immutable report bundle already exists: {bundle_dir}")
    bundle_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, bundle_dir)

    versioned_report_name = f"{filename_base}.md"
    versioned_report = (
        report.rstrip() + f"\n\n---\n보고서 ID `{artifact_id}`\n"
    )
    (bundle_dir / versioned_report_name).write_text(
        versioned_report, encoding="utf-8"
    )
    files = tuple(
        {
            "filename": path.relative_to(bundle_dir).as_posix(),
            "sha256": _file_sha256(path),
        }
        for path in sorted(bundle_dir.rglob("*"))
        if path.is_file()
    )
    bundle_manifest = {
        "schema_version": "kr-live-report-bundle/v1",
        "artifact_id": artifact_id,
        "as_of": as_of,
        "run_id": run_id,
        "ticker": ticker,
        "reference_value_per_share": str(reference),
        "valuation_hash": valuation_hash,
        "audit_hash": audit_hash,
        "run_input_sha256": run_input_sha256,
        "report_filename": versioned_report_name,
        "files": files,
    }
    bundle_manifest_path = bundle_dir / "report_bundle_manifest.json"
    _write_json_atomic(bundle_manifest_path, bundle_manifest, token=short_hash)

    latest_name = f"{_safe_artifact_token(ticker)}_LATEST_REPORT.json"
    latest_path = output_root / latest_name
    latest = {
        "schema_version": "kr-live-latest-report/v1",
        "artifact_id": artifact_id,
        "as_of": as_of,
        "bundle_directory": bundle_relative.as_posix(),
        "bundle_manifest": (
            bundle_relative / bundle_manifest_path.name
        ).as_posix(),
        "bundle_manifest_sha256": _file_sha256(bundle_manifest_path),
        "report_filename": (
            bundle_relative / versioned_report_name
        ).as_posix(),
        "report_sha256": _file_sha256(bundle_dir / versioned_report_name),
        "valuation_hash": valuation_hash,
        "audit_hash": audit_hash,
        "run_input_sha256": run_input_sha256,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(latest_path, latest, token=short_hash)

    alias = Path(report_alias) if report_alias else output_root / "final_report.md"
    alias.parent.mkdir(parents=True, exist_ok=True)
    temporary_alias = alias.parent / f".{alias.name}.{short_hash}.tmp"
    temporary_alias.write_text(report, encoding="utf-8")
    os.replace(temporary_alias, alias)
    return {
        **latest,
        "latest_manifest_path": str(latest_path),
        "versioned_report_path": str(bundle_dir / versioned_report_name),
    }


def _resolve_manifest_path(root: Path, relative: object, *, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise RunbookError(f"published report {label} path is missing")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RunbookError(
            f"published report {label} escapes its output directory"
        ) from exc
    return candidate


def reuse_published_report_bundle(
    run_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    report_alias: str | Path | None = None,
) -> dict | None:
    """Return a matching completed bundle only after verifying every bound hash.

    A normal second invocation must not replay a fixed run ID into the immutable
    StateStore. It reuses the already-published result, but only when the latest
    pointer, bundle manifest, versioned report and every recorded bundle file
    are byte-identical to their receipts.
    """
    run_dir = Path(run_dir).resolve()
    output_root = Path(output_dir or run_dir / "out").resolve()
    if not output_root.is_dir():
        return None
    config = _load_run(run_dir)
    expected_run_id = str(config.get("run_id", f"RUNBOOK-{run_dir.name}"))
    expected_as_of = str(config.get("as_of") or "")
    expected_run_input_sha256 = _run_input_sha256(run_dir)
    for latest_path in sorted(output_root.glob("*_LATEST_REPORT.json")):
        try:
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RunbookError(
                f"published latest-report manifest is unreadable: {latest_path}"
            ) from exc
        if not isinstance(latest, dict) or latest.get("schema_version") != (
            "kr-live-latest-report/v1"
        ):
            raise RunbookError(
                f"published latest-report manifest has unsupported schema: {latest_path}"
            )
        bundle_manifest_path = _resolve_manifest_path(
            output_root, latest.get("bundle_manifest"), label="bundle manifest"
        )
        if not bundle_manifest_path.is_file():
            raise RunbookError(
                f"published bundle manifest is missing: {bundle_manifest_path}"
            )
        if _file_sha256(bundle_manifest_path) != latest.get(
            "bundle_manifest_sha256"
        ):
            raise RunbookError("published bundle manifest hash mismatch")
        try:
            bundle_manifest = json.loads(
                bundle_manifest_path.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            raise RunbookError("published bundle manifest is invalid JSON") from exc
        if (
            bundle_manifest.get("run_id") != expected_run_id
            or bundle_manifest.get("as_of") != expected_as_of
            or bundle_manifest.get("run_input_sha256")
            != expected_run_input_sha256
        ):
            continue
        bundle_dir = _resolve_manifest_path(
            output_root, latest.get("bundle_directory"), label="bundle directory"
        )
        if bundle_manifest_path.parent != bundle_dir:
            raise RunbookError("published bundle manifest is outside its bundle directory")
        if bundle_manifest.get("schema_version") != "kr-live-report-bundle/v1":
            raise RunbookError("published bundle manifest has unsupported schema")
        for key in (
            "artifact_id",
            "as_of",
            "valuation_hash",
            "audit_hash",
            "run_input_sha256",
        ):
            if latest.get(key) != bundle_manifest.get(key):
                raise RunbookError(f"published latest manifest disagrees on {key}")
        versioned_report_path = _resolve_manifest_path(
            output_root, latest.get("report_filename"), label="versioned report"
        )
        if versioned_report_path.parent != bundle_dir:
            raise RunbookError("published versioned report is outside its bundle directory")
        if versioned_report_path.name != bundle_manifest.get("report_filename"):
            raise RunbookError("published bundle disagrees on report filename")
        if (
            not versioned_report_path.is_file()
            or _file_sha256(versioned_report_path) != latest.get("report_sha256")
        ):
            raise RunbookError("published versioned report hash mismatch")
        files = bundle_manifest.get("files")
        if not isinstance(files, list) or not files:
            raise RunbookError("published bundle manifest carries no file receipts")
        received_names = {
            receipt.get("filename")
            for receipt in files
            if isinstance(receipt, dict)
        }
        required_names = {
            "manifest.json",
            "control_plane_trace.json",
            "audit.json",
            "final_report.md",
            "execution_attestation.json",
            str(bundle_manifest.get("report_filename") or ""),
        }
        missing_names = tuple(sorted(required_names - received_names))
        if missing_names:
            raise RunbookError(
                "published bundle is missing required file receipts: "
                + ", ".join(missing_names)
            )
        for receipt in files:
            if not isinstance(receipt, dict):
                raise RunbookError("published bundle file receipt is invalid")
            path = _resolve_manifest_path(
                bundle_dir, receipt.get("filename"), label="bundle file"
            )
            if not path.is_file() or _file_sha256(path) != receipt.get("sha256"):
                raise RunbookError(
                    f"published bundle file hash mismatch: {receipt.get('filename')}"
                )
        raw_report = bundle_dir / "final_report.md"
        if not raw_report.is_file():
            raise RunbookError("published bundle is missing final_report.md")
        try:
            run_manifest = json.loads(
                (bundle_dir / "manifest.json").read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            raise RunbookError("published run manifest is invalid JSON") from exc
        if (
            run_manifest.get("run_id") != expected_run_id
            or run_manifest.get("ticker") != bundle_manifest.get("ticker")
            or run_manifest.get("status") != "COMPLETED"
            or run_manifest.get("audit_passed") is not True
        ):
            raise RunbookError("published run manifest identity/status mismatch")
        report = raw_report.read_text(encoding="utf-8")
        alias = Path(report_alias) if report_alias else output_root / "final_report.md"
        alias.parent.mkdir(parents=True, exist_ok=True)
        token = sha256(str(latest["artifact_id"]).encode("utf-8")).hexdigest()[:12]
        temporary_alias = alias.parent / f".{alias.name}.{token}.tmp"
        temporary_alias.write_text(report, encoding="utf-8")
        os.replace(temporary_alias, alias)
        return {
            **latest,
            "latest_manifest_path": str(latest_path),
            "versioned_report_path": str(versioned_report_path),
            "reused": True,
        }
    return None


def execute_run(run_dir: str | Path, *, state_root: str | None = None):
    """Run one prepared directory; returns (reached, stop_stage, stop_reason, result)."""
    run_dir = Path(run_dir).resolve()
    config = _load_run(run_dir)
    filing = config["filing"]
    archetype, _, rest = str(config["method"]).partition("/")
    method, _, version = rest.partition("/")
    if not archetype or not method:
        raise RunbookError("run.yaml method must be 'archetype/method[/version]'")

    calibration = config.get("calibration")
    spec_kwargs: dict = {}
    if calibration:
        spec_kwargs.update(
            calibration_snapshot_loader=_calibration_loader(run_dir, calibration)(
                str(config["as_of"])
            ),
            calibration_cohort_key=calibration["cohort_key"],
            external_probability_source=calibration["external_probability_source"],
        )
    market_path = _optional_path(run_dir, "market.yaml")
    spec = GenericKRRuntimeSpec(
        as_of=str(config["as_of"]),
        scenario_ids=tuple(config["scenario_ids"]),
        method_choices=(
            SegmentMethodChoice(
                str(filing.get("segment_id", "core")),
                archetype,
                method,
                version or None,
            ),
        ),
        filing=OpenDartFilingSelection(
            business_year=str(filing["business_year"]),
            report_code=str(filing.get("report_code", "11011")),
            fs_div=str(filing.get("fs_div", "CFS")),
            fiscal_period_end=str(filing["fiscal_period_end"]),
            checked_at=str(config["as_of"]),
            segment_id=str(filing.get("segment_id", "core")),
        ),
        forecast_years=int(config.get("forecast_years", 5)),
        declared_underwriting_path=str(run_dir / "declarations" / "underwriting.yaml"),
        declared_risk_path=_optional_path(run_dir, "risk_pack.yaml"),
        declared_segments_path=_optional_path(run_dir, "segments.yaml"),
        extra_required_evidence=tuple(config.get("extra_required_evidence", ())),
        market_config_path=market_path,
        street_export_path=_optional_path(run_dir, "street.json"),
        market_currency=(
            str(config.get("market_currency", "KRW")) if market_path else None
        ),
        **spec_kwargs,
    )
    factory = build_generic_kr_runtime_factory(
        network=_build_network(run_dir),
        transport=_StaffTransport(run_dir / "declarations" / "staff"),
        spec=spec,
    )

    def run(root: str):
        request = LiveAnalysisRequest(
            command=f"분석시작 {config['company_query']}",
            company_query=str(config["company_query"]),
            state_root=root,
            run_id=str(config.get("run_id", f"RUNBOOK-{run_dir.name}")),
            jurisdiction=str(config.get("jurisdiction", "KR")),
        )
        return run_prism(factory(request)).result

    if state_root is not None:
        result = run(state_root)
    else:
        with tempfile.TemporaryDirectory(prefix="kr-live-run-") as root:
            result = run(root)

    reached: list[str] = []
    stop_stage = None
    stop_reason = ""
    for trace in result.stage_traces:
        if trace.status in _PASSING:
            reached.append(trace.stage)
        else:
            stop_stage = trace.stage
            stop_reason = f"{trace.status.value}: {trace.rationale}"
            break
    return tuple(reached), stop_stage, stop_reason, result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", help="prepared run directory (see runbook)")
    parser.add_argument(
        "--report-out",
        help="write the mutable latest-report alias here (immutable bundle stays under <run_dir>/out/bundles)",
    )
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    output_root = run_dir / "out"
    reused = reuse_published_report_bundle(
        run_dir,
        output_dir=output_root,
        report_alias=args.report_out,
    )
    if reused is not None:
        print("\n  stages: previously completed — REUSED")
        print(f"  report: {reused['versioned_report_path']}")
        print(f"  manifest: {reused['latest_manifest_path']}")
        return 0
    reached, stop_stage, stop_reason, result = execute_run(
        run_dir, state_root=str(output_root / "state" / _run_input_sha256(run_dir))
    )
    for stage in reached:
        print(f"  OK  {stage}")
    if stop_stage is not None:
        print(f"  STOP {stop_stage}  {stop_reason}")
        print(f"\n  stages: {len(reached)}/{len(result.stage_traces)}")
        print(
            "\nThe stop message above names exactly what the run still needs — "
            "that is the work order, not a crash. See docs/RUNBOOK_KR_LIVE.md."
        )
        return 1
    print(f"\n  stages: {len(reached)}/{len(result.stage_traces)} — COMPLETED")
    report = result.data.get("final_report")
    if isinstance(report, str):
        published = publish_report_bundle(
            run_dir,
            result,
            output_dir=output_root,
            report_alias=args.report_out,
        )
        print(f"  report: {published['versioned_report_path']}")
        print(f"  manifest: {published['latest_manifest_path']}")
        for line in report.splitlines():
            if "내재가치" in line or "기대값" in line or "상승여력" in line:
                print("  " + line.strip("- *"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
