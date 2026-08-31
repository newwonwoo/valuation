from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO = ROOT / "ops" / "project_portfolio.yaml"

portfolio = yaml.safe_load(PORTFOLIO.read_text(encoding="utf-8"))
departments = {row["id"]: row for row in portfolio["departments"]}
departments["pm-integrator"]["current_work"] = (
    "MAINTENANCE — natural-language routing, PRISM MCP, KR live-run/SOTP and runtime hardening are accepted; "
    "only repository-admin branch protection, live-runtime credential provisioning (Issue #158), "
    "and real production-history accumulation remain"
)
departments["runtime-safety-agent"]["current_work"] = (
    "MAINTENANCE — natural-language/MCP authority and native-Linux tunnel state authorization are accepted; "
    "main branch protection and live-runtime credentials remain external admin configuration"
)
PORTFOLIO.write_text(
    yaml.safe_dump(portfolio, allow_unicode=True, sort_keys=False, width=120),
    encoding="utf-8",
)
