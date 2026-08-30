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
from io import BytesIO
import json
from pathlib import Path
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

    def complete(self, *, role: str, prompt: str) -> str:
        answers = self._answers.get(role)
        if not answers:
            raise RunbookError(
                f"no staff proposal file for role {role!r}; add "
                f"declarations/staff/{role}.json per the runbook"
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
        help="write the final report markdown here (default: <run_dir>/out/final_report.md)",
    )
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    reached, stop_stage, stop_reason, result = execute_run(run_dir)
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
        out = (
            Path(args.report_out)
            if args.report_out
            else run_dir / "out" / "final_report.md"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"  report: {out}")
        for line in report.splitlines():
            if "내재가치" in line or "기대값" in line or "상승여력" in line:
                print("  " + line.strip("- *"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
