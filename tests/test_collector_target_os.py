"""Regression tests for collector OS selection and useful failure reporting."""
from __future__ import annotations

import ast
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

COLLECTOR = Path(__file__).parents[1] / "custom_components/vserver_ssh_stats/ssh_collector.py"


class AuthenticationError(Exception):
    """Stand-in for Paramiko's authentication exception."""


class HostKeyError(Exception):
    """Stand-in for the integration's host-key exception."""


def _namespace(run_ssh):
    """Load collector functions without importing HA or requiring Paramiko."""
    tree = ast.parse(COLLECTOR.read_text())
    names = {"_build_collection_commands", "_parse_json_output", "_async_collect_raw"}
    body = [
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        *[node for node in tree.body if getattr(node, "name", None) in names],
    ]
    namespace = {
        "asyncio": asyncio,
        "json": json,
        "_LOGGER": logging.getLogger(__name__),
        "REMOTE_SCRIPT": "linux collector payload",
        "WINDOWS_REMOTE_SCRIPT": "powershell.exe -Command collector",
        "_run_ssh": run_ssh,
        "paramiko": SimpleNamespace(AuthenticationException=AuthenticationError),
        "SSHHostKeyError": HostKeyError,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])),
                 str(COLLECTOR), "exec"), namespace)
    return namespace


def _collect(namespace, target_os="auto"):
    return asyncio.run(namespace["_async_collect_raw"](
        "cinema", "root", "password", None, 22, target_os, 10, 45, "base",
    ))


@pytest.mark.parametrize("target_os", ["debian", "raspbian", " Debian "])
@pytest.mark.parametrize("mode", ["base", "packages", "docker", "storage"])
def test_explicit_linux_profiles_only_build_linux_commands(target_os, mode):
    commands = _namespace(Mock())["_build_collection_commands"](target_os, mode)
    assert len(commands) == 2
    assert all(payload == "linux collector payload" for _, payload in commands)
    assert all("powershell" not in command for command, _ in commands)
    assert commands[0][0].endswith(" sh -s")
    assert commands[1][0].endswith(" /bin/sh -s")


@pytest.mark.parametrize("mode", ["base", "packages", "docker", "storage"])
def test_explicit_windows_never_builds_linux_fallbacks(mode):
    commands = _namespace(Mock())["_build_collection_commands"]("windows", mode)
    assert commands == ([("powershell.exe -Command collector", None)] if mode == "base" else [])


def test_linux_failure_reports_both_linux_errors_without_trying_windows():
    runner = Mock(side_effect=[
        RuntimeError("sh: not found"),
        RuntimeError("grep: unrecognized option -- P"),
        RuntimeError("powershell.exe: not found"),
    ])
    data, _, error = _collect(_namespace(runner), "debian")
    assert data is None
    assert runner.call_count == 2
    assert "sh: not found" in str(error)
    assert "grep: unrecognized option -- P" in str(error)
    assert "powershell" not in str(error)


def test_auto_failure_keeps_linux_errors_alongside_windows_error():
    runner = Mock(side_effect=[
        RuntimeError("sh: not found"),
        RuntimeError("collector: unsupported tool option"),
        RuntimeError("powershell.exe: not found"),
    ])
    data, _, error = _collect(_namespace(runner))
    assert data is None
    assert runner.call_count == 3
    assert "sh: not found" in str(error)
    assert "collector: unsupported tool option" in str(error)
    assert "powershell.exe: not found" in str(error)
    assert "Linux" in str(error)
    assert "Windows" in str(error)


@pytest.mark.parametrize(("target_os", "results", "attempts"), [
    ("auto", [RuntimeError("sh missing"), RuntimeError("sh missing"),
              ('{"os":"Windows"}', {"collection_time_ms": 5})], 3),
    ("debian", [RuntimeError("sh missing from PATH"),
                ('{"os":"LibreELEC"}', {"collection_time_ms": 5})], 2),
    ("debian", [('{"os":"LibreELEC"}', {"collection_time_ms": 5})], 1),
])
def test_successful_fallback_clears_previous_errors(target_os, results, attempts):
    runner = Mock(side_effect=results)
    data, timing, error = _collect(_namespace(runner), target_os)
    assert data["os"] in {"Windows", "LibreELEC"}
    assert timing == {"collection_time_ms": 5}
    assert error is None
    assert runner.call_count == attempts


@pytest.mark.parametrize("error_type", [AuthenticationError, HostKeyError])
def test_authentication_failures_preserve_type_and_stop_fallback(error_type):
    original = error_type("SSH identity rejected")
    runner = Mock(side_effect=original)
    data, _, error = _collect(_namespace(runner))
    assert data is None
    assert error is original
    assert runner.call_count == 1


def test_explicit_windows_preserves_original_failure():
    original = RuntimeError("PowerShell collection failed")
    runner = Mock(side_effect=original)
    data, _, error = _collect(_namespace(runner), "windows")
    assert data is None
    assert error is original
    assert runner.call_count == 1
