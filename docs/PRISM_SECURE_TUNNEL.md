# PRISM Secure MCP Tunnel Runbook

This runbook connects the canonical local PRISM MCP server to supported OpenAI
products without exposing the valuation host to inbound public traffic.

The supported architecture is:

```text
ChatGPT / supported OpenAI host
→ OpenAI Secure MCP Tunnel control plane
→ outbound tunnel-client session
→ valuation_engine.mcp_tunnel_child
→ prism_analyze
→ strict execute_live_analysis
→ attested run_prism
→ persistent VALUATION_MCP_STATE_ROOT
```

Do not move the PRISM engine or `.valuation_state` onto an ephemeral serverless
filesystem just to obtain a public MCP URL. PRISM persists current state,
immutable run artifacts, learning history and execution attestations. The
machine that runs PRISM must therefore use persistent storage.

## 1. OpenAI-side prerequisites

Create or obtain a Secure MCP Tunnel in the OpenAI control plane and record its
`tunnel_...` identifier.

Create a **runtime** API key that is restricted to the tunnel runtime permissions
required by the control plane (Tunnels Read + Use). Keep administrative tunnel
credentials separate. The runtime key is supplied to `tunnel-client` through an
environment-variable reference and is removed before the PRISM MCP child starts.

This repository expects:

```text
CONTROL_PLANE_TUNNEL_ID=tunnel_<32 lowercase hex chars>
CONTROL_PLANE_API_KEY=<dedicated tunnel runtime key>
```

Do not put either value in repository files, command history, `.env` files that
are committed, MCP tool arguments, logs or generated reports.

## 2. Install the supported tunnel-client

Use an official OpenAI `tunnel-client` release. The repository intentionally
does not download or silently replace the tunnel binary.

Either make `tunnel-client` available on `PATH`, or pin the selected executable
explicitly:

```text
TUNNEL_CLIENT_BIN=/trusted/path/to/tunnel-client
```

`prism-tunnel check` refuses to continue when the executable cannot be located.

## 3. Prepare the PRISM runtime host

The tunnel launcher currently requires a **Linux security model** that this
repository can verify directly. Native Linux and WSL2 are supported. Native
macOS and Windows are intentionally rejected until their ACL models have
platform-specific enforcement; the launcher does not infer privacy from POSIX
mode bits on platforms where those bits are not the whole authorization surface.

The valuation process needs its ordinary provider credentials and an explicitly
configured persistent state directory. Minimum generic setup:

```text
DART_API_KEY=<OpenDART credential>
VALUATION_LLM_TRANSPORT=your_transport.module:build
VALUATION_MCP_STATE_ROOT=/persistent/private/prism-state
CONTROL_PLANE_TUNNEL_ID=tunnel_<id>
CONTROL_PLANE_API_KEY=<runtime key>
```

In tunnel mode, `VALUATION_MCP_STATE_ROOT` has **no implicit default**. It must be
supplied explicitly and resolve to an absolute persistent path. A relative path
such as `.valuation_state` or `state/prism` is rejected so a launch from a Git
checkout or ephemeral working directory cannot silently become the durable
state location.

The launcher creates a missing state root with owner-only `0700` permissions and
repairs an existing root to `0700` before starting the tunnel. It then verifies
that no group/other mode bits remain **and** refuses a root carrying Linux
`system.posix_acl_access` or `system.posix_acl_default` metadata. If ACL metadata
cannot be inspected, preflight also fails. The launcher deliberately does not
rewrite an operator's ACL policy and then assume the rewrite was safe.

Optional:

```text
PRISM_TUNNEL_ALIAS=prism-valuation
VALUATION_MCP_JURISDICTION=KR
VALUATION_UNDERWRITING_PATH=/private/run-inputs/underwriting.yaml
VALUATION_RISK_PACK_PATH=/private/run-inputs/risk-pack.yaml
VALUATION_MARKET_CONFIG=/private/post-freeze/market.yaml
VALUATION_STREET_EXPORT=/private/post-freeze/street.json
```

`VALUATION_METHOD` is normally omitted. The MCP default uses canonical automatic
routing and leaves economic method authority in `VALUATION_METHOD_INTENT`. Set
`VALUATION_METHOD` only when an operator deliberately wants the existing
explicit-method path.

The state root must be on durable storage and must not be shared by multiple
independent MCP processes unless the deployment supplies an external
single-writer lock. One PRISM MCP process already serializes runs that share the
same state root.

## 4. Preflight

Run:

```bash
prism-tunnel check
```

