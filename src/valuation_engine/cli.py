from __future__ import annotations

import argparse
import json
from pathlib import Path

from .audit import audit_model, gate_report
from .config import load_intrinsic_company_config, load_market_comparison
from .engine import compare_to_market, run_valuation
from .provenance import build_oci_legacy_trace
from .workflow import run_analysis_command


def main() -> None:
    parser = argparse.ArgumentParser(description="Evidence-first valuation engine")
    parser.add_argument("input", help="company YAML config or '분석시작 <회사>'")
    parser.add_argument("--config", default="examples/oci/company.yaml", help="fixture config for analysis command")
    parser.add_argument("--state-root", default=".valuation_state", help="private/local state root")
    args = parser.parse_args()
    if args.input.strip().startswith("분석시작"):
        outcome = run_analysis_command(
            args.input, config_path=args.config, state_root=args.state_root,
        )
        print("\n".join(outcome.progress))
        print(outcome.report)
        return

    config = Path(args.input)
    shares, scenarios, raw = load_intrinsic_company_config(config)
    build_oci_legacy_trace(raw, run_id="CLI").validate()
    result = run_valuation(scenarios, shares)
    core_audit = audit_model(scenarios, shares)
    audit = gate_report(core_audit, traceability_ok=True).to_dict()
    if not audit["pass"]:
        print(json.dumps({"status": "VALUATION_BLOCKED", "audit": audit}, ensure_ascii=False, indent=2))
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
