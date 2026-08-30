# PRISM MCP Gateway

`prism-mcp` is the external tool boundary for the PRISM valuation repository.
It lets an MCP host invoke the same canonical PRISM runtime instead of answering
a stock-analysis request with a separate manual valuation.

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
→ canonical Control Plane stages
→ AuthorityControlledResult / execution attestation
→ FINAL_REPORT or fail-closed blocking code
```

It does not contain valuation formulas, a simplified DCF, a target-price
fallback, or an alternate report generator. A blocked PRISM run stays blocked.
A missing execution attestation stays an error.

## Automatic method routing

The MCP default provider is:

```text
valuation_engine.auto_generic_kr_cli:factory
```

`VALUATION_METHOD` is no longer required for ordinary MCP analysis. The default
flow is:

```text
Industry DNA
→ Module Requirement Plan
→ Evidence / Bridge / Scenario compilation
→ VALUATION_METHOD_INTENT
→ deterministic method resolution
```

The Bridge prepares only candidate methods whose required inputs are actually
present in the Evidence Ledger. This is an evidence-feasibility filter, not an
economic-method decision. The formal `VALUATION_METHOD_INTENT` stage retains
authority:

- if one compiled candidate remains, the stage selects it deterministically;
- if multiple viable economic methods remain, the run stops with
  `AWAITING_USER_DECISION`;
- if no source-backed candidate can be compiled, the run fails closed earlier;
- no LLM or MCP wrapper is allowed to choose a method outside the canonical
  method-intent stage.

An operator may still set `VALUATION_METHOD` deliberately. When present, the
existing explicit-intent `generic_kr_cli:factory` path is used unchanged.

## Install

The MCP dependency is optional:

```bash
python -m pip install -e '.[mcp]'
```

For development:

```bash
python -m pip install -e '.[dev]'
```

The repository tracks MCP Python SDK major version 2:

```text
mcp>=2,<3
```

## Local stdio

```bash
prism-mcp
```

or:

```bash
python -m valuation_engine.mcp_server
```

The MCP process keeps PRISM credentials in its process environment. Credentials
are never tool arguments.

A generic MCP host can launch the server with configuration similar to:

```json
{
  "mcpServers": {
    "prism-valuation": {
      "command": "prism-mcp",
      "args": [],
      "env": {
        "DART_API_KEY": "<secret>",
        "VALUATION_LLM_TRANSPORT": "your_transport.module:build",
        "VALUATION_MCP_STATE_ROOT": "/persistent/private/valuation-state"
      }
    }
  }
}
```

Do not commit credentials.

## Provider-factory precedence

1. `VALUATION_MCP_PROVIDER_FACTORY`
2. `VALUATION_LIVE_PROVIDER_FACTORY`
3. `valuation_engine.auto_generic_kr_cli:factory`

The automatic provider preserves existing source and declaration contracts. It
can require, depending on the selected company and route:

- `DART_API_KEY`
- `VALUATION_LLM_TRANSPORT`
- `VALUATION_UNDERWRITING_PATH` when source-backed analyst declarations are
  needed
- `VALUATION_RISK_PACK_PATH` when the selected method needs risk inputs
- optional post-freeze market / Street declaration paths
- optional `VALUATION_METHOD` only as an explicit operator override

Missing inputs are never invented by the MCP layer.

## MCP process configuration

```text
VALUATION_MCP_PROVIDER_FACTORY   optional provider-factory override
VALUATION_MCP_STATE_ROOT         persistent state root; default .valuation_state
VALUATION_MCP_JURISDICTION       jurisdiction lock; default KR
```

Same-state-root executions are single-writer serialized inside one MCP server
process so mutable current-state promotion cannot race. Independent MCP server
processes must not share one mutable state root unless the deployment supplies
an external single-writer lock.

## Returned result

```text
status              COMPLETED | VALUATION_BLOCKED
company             normalized target supplied to PRISM
canonical_command   분석시작 <company>
run_id               PRISM run identity
execution_mode       LIVE_PRIMARY
blocking_codes       sanitized stage/status codes only
report               canonical PRISM report or blocked rendering
```

Raw provider exceptions, raw blocker rationales and credentials are not returned
through the MCP result.

## ChatGPT connection

ChatGPT does not connect directly to a local stdio MCP process. Keep the PRISM
engine on a host with persistent storage and connect it through OpenAI Secure MCP
Tunnel instead of moving `.valuation_state` onto an ephemeral serverless
filesystem.

The repository provides:

```bash
prism-tunnel check
prism-tunnel connect
prism-tunnel status
prism-tunnel stop
```

See `docs/PRISM_SECURE_TUNNEL.md` for the operator flow.
