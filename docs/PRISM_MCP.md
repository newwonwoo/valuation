# PRISM MCP Gateway

`prism-mcp` is the external tool boundary for the PRISM valuation repository.
It exists so ChatGPT, Claude, IDE agents, and other MCP hosts can invoke the
same canonical PRISM runtime instead of answering a stock-analysis request with
a separate manual valuation.

## Contract

The server exposes exactly one model-controlled tool:

```text
prism_analyze(company)
```

The tool performs only this path:

```text
company / ticker
→ "분석시작 <company>"
→ strict_cli_runtime.execute_live_analysis
→ strict_live_runtime.run_prism
→ AuthorityControlledResult / execution attestation
→ FINAL_REPORT or fail-closed blocking code
```

It does **not** contain valuation formulas, a second router, a simplified DCF,
a price target fallback, or an alternate report generator. A blocked PRISM run
stays blocked. A missing execution attestation stays an error.

The MCP server instructions explicitly tell the host model to use
`prism_analyze` whenever a user names a listed company/ticker and asks for
analysis, valuation, PRISM, fair value, target value, or says to run the stock.
The tool accepts the normalized company/ticker only; the deterministic
`COMPANY_RESOLUTION` stage remains responsible for deciding whether that target
is valid.

## Install

The MCP dependency is optional so ordinary engine installations stay small.

```bash
python -m pip install -e '.[mcp]'
```

For development, the existing dev extra includes MCP:

```bash
python -m pip install -e '.[dev]'
```

The repository pins the current MCP Python SDK major line:

```text
mcp>=2,<3
```

## Run over stdio

```bash
prism-mcp
```

or:

```bash
python -m valuation_engine.mcp_server
```

`stdio` is intentional. The MCP host launches the process and owns the process
security boundary; PRISM credentials remain in the server process environment
and are never MCP tool arguments.

## Host configuration

A typical local MCP host entry is:

```json
{
  "mcpServers": {
    "prism-valuation": {
      "command": "prism-mcp",
      "args": [],
      "env": {
        "DART_API_KEY": "<secret>",
        "VALUATION_LLM_TRANSPORT": "your_transport.module:build",
        "VALUATION_METHOD": "commodity_price_taker/normalized_multiple",
        "VALUATION_MCP_STATE_ROOT": "/private/path/valuation-state"
      }
    }
  }
}
```

Do not commit credentials. Exact host configuration keys can differ by MCP host;
what matters is that the host launches `prism-mcp` over stdio with the required
runtime environment.

## Provider factory

Provider-factory precedence is:

1. `VALUATION_MCP_PROVIDER_FACTORY`
2. `VALUATION_LIVE_PROVIDER_FACTORY`
3. `valuation_engine.generic_kr_cli:factory`

The default generic Korean provider keeps the existing production contracts.
It can require, depending on the selected method and company:

- `DART_API_KEY`
- `VALUATION_LLM_TRANSPORT`
- `VALUATION_METHOD`
- `VALUATION_UNDERWRITING_PATH`
- `VALUATION_RISK_PACK_PATH`
- optional post-freeze market / Street declaration paths

The MCP gateway does not invent any missing declaration. Missing inputs must
surface as the existing PRISM configuration or stage blocker.

## MCP-only configuration

```text
VALUATION_MCP_PROVIDER_FACTORY   optional provider factory override
VALUATION_MCP_STATE_ROOT         state root; default .valuation_state
VALUATION_MCP_JURISDICTION       jurisdiction lock; default KR
```

These are process configuration, not model-controlled tool parameters.

## Returned result

The tool returns structured content with:

```text
status              COMPLETED | VALUATION_BLOCKED
company             normalized target supplied to PRISM
canonical_command   분석시작 <company>
run_id               PRISM run identity
execution_mode       must be LIVE_PRIMARY
blocking_codes       sanitized stage/status codes only
report               canonical PRISM report or blocked rendering
```

Raw provider exceptions and raw blocked rationales are not returned through the
MCP result. The existing PRISM report renderer owns blocked-output redaction.

## What this solves — and what it cannot solve alone

Inside an MCP host that has this server enabled, the model now sees a dedicated
`PRISM_ANALYZE` tool whose description says to use it for stock-analysis intent.
Once the tool is called, there is no manual-valuation escape hatch: only the
strict attested runtime is reachable.

MCP itself cannot intercept a host that refuses to call its tools. A host-level
policy can make tool selection mandatory, but that policy lives in the host,
not in the MCP server. The repository side is therefore designed so that any
host that *does* invoke PRISM has one canonical executable path and cannot
silently switch to a second valuation implementation.
