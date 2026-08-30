from __future__ import annotations

import os
from pathlib import Path
from typing import TypedDict

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .cli_runtime import (
    LiveCLIError,
    load_live_runtime_config_factory,
    render_controlled_run,
)
from .strict_cli_runtime import execute_live_analysis


_DEFAULT_PROVIDER_FACTORY = "valuation_engine.generic_kr_cli:factory"
_MCP_PROVIDER_FACTORY_ENV = "VALUATION_MCP_PROVIDER_FACTORY"
_LIVE_PROVIDER_FACTORY_ENV = "VALUATION_LIVE_PROVIDER_FACTORY"
_MCP_STATE_ROOT_ENV = "VALUATION_MCP_STATE_ROOT"
_MCP_JURISDICTION_ENV = "VALUATION_MCP_JURISDICTION"


class PrismAnalyzeResult(TypedDict):
    status: str
    company: str
    canonical_command: str
    run_id: str
    execution_mode: str
    blocking_codes: list[str]
    report: str


def _provider_factory_spec() -> str:
    return (
        os.environ.get(_MCP_PROVIDER_FACTORY_ENV, "").strip()
        or os.environ.get(_LIVE_PROVIDER_FACTORY_ENV, "").strip()
        or _DEFAULT_PROVIDER_FACTORY
    )


def _state_root() -> Path:
    raw = os.environ.get(_MCP_STATE_ROOT_ENV, ".valuation_state").strip()
    if not raw:
        raise ToolError(f"{_MCP_STATE_ROOT_ENV} cannot be blank")
    return Path(raw).expanduser()


def _jurisdiction() -> str:
    value = os.environ.get(_MCP_JURISDICTION_ENV, "KR").strip()
    if not value:
        raise ToolError(f"{_MCP_JURISDICTION_ENV} cannot be blank")
    return value


def _blocking_codes(result) -> list[str]:
    return list(
        dict.fromkeys(
            f"{trace.stage}:{trace.status.name}"
            for trace in result.stage_traces
            if trace.blocking
        )
    )


def run_prism_mcp(company: str) -> PrismAnalyzeResult:
    """Run one company through the canonical, attested PRISM LIVE_PRIMARY path."""
    if "\n" in company or "\r" in company:
        raise ToolError("company must be a single-line company name or ticker")
    normalized_company = " ".join(company.strip().split())
    if not normalized_company:
        raise ToolError("company is required")

    canonical_command = f"분석시작 {normalized_company}"
    try:
        factory = load_live_runtime_config_factory(_provider_factory_spec())
        result = execute_live_analysis(
            canonical_command,
            state_root=_state_root(),
            provider_factory=factory,
            jurisdiction=_jurisdiction(),
        )
        report = render_controlled_run(result)
    except LiveCLIError as exc:
        raise ToolError(f"PRISM [{exc.code}] {exc}") from exc
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"PRISM MCP execution failed ({type(exc).__name__}); "
            "no manual valuation fallback is permitted"
        ) from exc

    blocked = bool(result.blocked_reasons)
    return PrismAnalyzeResult(
        status="VALUATION_BLOCKED" if blocked else "COMPLETED",
        company=normalized_company,
        canonical_command=canonical_command,
        run_id=result.run_id,
        execution_mode=result.execution_mode.value,
        blocking_codes=_blocking_codes(result),
        report=report,
    )


mcp = MCPServer(
    name="prism-valuation",
    title="PRISM Valuation",
    description=(
        "Canonical MCP gateway for the newwonwoo/valuation PRISM engine. "
        "It does not perform a separate or simplified valuation."
    ),
    instructions=(
        "When a user names a listed company or ticker and asks for analysis, valuation, "
        "PRISM, fair value, target value, or says to run/돌려봐 the company, use the "
        "prism_analyze tool instead of answering with a manual equity valuation. Pass only "
        "the company name or ticker. The tool invokes the strict attested LIVE_PRIMARY "
        "run_prism orchestrator and returns its own report or its fail-closed blocking code."
    ),
    version="0.1.0",
)


@mcp.tool(
    name="prism_analyze",
    title="PRISM_ANALYZE",
    description=(
        "Run a listed company or ticker through the canonical PRISM valuation orchestrator. "
        "Use this whenever the user asks to analyze/value/PRISM/run a stock. This tool only "
        "dispatches to strict execute_live_analysis -> attested run_prism; it never computes "
        "or substitutes a manual valuation and never bypasses a blocked Gate."
    ),
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
)
def prism_analyze(company: str) -> PrismAnalyzeResult:
    return run_prism_mcp(company)


def main() -> None:
    """Run the local PRISM MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
