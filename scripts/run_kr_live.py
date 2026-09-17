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

from valuation_engine.calibration_cohort_registry import (  # noqa: E402
    CalibrationCohortRegistryError,
    load_production_calibration_registry,
    resolve_production_calibration_cohort,
    validate_declared_calibration,
)
from valuation_engine.canonical_completion import (  # noqa: E402
    BUNDLE_MANIFEST_NAME,
    BUNDLE_MANIFEST_SCHEMA,
    CANONICAL_ENTRYPOINT_ID,
    CompletionProofError,
    LATEST_MANIFEST_SCHEMA,
    validate_completion_bundle,
)
from valuation_engine.cli_runtime import LiveAnalysisRequest  # noqa: E402
from valuation_engine.control_plane import StageStatus  # noqa: E402
from valuation_engine.declared_segments import load_declared_segments  # noqa: E402
from valuation_engine.generic_kr_industry import (  # noqa: E402
    fetch_opendart_company_profile,
    opendart_filing_snapshot_loader,
)
from valuation_engine.generic_live_providers import (  # noqa: E402
    GenericKRRuntimeSpec,
    build_generic_kr_runtime_factory,
)
from valuation_engine.kr_opendart_provider import (  # noqa: E402
    OpenDartFilingSelection,
    OpenDartNetwork,
)
from valuation_engine.investor_report import (  # noqa: E402
    load_investor_report_profile,
    probability_weighted_equity_value,
    render_investor_report,
)
from valuation_engine.live_primary_adapters import (  # noqa: E402
    CompanyResolutionRequest,
    live_opendart_company_resolver,
)
from valuation_engine.strict_live_runtime import run_prism  # noqa: E402
from valuation_engine.staff_transport import StaffTransport, StaffWorkRequired  # noqa: E402
from valuation_engine.valuation_execution import ParentAdjustmentPlan  # noqa: E402
from valuation_engine.valuation_plan_compiler import SegmentMethodChoice  # noqa: E402


_PASSING = {
    StageStatus.PASS,
    StageStatus.WARNING,
    StageStatus.SKIPPED_NOT_APPLICABLE,
    StageStatus.RECOVERED,
}


class RunbookError(ValueError):
    pass


class _StaffUnavailableError(RunbookError, StaffWorkRequired):
    """A missing proposal is a reader failure, not undisclosed evidence."""


class _TargetProfileMismatchError(RunbookError):
    """Resolved target and OpenDART company profile identify different issuers."""

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


class _StaffTransport(StaffTransport):
    """Preserve the runbook error contract while exposing repair handoffs."""

    def complete(self, *, role: str, prompt: str) -> str:
        try:
            return super().complete(role=role, prompt=prompt)
        except StaffWorkRequired as exc:
            raise _StaffUnavailableError(str(exc)) from exc


def _calibration_loader(run_dir: Path, calibration: dict):
    from decimal import Decimal

    from valuation_engine.continuous_probability_assembly import (
        ContinuousCalibrationBinding,
        ContinuousConditioning,
        SELF_PROBABILITY_SOURCE,
        build_continuous_probability_snapshot,
    )

    constants = calibration["constants"]
    self_calibrated = bool(calibration.get("self_calibrated", False))
    declared_source = str(calibration.get("external_probability_source") or "")
    if self_calibrated != (declared_source == SELF_PROBABILITY_SOURCE):
        raise RunbookError(
            "run.yaml calibration self_calibrated flag contradicts "
            f"external_probability_source={declared_source!r}; target-history "
            f"calibration must declare {SELF_PROBABILITY_SOURCE}"
        )
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
        self_calibrated=self_calibrated,
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


