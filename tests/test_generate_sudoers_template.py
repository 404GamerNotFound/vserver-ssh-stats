"""Tests for the sudoers template generator script."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "generate_sudoers_template.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def test_generates_rules_for_selected_features() -> None:
    """Only requested features produce NOPASSWD rules, scoped to that user."""

    result = _run(
        "--user",
        "vserver-monitor",
        "--package-manager",
        "apt",
        "--reboot",
        "--storage-health",
        "--firewall-status",
        "--service",
        "nginx",
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert all(
        line.startswith(("#", "Defaults:vserver-monitor", "vserver-monitor ALL=(root) NOPASSWD:"))
        for line in lines
    )
    assert "vserver-monitor ALL=(root) NOPASSWD: /usr/bin/apt-get -y upgrade" in result.stdout
    assert "vserver-monitor ALL=(root) NOPASSWD: /sbin/reboot" in result.stdout
    assert "vserver-monitor ALL=(root) NOPASSWD: /usr/sbin/smartctl -a /dev/*" in result.stdout
    assert "vserver-monitor ALL=(root) NOPASSWD: /usr/sbin/ufw status verbose" in result.stdout
    assert "vserver-monitor ALL=(root) NOPASSWD: /usr/bin/systemctl restart nginx" in result.stdout
    assert "dnf" not in result.stdout
    assert "docker" not in result.stdout


def test_requires_at_least_one_feature() -> None:
    """Fail loudly instead of writing an empty, useless sudoers file."""

    result = _run("--user", "vserver-monitor")

    assert result.returncode == 1
    assert "No features selected" in result.stderr


def test_service_flag_is_repeatable() -> None:
    """Each --service adds its own narrowly scoped restart rule."""

    result = _run("--user", "vserver-monitor", "--service", "nginx", "--service", "postgresql")

    assert result.returncode == 0, result.stderr
    assert "systemctl restart nginx" in result.stdout
    assert "systemctl restart postgresql" in result.stdout
