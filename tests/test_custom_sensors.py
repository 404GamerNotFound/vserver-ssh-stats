"""Tests for scheduled custom command sensors."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "vserver_ssh_stats"


def _function_from_class(path: Path, class_name: str, function_name: str):
    """Compile one class method without importing Home Assistant."""

    tree = ast.parse(path.read_text())
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    function = next(
        node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    function.decorator_list = []
    namespace = {
        "Any": Any,
        "re": re,
        "MAX_SENSOR_STATE_LENGTH": 255,
        "MIN_CUSTOM_SENSOR_INTERVAL": 5,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[function_name]


def _channel_reader():
    """Compile the bounded Paramiko channel reader in isolation."""

    path = INTEGRATION / "ssh_collector.py"
    tree = ast.parse(path.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_read_custom_command_channel"
    )
    namespace = {"Any": Any, "MAX_CUSTOM_COMMAND_OUTPUT": 16 * 1024}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["_read_custom_command_channel"], namespace


def _custom_command_runner(client):
    """Compile the custom command runner with a fake Paramiko client."""

    path = INTEGRATION / "ssh_collector.py"
    tree = ast.parse(path.read_text())
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_read_custom_command_channel", "_run_custom_command"}
    ]
    namespace = {
        "Any": Any,
        "Dict": dict,
        "Optional": Any,
        "MAX_CUSTOM_COMMAND_OUTPUT": 16 * 1024,
        "paramiko": SimpleNamespace(SSHClient=lambda: client),
        "configure_pinned_host_keys": lambda _client, _fingerprints: None,
        "time": SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda _delay: None),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["_run_custom_command"]


class FakeChannel:
    """Small Paramiko channel stand-in for output and timeout tests."""

    def __init__(
        self,
        stdout: list[bytes] | None = None,
        stderr: list[bytes] | None = None,
        status: int = 0,
        exited: bool = True,
    ) -> None:
        self.stdout = list(stdout or [])
        self.stderr = list(stderr or [])
        self.status = status
        self.exited = exited
        self.closed = False

    def recv_ready(self) -> bool:
        return bool(self.stdout)

    def recv(self, _size: int) -> bytes:
        return self.stdout.pop(0)

    def recv_stderr_ready(self) -> bool:
        return bool(self.stderr)

    def recv_stderr(self, _size: int) -> bytes:
        return self.stderr.pop(0)

    def exit_status_ready(self) -> bool:
        return self.exited

    def recv_exit_status(self) -> int:
        return self.status

    def close(self) -> None:
        self.closed = True


def test_custom_sensor_converts_numeric_output_and_bounds_text_state() -> None:
    """Numeric output is recorder-friendly while long text remains a valid HA state."""

    native_value = _function_from_class(
        INTEGRATION / "sensor.py", "VServerCustomCommandSensor", "native_value"
    )
    sensor = SimpleNamespace(coordinator=SimpleNamespace(data={"output": "632"}))
    assert native_value(sensor) == 632

    sensor.coordinator.data = {"output": "1.25e2"}
    assert native_value(sensor) == 125.0

    sensor.coordinator.data = {"output": "alice 192.0.2.1\nbob 192.0.2.2"}
    assert native_value(sensor) == "alice 192.0.2.1\nbob 192.0.2.2"

    sensor.coordinator.data = {"output": "x" * 300}
    assert native_value(sensor) == "x" * 252 + "..."


def test_custom_command_reader_drains_both_streams_and_limits_output() -> None:
    """A noisy stderr cannot block stdout and retained data remains bounded."""

    read_channel, namespace = _channel_reader()
    namespace["time"] = SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda _delay: None)
    channel = FakeChannel(
        stdout=[b"value\n", b"x" * (20 * 1024)],
        stderr=[b"warning\n"],
        status=0,
    )

    stdout, stderr, status, truncated = read_channel(channel, 30)

    assert stdout.startswith("value\n")
    assert len(stdout.encode()) == 16 * 1024
    assert stderr == "warning\n"
    assert status == 0
    assert truncated is True


def test_custom_command_reader_enforces_timeout() -> None:
    """Commands without output or an exit status are closed at their deadline."""

    read_channel, namespace = _channel_reader()
    ticks = iter([0.0, 2.0])
    namespace["time"] = SimpleNamespace(
        monotonic=lambda: next(ticks),
        sleep=lambda _delay: None,
    )
    channel = FakeChannel(exited=False)

    with pytest.raises(TimeoutError, match="timed out after 1 seconds"):
        read_channel(channel, 1)
    assert channel.closed is True


def test_custom_command_rejects_nonzero_exit_status() -> None:
    """Output from a failed shell command must never become a successful sensor value."""

    channel = FakeChannel(stdout=[b"partial output"], stderr=[b"permission denied"], status=7)
    client = SimpleNamespace(
        connect=lambda **_kwargs: None,
        exec_command=lambda _command, timeout: (
            None,
            SimpleNamespace(channel=channel),
            None,
        ),
        close=lambda: None,
    )
    run_command = _custom_command_runner(client)

    with pytest.raises(RuntimeError, match="permission denied"):
        run_command(
            "server",
            "user",
            None,
            None,
            22,
            "false",
            10,
            30,
            ["SHA256:test"],
        )


def test_custom_sensor_config_preserves_id_and_rejects_duplicate_name() -> None:
    """Editing keeps entity identity and names are unique per server."""

    normalize = _function_from_class(
        INTEGRATION / "config_flow.py", "OptionsFlowHandler", "_custom_sensor_from_input"
    )
    current = {
        "id": "stable-id",
        "name": "Lifetime writes",
        "server_host": "pi.local",
        "command": "old",
        "interval": 86400,
        "timeout": 30,
    }
    flow = SimpleNamespace(
        _existing_servers=[{"host": "pi.local"}],
        _custom_sensors=[current],
    )
    errors: dict[str, str] = {}
    updated = normalize(
        flow,
        {
            "name": "Lifetime writes",
            "server_host": "pi.local",
            "command": "tune2fs -l /dev/mmcblk0p2",
            "interval": 604800,
            "timeout": 60,
        },
        errors,
        existing=current,
        ignore_index=0,
    )
    assert errors == {}
    assert updated["id"] == "stable-id"

    flow._custom_sensors.append({**current, "id": "second-id", "name": "SSH logins"})
    duplicate_errors: dict[str, str] = {}
    assert (
        normalize(
            flow,
            {
                "name": "ssh LOGINs",
                "server_host": "pi.local",
                "command": "journalctl",
                "interval": 3600,
                "timeout": 30,
            },
            duplicate_errors,
        )
        is None
    )
    assert duplicate_errors == {"name": "duplicate_custom_sensor"}


def test_custom_sensor_form_uses_strip_as_validator() -> None:
    """Build the form with the current callable-style Voluptuous Strip API."""

    class FakeVol:
        UNDEFINED = object()

        @staticmethod
        def Strip(value):
            return value.strip()

        @staticmethod
        def Required(key, *, default):
            return key, default

        @staticmethod
        def Length(*, min, max):
            return min, max

        @staticmethod
        def All(*validators):
            return validators

        @staticmethod
        def Schema(schema):
            return schema

    build_schema = _function_from_class(
        INTEGRATION / "config_flow.py",
        "OptionsFlowHandler",
        "_custom_sensor_form_schema",
    )
    build_schema.__globals__.update(
        {
            "vol": FakeVol,
            "DEFAULT_CUSTOM_SENSOR_INTERVAL": 300,
            "MIN_CUSTOM_SENSOR_INTERVAL": 5,
            "DEFAULT_CUSTOM_SENSOR_TIMEOUT": 30,
            "_number_box": lambda **kwargs: kwargs,
        }
    )
    flow = SimpleNamespace(
        _existing_servers=[{"host": "server.example"}],
        _server_host_select_selector=lambda: "server-selector",
    )

    schema = build_schema(flow)

    name_validators = next(
        validators
        for key, validators in schema.items()
        if isinstance(key, tuple) and key[0] == "name"
    )
    command_validators = next(
        validators
        for key, validators in schema.items()
        if isinstance(key, tuple) and key[0] == "command"
    )
    assert FakeVol.Strip in name_validators
    assert FakeVol.Strip in command_validators
    assert FakeVol.Strip("  uptime  ") == "uptime"


def test_custom_sensor_translation_keys_match_strings() -> None:
    """All shipped languages expose the new options-flow fields and errors."""

    reference = json.loads((INTEGRATION / "strings.json").read_text())["options"]
    expected_steps = {
        "select_custom_sensor",
        "add_custom_sensor",
        "edit_custom_sensor",
        "remove_custom_sensor",
    }
    expected_errors = {"no_custom_sensors", "duplicate_custom_sensor"}
    for path in sorted((INTEGRATION / "translations").glob("*.json")):
        options = json.loads(path.read_text())["options"]
        assert expected_steps <= options["step"].keys(), path
        assert expected_errors <= options["error"].keys(), path
    assert expected_steps <= reference["step"].keys()
    assert expected_errors <= reference["error"].keys()
