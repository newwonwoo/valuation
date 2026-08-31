from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

import pytest

import valuation_engine.mcp_tunnel_child as tunnel_child
import valuation_engine.tunnel_launcher as launcher


_NATIVE_LINUX = launcher._supports_private_posix_permissions()


def test_tunnel_child_scrubs_control_plane_secrets_but_keeps_model_key():
    env = {
        "CONTROL_PLANE_API_KEY": "runtime-secret",
        "OPENAI_ADMIN_KEY": "admin-secret",
        "CLOUDFLARED_TOKEN": "cloudflare-secret",
        "OPENAI_API_KEY": "model-secret",
        "DART_API_KEY": "dart-secret",
    }
    removed = tunnel_child.scrub_tunnel_only_secrets(env)
    assert set(removed) == {
        "CONTROL_PLANE_API_KEY",
        "OPENAI_ADMIN_KEY",
        "CLOUDFLARED_TOKEN",
    }
    assert "CONTROL_PLANE_API_KEY" not in env
    assert "OPENAI_ADMIN_KEY" not in env
    assert "CLOUDFLARED_TOKEN" not in env
    assert env["OPENAI_API_KEY"] == "model-secret"
    assert env["DART_API_KEY"] == "dart-secret"


def test_connect_command_uses_secret_reference_and_stdio_child():
    command = launcher.build_connect_command(
        binary="/trusted/tunnel-client",
        alias="prism-valuation",
        tunnel_id="tunnel_0123456789abcdef0123456789abcdef",
    )
    assert command[:3] == (
        "/trusted/tunnel-client",
        "runtimes",
        "connect",
    )
    assert "env:CONTROL_PLANE_API_KEY" in command
    assert "runtime-secret" not in command
    child = command[command.index("--mcp-command") + 1]
    assert "valuation_engine.mcp_tunnel_child" in child
    assert sys.executable in child
    assert command[-1] == "--json"


def _base_env():
    return {
        "DART_API_KEY": "dart",
        "VALUATION_LLM_TRANSPORT": "transport.module:build",
        "CONTROL_PLANE_API_KEY": "runtime",
    }


def test_prepare_runtime_environment_requires_separate_tunnel_key(tmp_path):
    env = {
        "DART_API_KEY": "dart",
        "VALUATION_LLM_TRANSPORT": "transport.module:build",
        "VALUATION_MCP_STATE_ROOT": str(tmp_path / "state"),
    }
    with pytest.raises(launcher.PrismTunnelError, match="CONTROL_PLANE_API_KEY"):
        launcher._prepare_runtime_environment(env)


def test_prepare_runtime_environment_rejects_non_linux_permission_host(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "_supports_private_posix_permissions", lambda: False)
    env = {**_base_env(), "VALUATION_MCP_STATE_ROOT": str(tmp_path / "state")}
    with pytest.raises(launcher.PrismTunnelError, match="native Linux host"):
        launcher._prepare_runtime_environment(env)


def test_wsl_runtime_is_not_supported(monkeypatch):
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setattr(launcher, "_is_wsl_runtime", lambda: True)
    assert launcher._supports_private_posix_permissions() is False


def test_prepare_runtime_environment_requires_explicit_state_root(monkeypatch):
    monkeypatch.setattr(launcher, "_supports_private_posix_permissions", lambda: True)
    with pytest.raises(launcher.PrismTunnelError, match="VALUATION_MCP_STATE_ROOT"):
        launcher._prepare_runtime_environment(_base_env())


def test_prepare_runtime_environment_rejects_relative_state_root(monkeypatch):
    monkeypatch.setattr(launcher, "_supports_private_posix_permissions", lambda: True)
    env = {**_base_env(), "VALUATION_MCP_STATE_ROOT": "relative/prism-state"}
    with pytest.raises(launcher.PrismTunnelError, match="absolute"):
        launcher._prepare_runtime_environment(env)


def test_mountinfo_uses_the_most_specific_mount():
    text = (
        "20 1 8:1 / / rw,relatime - ext4 /dev/root rw\n"
        "21 20 0:44 / /mnt/data rw,relatime - cifs //server/share rw\n"
    )
    fs_type, mount_point = launcher._filesystem_type_for_path(
        Path("/mnt/data/prism/state"), mountinfo_text=text
    )
    assert fs_type == "cifs"
    assert mount_point == Path("/mnt/data")


def test_mountinfo_decodes_escaped_mount_paths():
    text = "20 1 8:1 / /mnt/private\\040data rw - ext4 /dev/root rw\n"
    fs_type, mount_point = launcher._filesystem_type_for_path(
        Path("/mnt/private data/prism"), mountinfo_text=text
    )
    assert fs_type == "ext4"
    assert mount_point == Path("/mnt/private data")


def test_foreign_filesystem_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "_supports_private_posix_permissions", lambda: True)
    monkeypatch.setattr(
        launcher,
        "_filesystem_type_for_path",
        lambda root: ("cifs", Path("/mnt/share")),
    )
    with pytest.raises(launcher.PrismTunnelError, match="verified local Linux filesystem"):
        launcher._prepare_runtime_environment(
            {**_base_env(), "VALUATION_MCP_STATE_ROOT": str(tmp_path / "state")}
        )


