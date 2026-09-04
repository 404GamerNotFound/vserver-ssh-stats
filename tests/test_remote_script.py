"""Tests for the embedded remote collector script."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
REMOTE_SCRIPT_PATH = ROOT / "custom_components" / "vserver_ssh_stats" / "remote_collector.sh"


def _remote_script() -> str:
    return REMOTE_SCRIPT_PATH.read_text()


def _bash_function(name: str) -> str:
    """Extract one top-level shell function from the embedded payload."""

    script = _remote_script()
    start = script.index(f"{name}() {{")
    end = script.index("\n}\n", start) + len("\n}\n")
    return script[start:end]


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
    stats) printf '%s\n' 'abc123|running-app|1.25%|4.50%|128MiB / 512MiB|17' ;;
    inspect) printf '%s\n' \
      'abc123full|2|true|healthy|unless-stopped|monitoring|grafana||0|536870912' \
      'def456full|0|false|exited|no|monitoring|stopped-app||0|0' ;;
    system) printf '%s\n' 'Images|1.5GiB|500MiB (32%%)' 'Containers|64MiB|0B (0%%)' 'Local Volumes|2GiB|1GiB (50%%)' 'Build Cache|256MiB|128MiB (50%%)' ;;
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
    assert data["container_stats"][0]["memory_usage_bytes"] == 134217728
    assert data["container_stats"][0]["memory_limit_bytes"] == 536870912
    assert data["container_stats"][0]["pids"] == 17
    assert data["container_stats"][0]["running"] is True
    assert data["container_stats"][0]["restart_policy"] == "unless-stopped"
    assert data["container_stats"][0]["compose_project"] == "monitoring"
    assert data["container_stats"][0]["compose_service"] == "grafana"
    assert data["container_stats"][1]["id"] == "def456"
    assert data["container_stats"][1]["cpu"] is None
    assert data["container_stats"][1]["running"] is False
    assert data["container_stats"][1]["status"].startswith("Exited (0)")
    assert data["docker_images_size_bytes"] == 1610612736
    assert data["docker_volumes_size_bytes"] == 2147483648


def test_base_collector_reports_process_socket_and_raid_fields() -> None:
    """The fast collector always returns the new diagnostic metric keys."""

    result = subprocess.run(
        ["bash"],
        input=_remote_script(),
        text=True,
        capture_output=True,
        check=False,
        env=os.environ | {"VSERVER_SSH_STATS_MODE": "base"},
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert {
        "process_total",
        "process_running",
        "process_zombies",
        "tcp_established",
        "tcp_time_wait",
        "sockets_used",
        "conntrack_count",
        "software_raid_arrays",
        "software_raid_degraded",
        "software_raid_rebuild_active",
        "raid_arrays",
        "failed_ssh_logins_15m",
        "firewall_active",
        "firewall_backend",
        "firewall_rules_count",
    }.issubset(data)
    assert data["firewall_active"] == 0
    assert data["firewall_backend"] == ""
    assert data["firewall_rules_count"] is None


def test_failed_ssh_login_collector_counts_recent_failures() -> None:
    """Count sshd authentication failures reported by journalctl."""

    journal_stub = r'''
timeout() { shift; "$@"; }
journalctl() {
  cat <<'EOF'
Jan 01 00:00:01 host sshd[123]: Failed password for invalid user admin from 10.0.0.1 port 4444 ssh2
Jan 01 00:00:02 host sshd[124]: Invalid user test from 10.0.0.2 port 4445
Jan 01 00:00:03 host sshd[125]: Accepted password for alice from 10.0.0.3 port 22 ssh2
EOF
}
'''
    result = subprocess.run(
        ["bash"],
        input=journal_stub + _remote_script(),
        text=True,
        capture_output=True,
        check=False,
        env=os.environ | {"VSERVER_SSH_STATS_MODE": "base"},
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["failed_ssh_logins_15m"] == 2


def test_firewall_status_collector_detects_active_ufw() -> None:
    """Report the active backend and rule count from `ufw status`."""

    ufw_stub = r'''
timeout() { shift; "$@"; }
ufw() {
  cat <<'EOF'
Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
[ 1] 22/tcp                     ALLOW IN    Anywhere
[ 2] 80/tcp                     ALLOW IN    Anywhere
EOF
}
'''
    result = subprocess.run(
        ["bash"],
        input=ufw_stub + _remote_script(),
        text=True,
        capture_output=True,
        check=False,
        env=os.environ | {"VSERVER_SSH_STATS_MODE": "base"},
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["firewall_active"] == 1
    assert data["firewall_backend"] == "ufw"
    assert data["firewall_rules_count"] == 2


def test_firewall_status_collector_allows_default_only_iptables() -> None:
    """Do not abort the collector when iptables has no custom rules."""

    iptables_stub = r'''
timeout() { shift; "$@"; }
command() {
  if [ "$1" = "-v" ]; then
    case "$2" in
      iptables) return 0 ;;
      ufw|firewall-cmd|nft|sudo) return 1 ;;
    esac
  fi
  builtin command "$@"
}
iptables() {
  [ "$1" = "-S" ] || return 23
  cat <<'EOF'
-P INPUT ACCEPT
-P FORWARD ACCEPT
-P OUTPUT ACCEPT
EOF
}
'''
    result = subprocess.run(
        ["bash"],
        input=iptables_stub + _remote_script(),
        text=True,
        capture_output=True,
        check=False,
        env=os.environ | {"VSERVER_SSH_STATS_MODE": "base"},
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["firewall_active"] == 0
    assert data["firewall_backend"] == ""
    assert data["firewall_rules_count"] is None


def test_unattended_upgrades_collector_allows_missing_dnf_timer() -> None:
    """An unavailable dnf timer must not abort the collector under set -e."""

    dnf_timer_stub = r'''
set -e
run_limited() { shift; "$@"; }
[() {
  if builtin test "$1" = "-r" && builtin test "${2:-}" = "/etc/apt/apt.conf.d/20auto-upgrades"; then
    return 1
  fi
  builtin test "$@"
}
systemctl() { return 3; }
'''
    result = subprocess.run(
        ["bash"],
        input=(
            dnf_timer_stub
            + _bash_function("read_unattended_upgrades_status")
            + "\nread_unattended_upgrades_status\nprintf '%s' \"$unattended_upgrades_active\"\n"
        ),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "0"


def test_unattended_upgrades_collector_allows_inactive_apt_timer() -> None:
    """An inactive apt timer must not abort the collector under set -e."""

    apt_timer_stub = r'''
set -e
run_limited() { shift; "$@"; }
[() {
  if builtin test "$1" = "-r" && builtin test "${2:-}" = "/etc/apt/apt.conf.d/20auto-upgrades"; then
    return 0
  fi
  builtin test "$@"
}
grep() { return 0; }
systemctl() { return 3; }
'''
    result = subprocess.run(
        ["bash"],
        input=(
            apt_timer_stub
            + _bash_function("read_unattended_upgrades_status")
            + "\nread_unattended_upgrades_status\nprintf '%s' \"$unattended_upgrades_active\"\n"
        ),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "0"


def test_network_collector_allows_no_eligible_interfaces() -> None:
    """An empty filtered interface list must not abort the collector."""

    network_stub = r'''
set -e
awk_calls=0
awk() {
  awk_calls=$((awk_calls + 1))
  case "$awk_calls" in
    1|2) printf '0\n' ;;
    3) printf 'lo|0|0\n' ;;
  esac
}
'''
    result = subprocess.run(
        ["bash"],
        input=(
            network_stub
            + _bash_function("read_network_bytes")
            + "\nread_network_bytes\nprintf '%s|%s|%s' \"$rx\" \"$tx\" \"$network_interfaces_json\"\n"
        ),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "0|0|[]"


def test_fail2ban_collector_aggregates_banned_ips_across_jails() -> None:
    """Sum "Currently banned" across every reported jail, capped at 5."""

    fail2ban_stub = r'''
timeout() { shift; "$@"; }
fail2ban-client() {
  if [ "$1" = "status" ] && [ -z "$2" ]; then
    cat <<'EOF'
Status
|- Number of jail:      2
`- Jail list:   sshd, recidive
EOF
  elif [ "$1" = "status" ] && [ "$2" = "sshd" ]; then
    cat <<'EOF'
Status for the jail: sshd
`- Actions
   |- Currently banned: 3
   `- Banned IP list:   1.2.3.4 5.6.7.8 9.10.11.12
EOF
  elif [ "$1" = "status" ] && [ "$2" = "recidive" ]; then
    cat <<'EOF'
Status for the jail: recidive
`- Actions
   |- Currently banned: 1
   `- Banned IP list:   1.2.3.4
EOF
  fi
}
'''
    result = subprocess.run(
        ["bash"],
        input=fail2ban_stub + _remote_script(),
        text=True,
        capture_output=True,
        check=False,
        env=os.environ | {"VSERVER_SSH_STATS_MODE": "base"},
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["fail2ban_active"] == 1
    assert data["fail2ban_banned_count"] == 4
    assert data["fail2ban_jails"] == [
        {"jail": "sshd", "banned": 3},
        {"jail": "recidive", "banned": 1},
    ]


def test_fail2ban_collector_defaults_when_not_installed() -> None:
    """Report inactive fail2ban without failing the whole collector."""

    result = subprocess.run(
        ["bash"],
        input=_remote_script(),
        text=True,
        capture_output=True,
        check=False,
        env=os.environ | {"VSERVER_SSH_STATS_MODE": "base"},
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["fail2ban_active"] == 0
    assert data["fail2ban_banned_count"] == 0
    assert data["fail2ban_jails"] == []


def test_process_state_parser_handles_spaces_and_parentheses() -> None:
    """Parse the state after the final command-name parenthesis in proc stat."""

    result = subprocess.run(
        ["bash"],
        input=(
            _bash_function("parse_process_state")
            + "\nparse_process_state '123 (worker ) with spaces) Z 1 2 3'\n"
            + "printf '%s' \"$parsed_process_state\"\n"
        ),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "Z"


def test_storage_collector_returns_a_stable_payload() -> None:
    """The optional slow collector remains valid without storage tools."""

    result = subprocess.run(
        ["bash"],
        input=_remote_script(),
        text=True,
        capture_output=True,
        check=False,
        env=os.environ
        | {
            "PATH": "/usr/bin:/bin",
            "VSERVER_SSH_STATS_MODE": "storage",
            "VSERVER_SSH_STATS_STORAGE_TIMEOUT": "1",
        },
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert {
        "storage_devices",
        "storage_tools_available",
        "storage_stats_complete",
        "storage_stats_partial",
        "storage_devices_seen",
        "storage_devices_collected",
        "storage_device_errors",
        "raid_details",
    }.issubset(data)
    assert isinstance(data["storage_devices"], list)
    assert isinstance(data["raid_details"], list)


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
