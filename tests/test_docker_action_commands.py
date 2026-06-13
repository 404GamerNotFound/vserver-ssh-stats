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

    assert commands[0].startswith("restart_policy=$(docker inspect")
    assert "docker stop grafana" in commands[0]
    assert "inspect --format '{{.State.Running}}' grafana" in commands[0]
    assert 'while [ "$attempt" -lt 10 ]' in commands[0]
    assert 'stable=$((stable + 1))' in commands[0]
    assert 'docker stop grafana >/dev/null 2>&1' in commands[0]
    assert "docker update --restart=no grafana" in commands[0]
    assert '[ "$state" = "false" ]' in commands[0]
    assert '[ "$stable" -ge 2 ]' in commands[0]
    assert commands[1].startswith("restart_policy=$(sudo docker inspect")
    assert "sudo docker stop grafana" in commands[1]


def test_start_and_restart_commands_require_running_state() -> None:
    """Only report start/restart success when Docker confirms running state."""

    for action in ("start", "restart"):
        command = _command_builder()(action, "grafana")[0]
        assert f"docker {action} grafana" in command
        assert '[ "$state" = "true" ]' in command

    restored_start = _command_builder()("start", "grafana", "unless-stopped")[0]
    assert "docker update --restart=unless-stopped grafana" in restored_start


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
      case "$3" in
        *RestartPolicy*) printf 'always\\n' ;;
        *compose*) printf '\\n' ;;
        *)
          count=$(cat "$INSPECT_COUNT" 2>/dev/null || printf 0)
          count=$((count + 1))
          printf '%s' "$count" > "$INSPECT_COUNT"
          if [ "$count" -eq 2 ]; then printf 'true\\n'; else printf 'false\\n'; fi
          ;;
      esac
      ;;
    compose) return 1 ;;
    update)
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
    assert "running=false stable_checks=2 previous_restart_policy=always" in result.stdout


def test_stop_uses_compose_service_when_labels_are_present(tmp_path: Path) -> None:
    """Stop the owning Compose service before enforcing container state."""

    command = _command_builder()("stop", "grafana")[0]
    compose_log = tmp_path / "compose-log"
    script = f'''
sleep() {{ :; }}
docker() {{
  case "$1" in
    inspect)
      case "$3" in
        *RestartPolicy*) printf 'unless-stopped\\n' ;;
        *project.config_files*) printf '/tmp/docker-compose.yml\\n' ;;
        *project.working_dir*) printf '{tmp_path}\\n' ;;
        *compose.project*) printf 'monitoring\\n' ;;
        *compose.service*) printf 'grafana\\n' ;;
        *State.Running*) printf 'false\\n' ;;
      esac
      ;;
    compose)
      printf '%s\\n' "$*" >> "$COMPOSE_LOG"
      ;;
    update|stop) ;;
  esac
}}
{command}
'''
    result = subprocess.run(
        ["bash", "-c", script],
        text=True,
        capture_output=True,
        check=False,
        env=os.environ | {"COMPOSE_LOG": str(compose_log)},
    )

    assert result.returncode == 0, result.stderr
    assert (
        "compose -f /tmp/docker-compose.yml -p monitoring stop grafana"
        in compose_log.read_text()
    )