def _resolved_target_context(run_dir: Path, config: dict, network: OpenDartNetwork):
    resolver = live_opendart_company_resolver(
        network.fetch_bytes, api_key=network.api_key
    )
    identity = resolver(
        CompanyResolutionRequest(
            query=str(config["company_query"]),
            jurisdiction=str(config.get("jurisdiction", "KR")),
        )
    )
    identity.validate()
    corp_code = next(
        (value for key, value in identity.external_ids if key == "opendart_corp_code"),
        "",
    )
    if not corp_code:
        raise RunbookError(
            "resolved target carries no OpenDART corp code for calibration preflight"
        )
    profile = fetch_opendart_company_profile(
        network.fetch_text,
        corp_code=corp_code,
        api_key=network.api_key,
    )
    if profile.corp_code and profile.corp_code != corp_code:
        raise _TargetProfileMismatchError(
            "OpenDART company profile corp code disagrees with resolved target: "
            f"profile={profile.corp_code}, resolved={corp_code}"
        )
    if profile.stock_code and profile.stock_code != identity.ticker:
        raise _TargetProfileMismatchError(
            "OpenDART company profile ticker disagrees with resolved target: "
            f"profile={profile.stock_code}, resolved={identity.ticker}"
        )
    return identity, corp_code, profile


def _run_industry_code(
    run_dir: Path,
    config: dict,
    *,
    identity,
    network: OpenDartNetwork,
    company_industry_code: str,
) -> str | None:
    """Return a cohort KSIC only after canonical scope validation succeeds.

    If the existing company/IFRS 8 contract is missing or invalid,
    return None so the ordinary Control Plane stage remains the owner
    of that refusal. Calibration must never mask an earlier structural
    blocker.
    """
    filing = config.get("filing") or {}
    if config.get("segments"):
        declaration_path = run_dir / "declarations" / "segments.yaml"
        if not declaration_path.is_file():
            return None
        try:
            declared = load_declared_segments(declaration_path)
            opendart_filing_snapshot_loader(
                fetch_text=network.fetch_text,
                fetch_bytes=network.fetch_bytes,
                as_of=str(config["as_of"]),
                api_key=network.api_key,
                declared_segments=declared,
            )(identity)
        except Exception:
            return None
        anchor_segment = str(filing.get("segment_id", "core"))
        matches = tuple(
            item for item in declared.segments
            if item.segment_id == anchor_segment
        )
        if len(matches) != 1:
            return None
        return matches[0].ksic_code
    return company_industry_code or None


def _target_filing_receipts(run_dir: Path) -> tuple[str, ...]:
    path = run_dir / "raw" / "list.json"
    if not path.is_file():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    rows = payload.get("list") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return ()
    return tuple(
        str(row.get("rcept_no"))
        for row in rows
        if isinstance(row, dict) and row.get("rcept_no")
    )


def _conditioning_source_ref(run_dir: Path, calibration: object) -> str:
    if not isinstance(calibration, dict):
        return ""
    value = calibration.get("conditioning")
    if not isinstance(value, str) or not value:
        return ""
    path = _resolve(run_dir, value)
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("source_ref") or "") if isinstance(payload, dict) else ""


def _required_production_calibration(
    run_dir: Path,
    config: dict,
    *,
    identity=None,
    network: OpenDartNetwork | None = None,
    company_industry_code: str | None = None,
):
    # Optional arguments preserve the small helper's original testing
    # surface while ensuring the real preflight passes one resolved
    # target context through the whole decision.
    if network is None:
        network = _build_network(run_dir)
    if identity is None or company_industry_code is None:
        try:
            resolved, _corp_code, profile = _resolved_target_context(
                run_dir, config, network
            )
        except Exception:
            return None
        identity = identity or resolved
        if company_industry_code is None:
            company_industry_code = profile.induty_code
    return resolve_production_calibration_cohort(
        load_production_calibration_registry(),
        ksic_code=_run_industry_code(
            run_dir,
            config,
            identity=identity,
            network=network,
            company_industry_code=company_industry_code or "",
        ),
        forecast_years=int(config.get("forecast_years", 5)),
        scenario_ids=tuple(config.get("scenario_ids", ())),
    )


