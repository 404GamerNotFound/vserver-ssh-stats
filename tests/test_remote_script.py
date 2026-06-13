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
    stats) printf '%s\n' 'abc123|running-app|1.25%|4.50%' ;;
    inspect) printf '%s\n' \
      'abc123full|2|true|healthy|unless-stopped|monitoring|grafana|' \
      'def456full|0|false|exited|no|monitoring|stopped-app|' ;;
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
    assert data["container_stats"][0]["restart_policy"] == "unless-stopped"
    assert data["container_stats"][0]["compose_project"] == "monitoring"
    assert data["container_stats"][0]["compose_service"] == "grafana"
    assert data["container_stats"][1]["id"] == "def456"
    assert data["container_stats"][1]["cpu"] is None
    assert data["container_stats"][1]["running"] is False
    assert data["container_stats"][1]["status"].startswith("Exited (0)")


def test_docker_collector_does_not_turn_parse_errors_into_zero() -> None:
    """Map stats by container ID and preserve invalid percentages as null."""

    docker_stub = r'''
timeout() { shift; "$@"; }
docker() {
  case "$1" in
    info) return 0 ;;
    ps)
      printf '%s\n' \
        'abc123|running-app|repo/app:1|Up 2 hours|8080/tcp' \
        'def456|second-app|repo/app:2|Up 1 hour|'
      ;;
    stats) printf '%s\n' \
      'abc123|renamed-output|1,25%|4,50%' \
      'def456|second-app|not-a-number|unknown'
      ;;
    inspect) printf '%s\n' \
      'abc123full|0|true|healthy|unless-stopped|||' \
      'def456full|0|true|healthy|unless-stopped|||'
      ;;
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
    assert data["container_stats"][0]["cpu"] == 1.25
    assert data["container_stats"][0]["mem"] == 4.5
    assert data["container_stats"][1]["cpu"] is None
    assert data["container_stats"][1]["mem"] is None


def test_docker_collector_retries_an_all_zero_stats_sample(tmp_path: Path) -> None:
    """Retry once when Docker reports zero CPU and memory for every container."""

    docker_stub = r'''
sleep() { :; }
timeout() { shift; "$@"; }
docker() {
  case "$1" in
    info) return 0 ;;
    ps) printf '%s\n' 'abc123|running-app|repo/app:1|Up 2 hours|' ;;
    stats)
      count=$(cat "$STATS_COUNT" 2>/dev/null || printf 0)
      count=$((count + 1))
      printf '%s' "$count" > "$STATS_COUNT"
      if [ "$count" -eq 1 ]; then
        printf '%s\n' 'abc123|running-app|0.00%|0.00%'
      else
        printf '%s\n' 'abc123|running-app|2.50%|8.75%'
      fi
      ;;
    inspect) printf '%s\n' 'abc123full|0|true|healthy|unless-stopped|||' ;;
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
            "STATS_COUNT": str(tmp_path / "stats-count"),
            "VSERVER_SSH_STATS_MODE": "docker",
            "VSERVER_SSH_STATS_DOCKER_TIMEOUT": "5",
        },
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["container_stats"][0]["cpu"] == 2.5
    assert data["container_stats"][0]["mem"] == 8.75


def test_docker_collector_falls_back_to_passwordless_sudo() -> None:
    """Use sudo for Docker metrics when the SSH user lacks socket access."""

    docker_stub = r'''
timeout() { shift; "$@"; }
sudo() {
  [ "$1" = "-n" ] && shift
  DOCKER_VIA_SUDO=1 "$@"
}
docker() {
  if [ "${DOCKER_VIA_SUDO:-0}" != "1" ]; then
    return 1
  fi
  case "$1" in
    info) return 0 ;;
    ps) printf '%s\n' 'abc123|running-app|repo/app:1|Up 2 hours|' ;;
    stats) printf '%s\n' 'abc123|running-app|3.25%|7.50%' ;;
    inspect) printf '%s\n' 'abc123full|0|true|healthy|unless-stopped|||' ;;
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
    assert data["container_stats"][0]["cpu"] == 3.25
    assert data["container_stats"][0]["mem"] == 7.5