@pytest.mark.skipif(not _NATIVE_LINUX, reason="native Linux tunnel security contract")
def test_prepare_runtime_environment_resolves_persistent_state_root(tmp_path):
    env = launcher._prepare_runtime_environment(
        {
            **_base_env(),
            "VALUATION_MCP_STATE_ROOT": str(tmp_path / "state"),
        }
    )
    root = Path(env["VALUATION_MCP_STATE_ROOT"])
    assert root.is_absolute()
    assert root.is_dir()
    assert env["VALUATION_MCP_JURISDICTION"] == "KR"


@pytest.mark.skipif(not _NATIVE_LINUX, reason="native Linux tunnel security contract")
def test_new_tunnel_state_root_is_private(tmp_path):
    root = tmp_path / "private-state"
    launcher._prepare_runtime_environment(
        {**_base_env(), "VALUATION_MCP_STATE_ROOT": str(root)}
    )
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


@pytest.mark.skipif(not _NATIVE_LINUX, reason="native Linux tunnel security contract")
def test_existing_permissive_state_root_is_repaired(tmp_path):
    root = tmp_path / "permissive-state"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    assert stat.S_IMODE(root.stat().st_mode) == 0o755

    launcher._prepare_runtime_environment(
        {**_base_env(), "VALUATION_MCP_STATE_ROOT": str(root)}
    )

    assert stat.S_IMODE(root.stat().st_mode) == 0o700


@pytest.mark.skipif(not _NATIVE_LINUX, reason="native Linux tunnel security contract")
def test_extended_linux_acl_metadata_fails_closed(monkeypatch, tmp_path):
    root = tmp_path / "acl-state"
    monkeypatch.setattr(
        launcher.os,
        "listxattr",
        lambda path: ["system.posix_acl_access", "system.nfs4_acl"],
    )
    with pytest.raises(launcher.PrismTunnelError, match="extended ACL metadata"):
        launcher._prepare_runtime_environment(
            {**_base_env(), "VALUATION_MCP_STATE_ROOT": str(root)}
        )


@pytest.mark.skipif(not _NATIVE_LINUX, reason="native Linux tunnel security contract")
def test_unverifiable_linux_acl_metadata_fails_closed(monkeypatch, tmp_path):
    root = tmp_path / "acl-unverifiable"

    def deny(_path):
        raise OSError("not permitted")

    monkeypatch.setattr(launcher.os, "listxattr", deny)
    with pytest.raises(launcher.PrismTunnelError, match="ACL metadata cannot be verified"):
        launcher._prepare_runtime_environment(
            {**_base_env(), "VALUATION_MCP_STATE_ROOT": str(root)}
        )


@pytest.mark.skipif(not _NATIVE_LINUX, reason="native Linux tunnel security contract")
def test_check_never_returns_secret_values(monkeypatch, tmp_path):
    fake_bin = tmp_path / "tunnel-client"
    fake_bin.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: str(fake_bin))
    payload = launcher.check(
        {
            "DART_API_KEY": "DART-TOP-SECRET",
            "VALUATION_LLM_TRANSPORT": "transport.module:build",
            "CONTROL_PLANE_API_KEY": "TUNNEL-TOP-SECRET",
            "CONTROL_PLANE_TUNNEL_ID": "tunnel_0123456789abcdef0123456789abcdef",
            "VALUATION_MCP_STATE_ROOT": str(tmp_path / "state"),
        }
    )
    rendered = repr(payload)
    assert payload["status"] == "READY_TO_CONNECT"
    assert payload["method_override"] is False
    assert "DART-TOP-SECRET" not in rendered
    assert "TUNNEL-TOP-SECRET" not in rendered


def _runtime_env(tmp_path):
    return {
        "DART_API_KEY": "dart",
        "VALUATION_LLM_TRANSPORT": "transport.module:build",
        "CONTROL_PLANE_API_KEY": "runtime",
        "CONTROL_PLANE_TUNNEL_ID": "tunnel_0123456789abcdef0123456789abcdef",
        "VALUATION_MCP_STATE_ROOT": str(tmp_path / "state"),
    }


@pytest.mark.skipif(not _NATIVE_LINUX, reason="native Linux tunnel security contract")
def test_connect_runs_managed_runtime_then_checks_status(monkeypatch, tmp_path):
    fake_bin = tmp_path / "tunnel-client"
    fake_bin.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: str(fake_bin))
    calls = []

    def fake_run(command, *, env):
        calls.append(command)
        if command[1:3] == ("runtimes", "connect"):
            return {"connected": True}
        return {"process_running": True, "healthy": True, "ready": True}

    monkeypatch.setattr(launcher, "_run", fake_run)
    payload = launcher.connect(_runtime_env(tmp_path))
    assert len(calls) == 2
    assert calls[0][1:3] == ("runtimes", "connect")
    assert calls[1][1:3] == ("runtimes", "status")
    assert payload["process_running"] is True
    assert payload["healthy"] is True
    assert payload["ready"] is True


@pytest.mark.skipif(not _NATIVE_LINUX, reason="native Linux tunnel security contract")
def test_connect_rejects_runtime_that_is_not_fully_ready(monkeypatch, tmp_path):
    fake_bin = tmp_path / "tunnel-client"
    fake_bin.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: str(fake_bin))

    def fake_run(command, *, env):
        if command[1:3] == ("runtimes", "connect"):
            return {"connected": True}
        return {"process_running": True, "healthy": True, "ready": False}

    monkeypatch.setattr(launcher, "_run", fake_run)
    with pytest.raises(launcher.PrismTunnelError, match="ready"):
        launcher.connect(_runtime_env(tmp_path))
