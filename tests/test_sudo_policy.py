"""Regression coverage for root-only hosts whose sudo is a no-op (issue #197)."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

INTEGRATION = Path(__file__).parents[1] / "custom_components" / "vserver_ssh_stats"


def _load_functions(filename, names, namespace=None, parent=None):
    """Compile selected functions with deferred annotations, without importing HA."""
    tree = ast.parse((INTEGRATION / filename).read_text())
    if parent:
        tree = next(node for node in tree.body if getattr(node, "name", None) == parent)
    nodes = [node for node in tree.body if getattr(node, "name", None) in names]
    assert len(nodes) == len(names)
    scope = dict(namespace or {})
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
              *nodes],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), filename, "exec"), scope)
    return scope


def _actions():
    return _load_functions(
        "__init__.py",
        {
            "_build_os_command_sequence", "_normalize_target_os",
            "_server_context_for_host", "_use_sudo_for_host",
            "_build_update_commands", "_build_package_list_update_commands",
            "_build_clear_package_cache_commands", "_build_reboot_commands",
            "_build_restart_service_commands", "_build_docker_container_commands",
            "_build_docker_prune_commands", "_build_tail_logs_commands",
        },
        {"DOMAIN": "vserver_ssh_stats", "LINUX_TARGET_OS": {"debian", "raspbian"},
         "SUPPORTED_TARGET_OS": {"auto", "debian", "raspbian", "windows"}},
    )


@pytest.mark.parametrize(("name", "args"), [
    ("update", ("debian",)),
    ("package_list_update", ("debian",)),
    ("clear_package_cache", ("debian",)),
    ("reboot", ("debian",)),
    ("restart_service", ("debian", "kodi")),
    ("docker_container", ("start", "kodi")),
    ("docker_container", ("stop", "kodi")),
    ("docker_container", ("restart", "kodi")),
    ("docker_prune", ()),
    ("tail_logs", ("debian", "kodi", 50)),
    ("tail_logs", ("debian", None, 50)),
])
def test_actions_can_disable_sudo_without_changing_legacy_default(name, args):
    builder = _actions()[f"_build_{name}_commands"]
    assert any("sudo " in command for command in builder(*args))
    direct = builder(*args, use_sudo=False)
    assert len(direct) == 1
    assert "sudo " not in direct[0]
    assert subprocess.run(["bash", "-n"], input=direct[0], text=True).returncode == 0


@pytest.mark.parametrize("use_sudo", [True, False])
def test_libreelec_noop_sudo_cannot_swallow_disabled_actions(use_sudo):
    actions = _actions()
    commands = actions["_build_reboot_commands"]("debian", use_sudo=use_sudo)
    commands += actions["_build_restart_service_commands"](
        "debian", "kodi", use_sudo=use_sudo
    )
    # No real reboot/service command can run: all commands are local shell functions.
    script = '''
sudo() { echo 'sudo is not needed'; }
reboot() { echo reboot-executed; }
systemctl() { echo "systemctl-executed $*"; }
'''
    result = subprocess.run(
        ["bash"], input=script + "\n".join(commands) + "\nwait\n",
        text=True, capture_output=True, check=True,
    )
    if use_sudo:
        assert "executed" not in result.stdout
    else:
        assert "reboot-executed" in result.stdout
        assert "systemctl-executed restart kodi" in result.stdout
        assert "sudo is not needed" not in result.stdout


@pytest.mark.parametrize("configured", [None, {}, {"use_sudo": False}, {"use_sudo": True}])
def test_service_uses_saved_host_policy(configured):
    server = {"host": "libreelec", **(configured or {})}
    hass = SimpleNamespace(data={"vserver_ssh_stats": {
        "entry": {"servers": [] if configured is None else [server]},
        "action_status": {},
    }})
    run = AsyncMock(return_value={"success": True})
    scope = _load_functions(
        "__init__.py", {"handle_reboot_host"},
        {**_actions(), "hass": hass, "_run_remote_action": run}, parent="async_setup",
    )
    asyncio.run(scope["handle_reboot_host"](
        SimpleNamespace(data={"host": "libreelec", "target_os": "debian"})
    ))
    expected = "reboot &" if configured == {"use_sudo": False} else "sudo reboot &"
    assert run.call_args.args[2] == [expected]


@pytest.mark.parametrize("mode", ["base", "packages", "docker", "storage"])
@pytest.mark.parametrize("use_sudo", [True, False])
def test_collector_environment_and_windows_commands(mode, use_sudo):
    scope = _load_functions(
        "ssh_collector.py", {"_build_collection_commands"},
        {"REMOTE_SCRIPT": "collector", "WINDOWS_REMOTE_SCRIPT": "powershell"},
    )
    builder = scope["_build_collection_commands"]
    commands = builder("debian", mode, use_sudo=use_sudo)
    linux = [(command, payload) for command, payload in commands if payload]
    assert len(linux) == 2
    assert all(f"VSERVER_SSH_STATS_USE_SUDO={int(use_sudo)}" in cmd for cmd, _ in linux)
    assert builder("windows", mode, use_sudo=use_sudo) == (
        [("powershell", None)] if mode == "base" else []
    )


@pytest.mark.parametrize("mode", ["base", "packages", "docker", "storage"])
def test_sampling_passes_policy_to_raw_collector(mode):
    name = "async_sample" + ("" if mode == "base" else f"_{mode}")
    # Stop at the transport boundary; no SSH or HA dependencies are required.
    class Collected(Exception):
        pass

    collect = AsyncMock(side_effect=Collected)
    scope = _load_functions("ssh_collector.py", {name}, {
        "asyncio": asyncio, "DEFAULT_CONNECT_TIMEOUT": 10, "DEFAULT_COMMAND_TIMEOUT": 45,
        "_async_collect_raw": collect, "_async_check_monitored_ports": AsyncMock(return_value=[]),
    })
    with pytest.raises(Collected):
        asyncio.run(scope[name]("libreelec", "root", "password", None, 22, use_sudo=False))
    assert collect.call_args.kwargs["use_sudo"] is False


@pytest.mark.parametrize("existing", [None, {"use_sudo": True}, {"use_sudo": False}])
@pytest.mark.parametrize("use_sudo", [True, False])
def test_server_options_save_sudo_choice(existing, use_sudo):
    scope = _load_functions(
        "config_flow.py", {"_server_from_input"}, {
            "DEFAULT_HISTORY_RETENTION_DAYS": 10, "MAX_HISTORY_RETENTION_DAYS": 365,
            "_coerce_positive_int": lambda value, default: value or default,
            "parse_host_key_fingerprints": lambda value: [value],
            "parse_monitored_ports": lambda value: [],
            "_async_test_ssh_connection": AsyncMock(return_value=None),
        }, parent="OptionsFlowHandler",
    )
    flow = SimpleNamespace(_existing_servers=[], _host_already_configured=lambda host: False)
    errors = {}
    server = asyncio.run(scope["_server_from_input"](flow, {
        "host": "libreelec", "name": "Cinema", "username": "root", "port": 22,
        "password": "password", "use_sudo": use_sudo, "host_key_fingerprints": "fingerprint",
    }, errors, existing=existing))
    assert errors == {}
    assert json.loads(json.dumps(server))["use_sudo"] is use_sudo


@pytest.mark.parametrize("choice", [{}, {"use_sudo": False}, {"use_sudo": True}])
def test_initial_setup_persists_sudo_choice_and_default(choice):
    defaults = {
        name: 30 for name in (
            "DEFAULT_INTERVAL", "DEFAULT_HISTORY_RETENTION_DAYS", "DEFAULT_CONNECT_TIMEOUT",
            "DEFAULT_COMMAND_TIMEOUT", "DEFAULT_PACKAGE_INTERVAL", "DEFAULT_DOCKER_INTERVAL",
            "DEFAULT_STORAGE_INTERVAL", "DEFAULT_SLOW_COMMAND_TIMEOUT",
        )
    }
    scope = _load_functions("config_flow.py", {"async_step_user"}, {
        **defaults, "DEFAULT_COMMAND_ALLOWLIST": "", "MAX_HISTORY_RETENTION_DAYS": 365,
        "hashlib": hashlib, "json": json,
        "_coerce_positive_int": lambda value, default: value or default,
        "parse_host_key_fingerprints": lambda value: [value],
        "parse_monitored_ports": lambda value: [],
        "_async_test_ssh_connection": AsyncMock(return_value=None),
    }, parent="ConfigFlow")
    flow = SimpleNamespace(
        _servers=[], _host_already_configured=lambda host: False,
        async_set_unique_id=AsyncMock(), _abort_if_unique_id_configured=lambda: None,
        async_create_entry=lambda **kwargs: kwargs,
    )
    result = asyncio.run(scope["async_step_user"](flow, {
        "host": "libreelec", "name": "Cinema", "username": "root", "port": 22,
        "password": "password", "host_key_fingerprints": "fingerprint", "interval": 30,
        **choice,
    }))
    server = json.loads(result["data"]["servers_json"])[0]
    assert server["use_sudo"] is choice.get("use_sudo", True)
