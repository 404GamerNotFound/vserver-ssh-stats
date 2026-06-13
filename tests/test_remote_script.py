"""Tests for the embedded remote collector script."""
from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
REMOTE_SCRIPT_PATH = ROOT / "custom_components" / "vserver_ssh_stats" / "remote_script.py"


def _remote_script() -> str:
    module = ast.parse(REMOTE_SCRIPT_PATH.read_text())
    return ast.literal_eval(module.body[0].value)


def test_remote_script_has_valid_bash_syntax() -> None:
    """Check the shell payload independently from the Python wrapper."""

    result = subprocess.run(
        ["bash", "-n"],
        input=_remote_script(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_docker_collector_includes_running_and_stopped_containers() -> None:
    """Verify one bulk Docker inventory supports stopped containers."""

    docker_stub = r'''
timeout() { shift; "$@"; }
docker() {
  case "$1" in
    info) return 0 ;;
    ps)
      [ "$2" = "-a" ] || return 23
      printf '%s\n' \
        'abc123|running-app|repo/app:1|Up 2 hours|8080/tcp' \
        'def456|stopped-app|repo/app:2|Exited (0) 3 hours ago|'
      ;;
    stats) printf '%s\n' 'running-app|1.25%|4.50%' ;;
    inspect) printf '%s\n' 'abc123full|2|true|healthy' 'def456full|0|false|exited' ;;
    *) return 24 ;;
  esac
}
'''
    result = subprocess.run(
        ["bash"],
        input=docker_stub + _remote_script(),
        text=True,
        capture_output=True,
        check=False,
        env=os.environ
        | {
            "VSERVER_SSH_STATS_MODE": "docker",
            "VSERVER_SSH_STATS_DOCKER_TIMEOUT": "5",
        },
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["containers"] == "running-app, stopped-app"
    assert data["docker_stats_complete"] == 1
    assert data["container_stats"][0]["id"] == "abc123"
    assert data["container_stats"][0]["cpu"] == 1.25
    assert data["container_stats"][0]["running"] is True
    assert data["container_stats"][1]["id"] == "def456"
    assert data["container_stats"][1]["cpu"] is None
    assert data["container_stats"][1]["running"] is False
    assert data["container_stats"][1]["status"].startswith("Exited (0)")
