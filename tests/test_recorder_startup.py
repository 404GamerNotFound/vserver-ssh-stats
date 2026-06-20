"""Regression tests for recorder-friendly startup and entity metadata."""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "vserver_ssh_stats"


def _load_function(path: Path, name: str, namespace: dict[str, Any]) -> Any:
    tree = ast.parse(path.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"),
        namespace,
    )
    return namespace[name]


def _unrecorded_attributes(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text())
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    assignment = next(
        node
        for node in class_node.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_unrecorded_attributes"
            for target in node.targets
        )
    )
    assert isinstance(assignment.value, ast.Call)
    return set(ast.literal_eval(assignment.value.args[0]))


def test_initial_refresh_waits_until_home_assistant_started() -> None:
    """Remote collectors must not compete with recorder startup or migrations."""

    captured: dict[str, Any] = {}
    unsubscribe = object()

    def fake_async_at_started(hass, callback):
        captured["hass"] = hass
        captured["callback"] = callback
        return unsubscribe

    schedule_initial_refresh = _load_function(
        INTEGRATION / "coordinator.py",
        "_schedule_initial_refresh",
        {
            "HomeAssistant": object,
            "ConfigEntry": object,
            "DataUpdateCoordinator": object,
            "async_at_started": fake_async_at_started,
            "asyncio": asyncio,
        },
    )
    refreshes: list[str] = []

    class FakeCoordinator:
        def __init__(self, name: str) -> None:
            self.name = name

        async def async_request_refresh(self) -> None:
            refreshes.append(self.name)

    unload_callbacks: list[Any] = []
    entry = SimpleNamespace(async_on_unload=unload_callbacks.append)
    hass = object()

    schedule_initial_refresh(
        hass,
        entry,
        [FakeCoordinator("host"), FakeCoordinator("custom")],
    )

    assert refreshes == []
    assert captured["hass"] is hass
    assert unload_callbacks == [unsubscribe]

    asyncio.run(captured["callback"](hass))

    assert refreshes == ["host", "custom"]


def test_coordinator_factories_use_deferred_initial_refresh() -> None:
    """Both regular and custom coordinators use the startup gate."""

    source = (INTEGRATION / "coordinator.py").read_text()

    assert source.count("_schedule_initial_refresh(hass, entry, coordinators)") == 2
    assert "hass.async_create_task(coordinator.async_request_refresh())" not in source


def test_volatile_live_attributes_are_not_persisted() -> None:
    """High-churn details remain live without expanding recorder history."""

    expected = {
        ("sensor.py", "VServerSensor"): {"processes", "containers", "arrays"},
        ("sensor.py", "VServerActionStatusSensor"): {"output"},
        ("sensor.py", "VServerCustomCommandSensor"): {
            "output",
            "last_updated",
            "collection_time_ms",
        },
        ("binary_sensor.py", "VServerOnlineBinarySensor"): {"last_seen"},
        ("binary_sensor.py", "VServerPortBinarySensor"): {"response_time_ms"},
        ("binary_sensor.py", "VServerContainerMemoryLimitBinarySensor"): {
            "memory_usage_bytes",
            "memory_limit_usage",
        },
        ("switch.py", "VServerContainerSwitch"): {"status", "restart_count"},
    }

    for (filename, class_name), attributes in expected.items():
        assert attributes <= _unrecorded_attributes(INTEGRATION / filename, class_name)