def _enforce_production_calibration(
    run_dir: Path,
    config: dict,
    *,
    network: OpenDartNetwork,
) -> None:
    # Company resolution/profile/segment-declaration errors belong to
    # their existing canonical stages. Defer silently here and let
    # run_prism emit the established stop stage/rationale.
    try:
        identity, corp_code, profile = _resolved_target_context(
            run_dir, config, network
        )
    except _TargetProfileMismatchError:
        # This mismatch is not guaranteed to be rejected later by the canonical
        # profile fetcher. Swallowing it would disable a mandatory cohort for a
        # run whose raw company payload belongs to another issuer.
        raise
    except Exception:
        return
    cohort = _required_production_calibration(
        run_dir,
        config,
        identity=identity,
        network=network,
        company_industry_code=profile.induty_code,
    )
    if cohort is None:
        return
    calibration = config.get("calibration")
    try:
        validate_declared_calibration(
            cohort,
            calibration,
            target_ticker=identity.ticker,
            target_corp_code=corp_code,
            conditioning_source_ref=_conditioning_source_ref(
                run_dir, calibration
            ),
            target_filing_receipts=_target_filing_receipts(run_dir),
        )
    except CalibrationCohortRegistryError as exc:
        raise RunbookError(str(exc)) from exc


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
    for path in (Path(__file__).resolve(), ROOT / "scripts/run_research_campaign.py",
                 ROOT / "scripts/research_report_completion.py", ROOT / "pyproject.toml"):
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
    equity_target = probability_weighted_equity_value(
        valuation, result.data.get("bound_scenario_set")
    )
    if equity_target is not None:
        return equity_target
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