The command validates only non-secret configuration and returns JSON similar to:

```json
{
  "status": "READY_TO_CONNECT",
  "alias": "prism-valuation",
  "mcp_command": "/path/to/python -m valuation_engine.mcp_tunnel_child",
  "method_override": false,
  "state_root": "/persistent/private/prism-state",
  "tunnel_client": "/trusted/path/to/tunnel-client",
  "tunnel_id": "tunnel_..."
}
```

Secret values are never printed. `READY_TO_CONNECT` also means the state root
was explicitly supplied, is absolute, exists as a directory, is `0700`, and
passed the Linux ACL metadata check.

## 5. Connect the managed runtime

Run:

```bash
prism-tunnel connect
```

The launcher invokes the official managed-runtime form of `tunnel-client`:

```bash
tunnel-client runtimes connect \
  --alias prism-valuation \
  --tunnel-id "$CONTROL_PLANE_TUNNEL_ID" \
  --runtime-api-key env:CONTROL_PLANE_API_KEY \
  --mcp-command "<current-python> -m valuation_engine.mcp_tunnel_child" \
  --json
```

It then immediately performs:

```bash
tunnel-client runtimes status prism-valuation --json
```

Do not treat the tunnel as ready merely because the connect process returned.
The managed runtime should report the process running and its health/readiness
checks healthy before it is presented as a usable connection.

Do not supervise the tunnel with `nohup` or `disown`; use the native managed
`runtimes connect` lifecycle.

## 6. Inspect or stop the runtime

```bash
prism-tunnel status
prism-tunnel stop
```

Stopping the local managed runtime stops the local tunnel process; it does not
rewrite PRISM state or create an alternate valuation path.

## 7. Add the MCP app in ChatGPT

ChatGPT cannot connect directly to the local stdio process. In a ChatGPT plan
and workspace that supports the required custom MCP capability:

1. Enable Developer mode / custom MCP apps in ChatGPT web.
2. Create a custom app from Apps settings.
3. Choose the Secure MCP Tunnel connection option when it is available for the
   workspace.
4. Select or enter the healthy tunnel created for PRISM.
5. Scan the tools.
6. Verify that the app exposes exactly one model-controlled tool:
   `prism_analyze(company)` / `PRISM_ANALYZE`.
7. Test with a non-sensitive company request before publishing the app to other
   users.

Tool discovery is a deployment gate. If ChatGPT sees an unexpected valuation
or generic shell tool, do not publish the app.

## 8. Network boundary

Secure MCP Tunnel is outbound initiated. The PRISM host does not need an inbound
public MCP port. Permit the outbound HTTPS connectivity required by the OpenAI
control plane and keep local MCP/stdin/stdout private to the tunnel runtime.

## 9. Runtime secret boundary

`tunnel-client` receives the runtime key through:

```text
--runtime-api-key env:CONTROL_PLANE_API_KEY
```

The child command is not `prism-mcp` directly. It is:

```text
python -m valuation_engine.mcp_tunnel_child
```

That wrapper removes tunnel/admin credentials from the inherited environment
before importing and running `valuation_engine.mcp_server`. PRISM-specific model
credentials remain available only when the configured proposal transport needs
them.

## 10. Failure behavior

A tunnel only transports the MCP call. It does not change PRISM authority.

```text
missing source / provider
→ existing PRISM blocker

multiple viable valuation methods
→ VALUATION_METHOD_INTENT / AWAITING_USER_DECISION

unattested completed result
→ MCP ToolError

blocked intrinsic run
→ sanitized VALUATION_BLOCKED result

successful canonical run
→ execution_mode=LIVE_PRIMARY + engine-owned report
```

There is no MCP-side fallback valuation.

## 11. Operational verification checklist

Before calling the integration complete, verify all of the following:

- `prism-tunnel check` is clean.
- runtime host is Linux/WSL2; native macOS and Windows are rejected.
- tunnel-client is an approved binary.
- `VALUATION_MCP_STATE_ROOT` is explicitly configured as an absolute path.
- state root is on persistent storage, mode `0700`, with no access/default POSIX ACL xattrs.
- `VALUATION_METHOD` is unset unless an explicit override is intended.
- tunnel runtime status reports running/healthy/ready.
- ChatGPT scans exactly `prism_analyze` as the valuation tool.
- a test request reaches `LIVE_PRIMARY`.
- a deliberately blocked fixture returns no intrinsic value and no raw provider
  secret/rationale.
- the resulting PRISM run directory contains the expected immutable execution
  attestation when the run completes.
