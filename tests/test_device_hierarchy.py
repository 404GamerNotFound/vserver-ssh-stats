"""Regression tests for the Home Assistant device hierarchy."""
from __future__ import annotations

import ast
import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).parents[1]
COMPONENT_PATH = ROOT / "custom_components" / "vserver_ssh_stats"


def _load_function(path: Path, name: str, namespace: dict[str, Any]) -> Any:
    tree = ast.parse(path.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"),
        namespace,
    )
    return namespace[name]


def test_container_device_is_grouped_below_host() -> None:
    """Docker devices use a stable identifier and reference the host device."""

    build_container_device_info = _load_function(
        COMPONENT_PATH / "util.py",
        "build_container_device_info",
        {"DeviceInfo": lambda **kwargs: kwargs},
    )

    device_info = build_container_device_info(
        "vserver_ssh_stats",
        {"host": "192.0.2.10", "name": "pi5docker"},
        "grafana",
        "grafana",
    )

    assert device_info == {
        "identifiers": {("vserver_ssh_stats", "192.0.2.10_container_grafana")},
        "name": "pi5docker grafana",
        "manufacturer": "Docker",
        "model": "Container",
        "via_device": ("vserver_ssh_stats", "192.0.2.10"),
    }


def test_all_container_entity_types_use_child_device_info() -> None:
    """Container sensors, switches, and buttons stay on the child device."""

    expected_calls = {
        "sensor.py": "_build_container_sensors",
        "switch.py": "__init__",
        "button.py": "__init__",
    }
    expected_classes = {
        "sensor.py": "ServerContainerRegistry",
        "switch.py": "VServerContainerSwitch",
        "button.py": "VServerContainerRestartButton",
    }

    for filename, method_name in expected_calls.items():
        tree = ast.parse((COMPONENT_PATH / filename).read_text())
        class_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == expected_classes[filename]
        )
        method = next(
            node
            for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == method_name
        )
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "build_container_device_info"
            for node in ast.walk(method)
        )


def test_container_sensors_read_canonical_container_metrics() -> None:
    """Container sensors prefer current lookup metrics over stale flat values."""

    tree = ast.parse((COMPONENT_PATH / "sensor.py").read_text())
    sensor_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "VServerSensor"
    )
    native_value = next(
        node
        for node in sensor_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "native_value"
    )
    helpers = runpy.run_path(str(COMPONENT_PATH / "docker_entities.py"))
    namespace = {
        "_build_health": lambda *_args: {"status": "ok", "score": 100},
        "find_container": helpers["find_container"],
    }
    exec(
        compile(ast.Module(body=[native_value], type_ignores=[]), "<sensor-test>", "exec"),
        namespace,
    )
    fake_sensor = SimpleNamespace(
        entity_description=SimpleNamespace(key="container_grafana_cpu"),
        _container_key="grafana",
        _container_metric="cpu",
        coordinator=SimpleNamespace(
            data={
                "container_grafana_cpu": 0.0,
                "container_lookup": {
                    "grafana": {"name": "grafana", "cpu": 1.25, "mem": 4.5}
                },
            },
            last_update_success=True,
        ),
    )

    assert namespace["native_value"].fget(fake_sensor) == 1.25
