"""Tests for verified Docker action commands."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
INIT_PATH = ROOT / "custom_components" / "vserver_ssh_stats" / "__init__.py"


def _command_builder():
    tree = ast.parse(INIT_PATH.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_build_docker_container_commands"
    )
    namespace: dict[str, object] = {}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "<docker-actions>", "exec"),
        namespace,
    )
    return namespace["_build_docker_container_commands"]


def test_stop_command_requires_confirmed_stopped_state() -> None:
    """Only report stop success when Docker confirms State.Running=false."""

    commands = _command_builder()("stop", "grafana")

    assert commands[0].startswith("docker stop grafana")
    assert "inspect --format '{{.State.Running}}' grafana" in commands[0]
    assert '[ "$state" = "false" ]' in commands[0]
    assert commands[1].startswith("sudo docker stop grafana")


def test_start_and_restart_commands_require_running_state() -> None:
    """Only report start/restart success when Docker confirms running state."""

    for action in ("start", "restart"):
        command = _command_builder()(action, "grafana")[0]
        assert f"docker {action} grafana" in command
        assert '[ "$state" = "true" ]' in command
