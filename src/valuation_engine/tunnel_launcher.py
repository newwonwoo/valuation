from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Mapping, Sequence


_TUNNEL_ID_RE = re.compile(r"^tunnel_[0-9a-f]{32}$")
_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DEFAULT_ALIAS = "prism-valuation"
_REQUIRED_PRISM_ENV = ("DART_API_KEY", "VALUATION_LLM_TRANSPORT")


class PrismTunnelError(RuntimeError):
    pass


def _environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    return dict(os.environ if environ is None else environ)


def _tunnel_client(env: Mapping[str, str]) -> str:
    explicit = env.get("TUNNEL_CLIENT_BIN", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise PrismTunnelError("TUNNEL_CLIENT_BIN does not point to a file")
        return str(path)
    discovered = shutil.which("tunnel-client")
    if discovered:
        return discovered
    raise PrismTunnelError(
        "tunnel-client is not installed; use the supported OpenAI tunnel-client "
        "release before connecting PRISM"
    )


def _tunnel_id(env: Mapping[str, str]) -> str:
    value = env.get("CONTROL_PLANE_TUNNEL_ID", "").strip()
    if not _TUNNEL_ID_RE.fullmatch(value):
        raise PrismTunnelError(
            "CONTROL_PLANE_TUNNEL_ID must be tunnel_ followed by 32 lowercase hex characters"
        )
    return value


def _alias(env: Mapping[str, str]) -> str:
    value = env.get("PRISM_TUNNEL_ALIAS", _DEFAULT_ALIAS).strip()
    if not _ALIAS_RE.fullmatch(value):
        raise PrismTunnelError("PRISM_TUNNEL_ALIAS has invalid characters")
    return value


def _prepare_runtime_environment(
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = _environment(environ)
    missing = tuple(name for name in _REQUIRED_PRISM_ENV if not env.get(name, "").strip())
    if missing:
        raise PrismTunnelError(
            "PRISM runtime environment is incomplete: " + ", ".join(missing)
        )
    if not env.get("CONTROL_PLANE_API_KEY", "").strip():
        raise PrismTunnelError(
            "CONTROL_PLANE_API_KEY is required as the dedicated Tunnels Read + Use runtime key"
        )
    root = Path(env.get("VALUATION_MCP_STATE_ROOT", ".valuation_state")).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise PrismTunnelError("VALUATION_MCP_STATE_ROOT is not a directory")
    env["VALUATION_MCP_STATE_ROOT"] = str(root)
    env.setdefault("VALUATION_MCP_JURISDICTION", "KR")
    return env


def _mcp_child_command() -> str:
    return shlex.join(
        (sys.executable, "-m", "valuation_engine.mcp_tunnel_child")
    )


def build_connect_command(
    *,
    binary: str,
    alias: str,
    tunnel_id: str,
) -> tuple[str, ...]:
    return (
        binary,
        "runtimes",
        "connect",
        "--alias",
        alias,
        "--tunnel-id",
        tunnel_id,
        "--runtime-api-key",
        "env:CONTROL_PLANE_API_KEY",
        "--mcp-command",
        _mcp_child_command(),
        "--json",
    )


def build_status_command(*, binary: str, alias: str) -> tuple[str, ...]:
    return (binary, "runtimes", "status", alias, "--json")


def build_stop_command(*, binary: str, alias: str) -> tuple[str, ...]:
    return (binary, "runtimes", "stop", alias, "--json")


def _run(command: tuple[str, ...], *, env: Mapping[str, str]) -> dict[str, object]:
    completed = subprocess.run(
        command,
        env=dict(env),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "tunnel-client failed").strip()
        raise PrismTunnelError(detail)
    text = completed.stdout.strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PrismTunnelError("tunnel-client did not return valid JSON") from exc
    if not isinstance(payload, dict):
        raise PrismTunnelError("tunnel-client JSON response must be an object")
    return payload


def connect(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    env = _prepare_runtime_environment(environ)
    binary = _tunnel_client(env)
    alias = _alias(env)
    tunnel_id = _tunnel_id(env)
    _run(
        build_connect_command(binary=binary, alias=alias, tunnel_id=tunnel_id),
        env=env,
    )
    status = _run(build_status_command(binary=binary, alias=alias), env=env)
    status.setdefault("alias", alias)
    status.setdefault("tunnel_id", tunnel_id)
    return status


def status(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    env = _environment(environ)
    binary = _tunnel_client(env)
    alias = _alias(env)
    return _run(build_status_command(binary=binary, alias=alias), env=env)


def stop(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    env = _environment(environ)
    binary = _tunnel_client(env)
    alias = _alias(env)
    return _run(build_stop_command(binary=binary, alias=alias), env=env)


def check(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    env = _prepare_runtime_environment(environ)
    return {
        "status": "READY_TO_CONNECT",
        "tunnel_client": _tunnel_client(env),
        "tunnel_id": _tunnel_id(env),
        "alias": _alias(env),
        "mcp_command": _mcp_child_command(),
        "state_root": env["VALUATION_MCP_STATE_ROOT"],
        "method_override": bool(env.get("VALUATION_METHOD", "").strip()),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Connect the canonical PRISM stdio MCP through OpenAI Secure MCP Tunnel"
    )
    parser.add_argument(
        "action",
        choices=("check", "connect", "status", "stop"),
        nargs="?",
        default="check",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "check":
            payload = check()
        elif args.action == "connect":
            payload = connect()
        elif args.action == "status":
            payload = status()
        else:
            payload = stop()
    except PrismTunnelError as exc:
        print(f"ERROR [PRISM_TUNNEL] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
