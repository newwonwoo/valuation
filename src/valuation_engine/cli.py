from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Mapping, Sequence

from .analysis_intent import canonicalize_analysis_command
from .audit import audit_model, gate_report
from .cli_runtime import (
    LiveCLIError,
    load_live_runtime_config_factory,
    parse_analysis_command,
    render_controlled_run,
    render_major_gate_summary,
    resolve_provider_factory_spec,
)
from .strict_cli_runtime import execute_live_analysis
from .config import load_intrinsic_company_config, load_market_comparison
from .engine import compare_to_market, run_valuation
from .provenance import build_oci_legacy_trace
from .workflow import run_analysis_command


_DEFAULT_LEGACY_CONFIG = "examples/oci/company.yaml"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evidence-first valuation engine"
    )
    parser.add_argument(
        "input",
        help="company YAML config or PRISM stock-analysis request",
    )
    parser.add_argument(
        "--state-root",
        default=".valuation_state",
        help="private/local state root",
    )
    parser.add_argument(
        "--provider-factory",
        help=(
            "LIVE_PRIMARY config factory in 'python.module:callable' form; "
            "alternatively set VALUATION_LIVE_PROVIDER_FACTORY"
        ),
    )
    parser.add_argument(
        "--run-id",
        help="optional explicit LIVE_PRIMARY or legacy run ID",
    )
    parser.add_argument(
        "--jurisdiction",
        help="optional jurisdiction constraint passed to the LIVE provider factory",
    )
    parser.add_argument(
        "--legacy-oci",
        action="store_true",
        help="explicitly use the OCI v0.3 regression workflow",
    )
    parser.add_argument(
        "--config",
        help=(
            "legacy OCI fixture config; valid only together with --legacy-oci"
        ),
    )
    return parser


def _run_legacy_analysis(args: argparse.Namespace) -> int:
    if args.provider_factory:
        raise LiveCLIError(
            "LEGACY_LIVE_OPTION_CONFLICT",
            "--legacy-oci와 --provider-factory를 함께 사용할 수 없습니다",
        )
    if args.jurisdiction:
        raise LiveCLIError(
            "LEGACY_LIVE_OPTION_CONFLICT",
            "--jurisdiction은 LIVE_PRIMARY 전용입니다",
        )
    outcome = run_analysis_command(
        args.input,
        config_path=args.config or _DEFAULT_LEGACY_CONFIG,
        state_root=args.state_root,
        run_id=args.run_id,
    )
    print("\n".join(outcome.progress))
    print(outcome.report)
    return 2 if outcome.blocked_reasons else 0


def _run_live_analysis(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str],
) -> int:
    if args.config:
        raise LiveCLIError(
            "LEGACY_CONFIG_REQUIRES_FLAG",
            "--config는 --legacy-oci와 함께 사용하는 회귀 전용 옵션입니다",
        )
    # Validate the canonical command before provider resolution so an intent
    # without a company fails as COMPANY_REQUIRED rather than as a provider error.
    parse_analysis_command(args.input)
    spec = resolve_provider_factory_spec(
        args.provider_factory,
        environ=environ,
    )
    factory = load_live_runtime_config_factory(spec)
    result = execute_live_analysis(
        args.input,
        state_root=args.state_root,
        provider_factory=factory,
        run_id=args.run_id,
        jurisdiction=args.jurisdiction,
        major_gate_reporter=lambda summary: print(
            render_major_gate_summary(summary),
            flush=True,
        ),
    )
    print(
        render_controlled_run(
            result,
            include_gate_summaries=not bool(result.major_gate_summaries),
        ),
        end="",
    )
    return 2 if result.blocked_reasons else 0


def _run_yaml_valuation(args: argparse.Namespace) -> int:
    incompatible = []
    if args.legacy_oci:
        incompatible.append("--legacy-oci")
    if args.provider_factory:
        incompatible.append("--provider-factory")
    if args.run_id:
        incompatible.append("--run-id")
    if args.jurisdiction:
        incompatible.append("--jurisdiction")
    if args.config:
        incompatible.append("--config")
    if incompatible:
        raise LiveCLIError(
            "YAML_MODE_OPTION_CONFLICT",
            "YAML valuation mode에서 사용할 수 없는 옵션: "
            + ", ".join(incompatible),
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
        return 2
    market = load_market_comparison(config)
    market_price = float(market["price"])
    market_gap = compare_to_market(result, market_price)
    payload = {
        "scenarios": [value.__dict__ for value in result.scenarios],
        "expected_equity_trn": result.expected_equity_trn,
        "expected_value_per_share": result.expected_value_per_share,
        "market_price": market_price,
        "market_gap": market_gap,
        "audit": audit,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = _build_parser().parse_args(argv)
    environment = os.environ if environ is None else environ
    try:
        canonical_command = canonicalize_analysis_command(args.input)
        if canonical_command is not None:
            args.input = canonical_command
            if args.legacy_oci:
                return _run_legacy_analysis(args)
            return _run_live_analysis(args, environ=environment)
        return _run_yaml_valuation(args)
    except LiveCLIError as exc:
        print(f"ERROR [{exc.code}] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
