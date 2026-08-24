from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib
import json
import os
from pathlib import Path
from typing import Callable

from .audit import audit_model, gate_report
from .config import load_intrinsic_company_config, load_market_comparison
from .control_plane import ExecutionMode
from .engine import compare_to_market, run_valuation
from .live_runtime import LivePrimaryRuntimeConfig, run_prism
from .orchestrator import ControlledRunResult
from .provenance import build_oci_legacy_trace
from .workflow import run_analysis_command


_RUNTIME_FACTORY_ENV = "PRISM_RUNTIME_FACTORY"


@dataclass(frozen=True)
class LiveRuntimeRequest:
    command: str
    company_query: str
    state_root: Path
    run_id: str | None = None


LiveRuntimeFactory = Callable[[LiveRuntimeRequest], LivePrimaryRuntimeConfig]
LiveRuntimeRunner = Callable[[LivePrimaryRuntimeConfig], ControlledRunResult]


class LiveRuntimeConfigurationError(RuntimeError):
    pass


def _analysis_company(command: str) -> str:
    text = command.strip()
    if not text.startswith("분석시작"):
        raise ValueError("command must start with '분석시작'")
    company = text.removeprefix("분석시작").strip()
    if not company:
        raise ValueError("company is required")
    return company


def _analysis_mode(mode: str | None) -> str:
    """Resolve the analysis-command mode without applying it to direct YAML runs."""
    return mode or "live-primary"


def load_runtime_factory(spec: str) -> LiveRuntimeFactory:
    value = spec.strip()
    if ":" not in value:
        raise LiveRuntimeConfigurationError(
            "runtime factory must use 'module:function' syntax"
        )
    module_name, attribute = value.rsplit(":", 1)
    if not module_name or not attribute:
        raise LiveRuntimeConfigurationError(
            "runtime factory must use 'module:function' syntax"
        )
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise LiveRuntimeConfigurationError(
            f"failed to import runtime factory module {module_name!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    try:
        factory = getattr(module, attribute)
    except AttributeError as exc:
        raise LiveRuntimeConfigurationError(
            f"runtime factory attribute {attribute!r} is missing from {module_name!r}"
        ) from exc
    if not callable(factory):
        raise LiveRuntimeConfigurationError(
            f"runtime factory {value!r} is not callable"
        )
    return factory


def run_live_analysis_command(
    command: str,
    *,
    state_root: str | Path,
    runtime_factory_spec: str | None,
    run_id: str | None = None,
    runner: LiveRuntimeRunner = run_prism,
) -> ControlledRunResult:
    factory_spec = (
        runtime_factory_spec or os.getenv(_RUNTIME_FACTORY_ENV, "")
    ).strip()
    if not factory_spec:
        raise LiveRuntimeConfigurationError(
            "LIVE_PRIMARY is the default for '분석시작', but no production runtime "
            f"factory is configured. Supply --runtime-factory module:function or "
            f"set {_RUNTIME_FACTORY_ENV}. Use --mode legacy-regression only for the "
            "explicit OCI regression workflow."
        )
    request = LiveRuntimeRequest(
        command=command,
        company_query=_analysis_company(command),
        state_root=Path(state_root),
        run_id=run_id,
    )
    factory = load_runtime_factory(factory_spec)
    try:
        config = factory(request)
    except Exception as exc:
        raise LiveRuntimeConfigurationError(
            "LIVE_PRIMARY runtime factory failed: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(config, LivePrimaryRuntimeConfig):
        raise LiveRuntimeConfigurationError(
            "runtime factory must return LivePrimaryRuntimeConfig"
        )
    if request.run_id is not None and config.run_id != request.run_id:
        raise LiveRuntimeConfigurationError(
            "runtime factory returned a run_id different from the explicit CLI --run-id"
        )
    if Path(config.state_root) != request.state_root:
        raise LiveRuntimeConfigurationError(
            "runtime factory must preserve the CLI state_root"
        )
    if config.company_request.query.strip() != request.company_query:
        raise LiveRuntimeConfigurationError(
            "runtime factory must preserve the requested company query"
        )
    return runner(config)


def render_controlled_run(result: ControlledRunResult) -> str:
    lines = [
        f"[{trace.status.value}] {trace.stage}: {trace.rationale}"
        for trace in result.stage_traces
    ]
    if result.blocked_reasons:
        lines.extend(
            (
                "",
                "# VALUATION BLOCKED",
                *(f"- {reason}" for reason in result.blocked_reasons),
            )
        )
        return "\n".join(lines)
    report = result.data.get("final_report")
    if not isinstance(report, str) or not report.strip():
        raise RuntimeError(
            "completed LIVE_PRIMARY run is missing final_report"
        )
    lines.extend(("", report))
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evidence-first PRISM valuation engine"
    )
    parser.add_argument(
        "input",
        help="company YAML config or '분석시작 <회사>'",
    )
    parser.add_argument(
        "--mode",
        choices=("live-primary", "legacy-regression"),
        default=None,
        help=(
            "analysis-command execution mode; omitted means LIVE_PRIMARY for "
            "'분석시작'. Direct YAML execution does not accept --mode."
        ),
    )
    parser.add_argument(
        "--runtime-factory",
        default=None,
        help=(
            "LIVE_PRIMARY provider/config factory in module:function form; may also "
            f"be supplied through {_RUNTIME_FACTORY_ENV}"
        ),
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="optional explicit LIVE_PRIMARY run ID",
    )
    parser.add_argument(
        "--config",
        default="examples/oci/company.yaml",
        help="OCI fixture config used only by legacy-regression analysis mode",
    )
    parser.add_argument(
        "--state-root",
        default=".valuation_state",
        help="private/local state root",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.input.strip().startswith("분석시작"):
        mode = _analysis_mode(args.mode)
        if mode == "legacy-regression":
            outcome = run_analysis_command(
                args.input,
                config_path=args.config,
                state_root=args.state_root,
                run_id=args.run_id,
            )
            print("\n".join(outcome.progress))
            print(outcome.report)
            return
        try:
            result = run_live_analysis_command(
                args.input,
                state_root=args.state_root,
                runtime_factory_spec=args.runtime_factory,
                run_id=args.run_id,
            )
        except LiveRuntimeConfigurationError as exc:
            parser.exit(2, f"LIVE_PRIMARY CONFIGURATION ERROR: {exc}\n")
        print(render_controlled_run(result))
        return

    if args.mode is not None or args.runtime_factory or args.run_id:
        parser.error(
            "--mode/--runtime-factory/--run-id are analysis-command options; "
            "direct YAML execution is the explicit legacy deterministic core"
        )

    config = Path(args.input)
    shares, scenarios, raw = load_intrinsic_company_config(config)
    build_oci_legacy_trace(raw, run_id="CLI").validate()
    result = run_valuation(scenarios, shares)
    core_audit = audit_model(scenarios, shares)
    audit = gate_report(core_audit, traceability_ok=True).to_dict()
    if not audit["pass"]:
        print(
            json.dumps(
                {"status": "VALUATION_BLOCKED", "audit": audit},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    market = load_market_comparison(config)
    market_price = float(market["price"])
    market_gap = compare_to_market(result, market_price)
    payload = {
        "scenarios": [v.__dict__ for v in result.scenarios],
        "expected_equity_trn": result.expected_equity_trn,
        "expected_value_per_share": result.expected_value_per_share,
        "market_price": market_price,
        "market_gap": market_gap,
        "audit": audit,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
