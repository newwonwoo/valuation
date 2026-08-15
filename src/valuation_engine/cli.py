from __future__ import annotations

import argparse
import json

from .audit import audit_model
from .config import load_company_config
from .engine import run_valuation


def main() -> None:
    parser = argparse.ArgumentParser(description="Evidence-first valuation engine")
    parser.add_argument("config", help="company YAML config")
    args = parser.parse_args()
    shares, market_price, scenarios, _ = load_company_config(args.config)
    result = run_valuation(scenarios, shares, market_price=market_price)
    audit = audit_model(scenarios, shares, market_price=market_price)
    payload = {
        "scenarios": [v.__dict__ for v in result.scenarios],
        "expected_equity_trn": result.expected_equity_trn,
        "expected_value_per_share": result.expected_value_per_share,
        "market_price": result.market_price,
        "market_gap": result.market_gap,
        "audit": audit,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
