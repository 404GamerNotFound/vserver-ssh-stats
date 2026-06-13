"""Tests for verified Docker action commands."""
from __future__ import annotations

import ast
import os
import subprocess
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
    """Only report stop success after Docker remains stopped."""

    commands = _command_builder()("stop", "grafana")

    assert commands[0].startswith("docker stop grafana")
    assert "inspect --format '{{.State.Running}}' grafana" in commands[0]
    assert 'while [ "$attempt" -lt 4 ]' in commands[0]
    assert 'stable=$((stable + 1))' in commands[0]
    assert 'docker stop grafana >/dev/null 2>&1' in commands[0]
    assert '[ "$state" = "false" ]' in commands[0]
    assert '[ "$stable" -ge 2 ]' in commands[0]
    assert commands[1].startswith("sudo docker stop grafana")


def test_start_and_restart_commands_require_running_state() -> None:
    """Only report start/restart success when Docker confirms running state."""

    for action in ("start", "restart"):
        command = _command_builder()(action, "grafana")[0]
        assert f"docker {action} grafana" in command
        assert '[ "$state" = "true" ]' in command


def test_stop_retries_after_container_starts_again(tmp_path: Path) -> None:
    """Stop a container again when it restarts during convergence checks."""

    command = _command_builder()("stop", "grafana")[0]
    stop_count = tmp_path / "stop-count"
    inspect_count = tmp_path / "inspect-count"
    script = f'''
sleep() {{ :; }}
docker() {{
  case "$1" in
    stop)
      count=$(cat "$STOP_COUNT" 2>/dev/null || printf 0)
      printf '%s' "$((count + 1))" > "$STOP_COUNT"
      ;;
    inspect)
      count=$(cat "$INSPECT_COUNT" 2>/dev/null || printf 0)
      count=$((count + 1))
      printf '%s' "$count" > "$INSPECT_COUNT"
      if [ "$count" -eq 2 ]; then printf 'true\\n'; else printf 'false\\n'; fi
      ;;
  esac
}}
{command}
'''
    result = subprocess.run(
        ["bash", "-c", script],
        text=True,
        capture_output=True,
        check=False,
        env=os.environ
        | {
            "STOP_COUNT": str(stop_count),
            "INSPECT_COUNT": str(inspect_count),
        },
    )

    assert result.returncode == 0, result.stderr
    assert stop_count.read_text() == "2"
    assert "running=false stable_checks=2" in result.stdout