def _restore_bytes_atomic(path: Path, content: bytes, *, token: str) -> None:
    temporary = path.parent / f".{path.name}.{token}.tmp"
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _canonical_receipt_tree_hash(receipts: list[dict[str, str]]) -> str:
    canonical = sorted(receipts, key=lambda item: item["filename"])
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def publish_report_bundle(
    run_dir: str | Path,
    result,
    *,
    output_dir: str | Path | None = None,
    report_alias: str | Path | None = None,
) -> dict:
    """Persist a v2 immutable bundle and prove it before exposing the report.

    The bundle manifest, latest pointer and public report are all derived from
    one completed LIVE_PRIMARY run.  The pointer is never written until the
    manifest and every receipt pass the same 33-stage completion validator.
    """
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
        "freeze_token.json",
        "execution_attestation.json",
        *visuals,
    )
    missing = tuple(name for name in required if not (source / name).is_file())
    if missing:
        raise RunbookError(
            "completed run bundle is incomplete: " + ", ".join(missing)
        )
    if (source / BUNDLE_MANIFEST_NAME).exists():
        raise RunbookError(
            "source run already carries a report bundle manifest; refusing to overwrite immutable evidence"
        )

    investor_profile_path = run_dir / "declarations" / "investor_report.yaml"
    if not investor_profile_path.is_file():
        raise RunbookError(
            "public report publication requires declarations/investor_report.yaml; "
            "refusing to expose the developer-facing audit report"
        )
    report = render_investor_report(
        result.data,
        load_investor_report_profile(investor_profile_path),
    )
    valuation_hash = str(result.data.get("valuation_hash") or "")
    audit_hash = str(result.data.get("audit_hash") or "")
    run_id = str(getattr(result, "run_id", "") or "")
    ticker = str(result.data.get("ticker") or "")
    config = _load_run(run_dir)
    as_of = str(config.get("as_of") or "")
    run_input_sha256 = _run_input_sha256(run_dir)
    stage_registry_path = ROOT / "config" / "control_plane_stage_registry.yaml"
    stage_registry_sha256 = _file_sha256(stage_registry_path)
    if not all((valuation_hash, audit_hash, run_id, ticker, as_of)):
        raise RunbookError("completed run lacks report artifact identities")

    token_payload = json.loads(
        (source / "freeze_token.json").read_text(encoding="utf-8")
    )
    attestation_payload = json.loads(
        (source / "execution_attestation.json").read_text(encoding="utf-8")
    )
    freeze_token_hash = str(token_payload.get("token_hash") or "")
    execution_attestation_hash = str(
        attestation_payload.get("attestation_hash") or ""
    )
    canonical_entrypoint_id = str(
        result.data.get("canonical_entrypoint_id") or ""
    )
    if (
        not freeze_token_hash
        or not execution_attestation_hash
        or canonical_entrypoint_id != CANONICAL_ENTRYPOINT_ID
    ):
        raise RunbookError(
            "completed run lacks canonical entrypoint, freeze-token, or execution-attestation proof"
        )

    reference = _reference_value_per_share(result)
    reference_token = f"TP{reference.quantize(Decimal('1')):.0f}"
    seed = "|".join(
        (
            BUNDLE_MANIFEST_SCHEMA,
            ticker,
            as_of,
            run_id,
            valuation_hash,
            audit_hash,
            run_input_sha256,
            sha256(report.encode("utf-8")).hexdigest(),
            _file_sha256(source / "manifest.json"),
            freeze_token_hash,
            execution_attestation_hash,
            canonical_entrypoint_id,
            stage_registry_sha256,
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

    versioned_report_name = (
        f"{_safe_artifact_token(ticker)}_"
        f"{_safe_artifact_token(as_of.replace('-', ''))}_투자보고서.md"
    )
    versioned_report_path = bundle_dir / versioned_report_name
    versioned_report_path.write_text(report, encoding="utf-8")
    receipts = [
        {
            "filename": path.relative_to(bundle_dir).as_posix(),
            "sha256": _file_sha256(path),
        }
        for path in sorted(bundle_dir.rglob("*"))
        if path.is_file()
        and path.relative_to(bundle_dir).as_posix() != BUNDLE_MANIFEST_NAME
    ]
    bundle_manifest = {
        "schema_version": BUNDLE_MANIFEST_SCHEMA,
        "artifact_id": artifact_id,
        "as_of": as_of,
        "run_id": run_id,
        "ticker": ticker,
        "reference_value_per_share": str(reference),
        "valuation_hash": valuation_hash,
        "audit_hash": audit_hash,
        "run_input_sha256": run_input_sha256,
        "freeze_token_hash": freeze_token_hash,
        "execution_attestation_hash": execution_attestation_hash,
        "canonical_entrypoint_id": canonical_entrypoint_id,
        "stage_registry_sha256": stage_registry_sha256,
        "bundle_tree_sha256": _canonical_receipt_tree_hash(receipts),
        "report_filename": versioned_report_name,
        "report_sha256": _file_sha256(versioned_report_path),
        "files": receipts,
    }
    bundle_manifest_path = bundle_dir / BUNDLE_MANIFEST_NAME
    _write_json_atomic(bundle_manifest_path, bundle_manifest, token=short_hash)

    latest_name = f"{_safe_artifact_token(ticker)}_LATEST_REPORT.json"
    latest_path = output_root / latest_name
    latest = {
        "schema_version": LATEST_MANIFEST_SCHEMA,
        "artifact_id": artifact_id,
        "as_of": as_of,
        "run_id": run_id,
        "ticker": ticker,
        "bundle_directory": bundle_relative.as_posix(),
        "bundle_manifest": (
            bundle_relative / bundle_manifest_path.name
        ).as_posix(),
        "bundle_manifest_sha256": _file_sha256(bundle_manifest_path),
        "report_filename": (
            bundle_relative / versioned_report_name
        ).as_posix(),
        "report_sha256": _file_sha256(versioned_report_path),
        "valuation_hash": valuation_hash,
        "audit_hash": audit_hash,
        "run_input_sha256": run_input_sha256,
        "freeze_token_hash": freeze_token_hash,
        "execution_attestation_hash": execution_attestation_hash,
        "bundle_tree_sha256": bundle_manifest["bundle_tree_sha256"],
        "canonical_entrypoint_id": canonical_entrypoint_id,
        "stage_registry_sha256": stage_registry_sha256,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    previous_latest = (
        latest_path.read_bytes() if latest_path.is_file() else None
    )
    _write_json_atomic(latest_path, latest, token=short_hash)
    try:
        completion = validate_completion_bundle(
            bundle_dir,
            stage_registry_path=ROOT / "config" / "control_plane_stage_registry.yaml",
            latest_manifest_path=latest_path,
        )
    except CompletionProofError as exc:
        bundle_manifest_path.unlink(missing_ok=True)
        if previous_latest is None:
            latest_path.unlink(missing_ok=True)
        else:
            _restore_bytes_atomic(
                latest_path, previous_latest, token=f"restore-{short_hash}"
            )
        shutil.rmtree(bundle_dir, ignore_errors=True)
        raise RunbookError(f"canonical bundle validation failed: {exc}") from exc

    alias = Path(report_alias) if report_alias else output_root / "final_report.md"
    alias.parent.mkdir(parents=True, exist_ok=True)
    temporary_alias = alias.parent / f".{alias.name}.{short_hash}.tmp"
    temporary_alias.write_text(report, encoding="utf-8")
    os.replace(temporary_alias, alias)
    return {
        **latest,
        "latest_manifest_path": str(latest_path),
        "versioned_report_path": str(versioned_report_path),
        "completion": completion.to_dict(),
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
    """Reuse only a matching v2 bundle that passes the full completion proof."""
    run_dir = Path(run_dir).resolve()
    investor_profile_path = run_dir / "declarations" / "investor_report.yaml"
    if not investor_profile_path.is_file():
        raise RunbookError(
            "public report reuse requires declarations/investor_report.yaml; "
            "refusing to expose the developer-facing audit report"
        )
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
        if not isinstance(latest, dict):
            raise RunbookError(f"published latest-report manifest is not an object: {latest_path}")
        # Old v1 artifacts remain readable evidence, but can never be reused as
        # a canonical result.  A new run will produce a v2 bundle instead.
        if latest.get("schema_version") != LATEST_MANIFEST_SCHEMA:
            continue
        if (
            latest.get("run_id") != expected_run_id
            or latest.get("as_of") != expected_as_of
            or latest.get("run_input_sha256") != expected_run_input_sha256
        ):
            continue
        bundle_manifest_path = _resolve_manifest_path(
            output_root, latest.get("bundle_manifest"), label="bundle manifest"
        )
        bundle_dir = _resolve_manifest_path(
            output_root, latest.get("bundle_directory"), label="bundle directory"
        )
        if not bundle_manifest_path.is_file() or not bundle_dir.is_dir():
            raise RunbookError("published canonical bundle path is missing")
        try:
            completion = validate_completion_bundle(
                bundle_dir,
                stage_registry_path=ROOT / "config" / "control_plane_stage_registry.yaml",
                latest_manifest_path=latest_path,
            )
        except CompletionProofError as exc:
            raise RunbookError(f"published canonical bundle failed validation: {exc}") from exc
        report_path = _resolve_manifest_path(
            output_root, latest.get("report_filename"), label="versioned report"
        )
        if not report_path.is_file():
            raise RunbookError("published canonical versioned report is missing")
        alias = Path(report_alias) if report_alias else output_root / "final_report.md"
        alias.parent.mkdir(parents=True, exist_ok=True)
        token = sha256(str(latest["artifact_id"]).encode("utf-8")).hexdigest()[:12]
        temporary_alias = alias.parent / f".{alias.name}.{token}.tmp"
        temporary_alias.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
        os.replace(temporary_alias, alias)
        return {
            **latest,
            "latest_manifest_path": str(latest_path),
            "versioned_report_path": str(report_path),
            "completion": completion.to_dict(),
            "reused": True,
        }
    return None

def execute_run(run_dir: str | Path, *, state_root: str | None = None,
                staff_mode: str | None = None, underwriting_path: str | Path | None = None):
    """Run one prepared directory; returns (reached, stop_stage, stop_reason, result)."""
    run_dir = Path(run_dir).resolve()
    config = _load_run(run_dir)
    filing = config["filing"]
    network = _build_network(run_dir)
    _enforce_production_calibration(run_dir, config, network=network)
    investor_profile_path = run_dir / "declarations" / "investor_report.yaml"
    investor_profile = (
        load_investor_report_profile(investor_profile_path)
        if investor_profile_path.is_file()
        else None
    )

    def _parse_method(text: str, label: str) -> tuple[str, str, str | None]:
        archetype, _, rest = str(text).partition("/")
        method, _, version = rest.partition("/")
        if not archetype or not method:
            raise RunbookError(f"{label} must be 'archetype/method[/version]'")
        return archetype, method, version or None

    segments_config = config.get("segments")
    if segments_config and config.get("method"):
        raise RunbookError(
            "run.yaml declares both 'method' and 'segments'; a single-segment "
            "run uses 'method', a multi-segment run lists one method per "
            "segment under 'segments'"
        )
    if segments_config:
        method_choices = tuple(
            SegmentMethodChoice(
                str(row["segment_id"]),
                *_parse_method(row["method"], f"segments[{index}].method"),
            )
            for index, row in enumerate(segments_config)
        )
    else:
        method_choices = (
            SegmentMethodChoice(
                str(filing.get("segment_id", "core")),
                *_parse_method(config["method"], "run.yaml method"),
            ),
        )

    calibration = config.get("calibration")
    spec_kwargs: dict = {}
    if calibration:
        spec_kwargs.update(
            calibration_snapshot_loader=_calibration_loader(run_dir, calibration)(
                str(config["as_of"])
            ),
            calibration_cohort_key=calibration["cohort_key"],
            external_probability_source=calibration["external_probability_source"],
            legacy_continuous_probability_replay_receipt=calibration.get(
                "legacy_replay_snapshot_hash"
            ),
        )
    market_path = _optional_path(run_dir, "market.yaml")
    parent_adjustments = tuple(
        ParentAdjustmentPlan(
            asset_id=str(row["asset_id"]),
            assumption_key=str(row["assumption_key"]),
        )
        for row in config.get("parent_adjustments", ())
    )
    spec = GenericKRRuntimeSpec(
        as_of=str(config["as_of"]),
        scenario_ids=tuple(config["scenario_ids"]),
        method_choices=method_choices,
        filing=OpenDartFilingSelection(
            business_year=str(filing["business_year"]),
            report_code=str(filing.get("report_code", "11011")),
            fs_div=str(filing.get("fs_div", "CFS")),
            fiscal_period_end=str(filing["fiscal_period_end"]),
            checked_at=str(config["as_of"]),
            segment_id=str(filing.get("segment_id", "core")),
        ),
        forecast_years=int(config.get("forecast_years", 5)),
        declared_underwriting_path=str(underwriting_path or run_dir / "declarations" / "underwriting.yaml"),
        public_filing_facts_path=(
            str(_resolve(run_dir, config["public_filing_facts_path"]))
            if config.get("public_filing_facts_path") else None
        ),
        declared_risk_path=_optional_path(run_dir, "risk_pack.yaml"),
        declared_segments_path=_optional_path(run_dir, "segments.yaml"),
        declared_broker_research_path=_optional_path(
            run_dir, "broker_research.yaml"
        ),
        require_broker_research=bool(config.get("require_broker_research", False)),
        table_cell_receipts_path=_optional_path(run_dir, "table_cell_receipts.json"),
        extra_required_evidence=tuple(config.get("extra_required_evidence", ())),
        parent_adjustments=parent_adjustments,
        market_config_path=market_path,
        street_export_path=_optional_path(run_dir, "street.json"),
        market_currency=(
            str(config.get("market_currency", "KRW")) if market_path else None
        ),
        investor_entry_margin_of_safety=(
            investor_profile.entry_margin_of_safety
            if investor_profile is not None
            else None
        ),
        **spec_kwargs,
    )
    factory = build_generic_kr_runtime_factory(
        network=network,
        transport=_StaffTransport(
            run_dir / "declarations" / "staff",
            mode=staff_mode or os.environ.get("VALUATION_STAFF_MODE", "replay"),
            request_dir=run_dir / "out" / "staff_requests",
        ),
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
    parser.add_argument("--staff-mode", choices=("replay", "assisted", "live"),
                        default=os.environ.get("VALUATION_STAFF_MODE", "replay"))
    parser.add_argument("--underwriting-path", help="alternate underwriting input; noncanonical inputs are not published")
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    output_root = run_dir / "out"
    publishable = (args.staff_mode == "replay" and (not args.underwriting_path or
                   Path(args.underwriting_path).resolve() == (run_dir / "declarations" / "underwriting.yaml").resolve()))
    reused = reuse_published_report_bundle(
        run_dir,
        output_dir=output_root,
        report_alias=args.report_out,
    ) if publishable else None
    if reused is not None:
        print("\n  stages: previously completed — REUSED")
        print(f"  report: {reused['versioned_report_path']}")
        print(f"  manifest: {reused['latest_manifest_path']}")
        return 0
    reached, stop_stage, stop_reason, result = execute_run(
        run_dir, state_root=(str(output_root / "state" / _run_input_sha256(run_dir)) if publishable else None),
        staff_mode=args.staff_mode, underwriting_path=args.underwriting_path,
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
    if not publishable:
        print("Validated execution only; publication withheld. Materialize canonical underwriting and replay staff files, then rerun in replay mode to publish.")
        return 0
    report = result.data.get("final_report")
    if not isinstance(report, str) or not report.strip():
        print("canonical completion: BLOCKED — final report is missing")
        return 1
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
