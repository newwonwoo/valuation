from __future__ import annotations

import os
from collections.abc import MutableMapping


_TUNNEL_ONLY_SECRET_ENV = (
    "CONTROL_PLANE_API_KEY",
    "OPENAI_ADMIN_KEY",
    "TUNNEL_MCP_ADMIN_KEY",
    "CONTROL_PLANE_CLIENT_CERT",
    "CONTROL_PLANE_CLIENT_KEY",
    "CLOUDFLARED_TOKEN",
    "CLOUDFLARED_TUNNEL_TOKEN",
)


def scrub_tunnel_only_secrets(
    environ: MutableMapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Remove tunnel-control secrets before starting the PRISM MCP child.

    The valuation process may legitimately need its own model credential, so
    ``OPENAI_API_KEY`` is deliberately not removed. Operators should use the
    dedicated ``CONTROL_PLANE_API_KEY`` for the tunnel runtime.
    """
    target = os.environ if environ is None else environ
    removed: list[str] = []
    for name in _TUNNEL_ONLY_SECRET_ENV:
        if name in target:
            target.pop(name, None)
            removed.append(name)
    return tuple(removed)


def main() -> None:
    scrub_tunnel_only_secrets()
    from .mcp_server import main as run_mcp

    run_mcp()


if __name__ == "__main__":
    main()
