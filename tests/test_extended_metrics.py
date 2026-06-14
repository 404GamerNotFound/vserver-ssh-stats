"""Tests for process, storage, network, and RAID metric processing."""
from __future__ import annotations

import ast
import asyncio
import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "vserver_ssh_stats"


def _storage_processor():
    """Load storage normalization helpers without Home Assistant imports."""

    tree = ast.parse((INTEGRATION / "ssh_collector.py").read_text())
    wanted = {
        "_sanitize",
        "_safe_int",
        "_safe_float",
        "_safe_list",
        "_process_storage_data",
    }
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {"Any": Any, "Dict": Dict, "Optional": Optional}
    exec(
        compile(ast.Module(body=functions, type_ignores=[]), "<storage-processor>", "exec"),
        namespace,
    )
    return namespace["_process_storage_data"]


def test_storage_devices_and_smart_failure_are_normalized() -> None:
    """Create stable lookup keys and an aggregate SMART failure flag."""

    result = _storage_processor()(
        {
            "storage_tools_available": 1,
            "storage_devices": [
                {
                    "name": "nvme0n1",
                    "path": "/dev/nvme0n1",
                    "model": "Example NVMe",
                    "serial": "SN-1234",
                    "smart_status": "failed",
                    "temperature": "42.5",
                    "wear_percent": "7",
                    "media_errors": "2",
                }
            ],
            "storage_stats_partial": 1,
            "storage_devices_seen": 2,
            "storage_devices_collected": 1,
            "storage_device_errors": 1,
            "raid_details": [{"name": "md0", "failed_devices": 1}],
        }
    )

    device = result["storage_device_lookup"]["sn_1234"]
    assert device["temperature"] == 42.5
    assert device["wear_percent"] == 7.0
    assert device["media_errors"] == 2
    assert result["smart_failed_devices"] == 1
    assert result["smart_failure_detected"] is True
    assert result["storage_stats_partial"] is True
    assert result["storage_devices_seen"] == 2
    assert result["storage_devices_collected"] == 1
    assert result["storage_device_errors"] == 1
    assert result["raid_detail_arrays"][0]["name"] == "md0"


def test_unavailable_storage_health_does_not_report_false_safety() -> None:
    """Return an unknown SMART warning when disks exist but tools are missing."""

    result = _storage_processor()(
        {
            "storage_tools_available": 0,
            "storage_stats_partial": 0,
            "storage_devices_seen": 1,
            "storage_devices_collected": 0,
            "storage_device_errors": 0,
            "storage_devices": [],
            "raid_details": [],
        }
    )

    assert result["smart_failure_detected"] is None


def test_process_peak_cache_resets_when_uptime_decreases() -> None:
    """The observed process peak belongs to one boot only."""

    module = runpy.run_path(str(INTEGRATION / "net_cache.py"))
    cache = module["ProcessPeakCache"]()

    assert cache.compute("host", 100, 1000) == 100
    assert cache.compute("host", 80, 1010) == 100
    assert cache.compute("host", 120, 1020) == 120
    assert cache.compute("host", 40, 10) == 40


def test_storage_collector_caps_commands_and_reports_partial_reads() -> None:
    """Bound individual privileged reads and preserve successful partial data."""

    tree = ast.parse((INTEGRATION / "ssh_collector.py").read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_sample_storage"
    )
    captured: dict[str, Any] = {}

    async def fake_collect(*args, **kwargs):
        captured["storage_timeout"] = kwargs["storage_timeout"]
        return (
            {
                "storage_stats_complete": 1,
                "storage_stats_partial": 1,
                "storage_tools_available": 1,
                "storage_devices_seen": 2,
                "storage_devices_collected": 1,
                "storage_device_errors": 1,
                "storage_devices": [],
                "raid_details": [],
            },
            {"collection_time_ms": 12.5},
            None,
        )

    namespace = {
        "Any": Any,
        "Dict": Dict,
        "Optional": Optional,
        "DEFAULT_CONNECT_TIMEOUT": 10,
        "DEFAULT_COMMAND_TIMEOUT": 45,
        "_async_collect_raw": fake_collect,
        "_safe_int": lambda value: int(value) if value is not None else None,
        "_process_storage_data": _storage_processor(),
    }
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "<storage-sample>", "exec"),
        namespace,
    )

    result = asyncio.run(
        namespace["async_sample_storage"](
            "host",
            "user",
            None,
            None,
            22,
            command_timeout=180,
        )
    )

    assert captured["storage_timeout"] == 20
    assert result["storage_collection_error"] == (
        "Storage health collection was partial: 1 of 2 devices could not be read"
    )


def test_new_warning_binary_sensors_are_registered() -> None:
    """Expose all requested host-level warning conditions."""

    source = (INTEGRATION / "binary_sensor.py").read_text()
    for key in (
        "zombie_processes_detected",
        "software_raid_degraded",
        "software_raid_rebuild_active",
        "conntrack_near_capacity",
        "smart_failure_detected",
        "VServerContainerMemoryLimitBinarySensor",
    ):
        assert key in source


def test_diagnostic_binary_sensor_preserves_unknown_values() -> None:
    """An unavailable metric must not be presented as a cleared warning."""

    tree = ast.parse((INTEGRATION / "binary_sensor.py").read_text())
    sensor_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "VServerDiagnosticBinarySensor"
    )
    is_on = next(
        node
        for node in sensor_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "is_on"
    )
    namespace: dict[str, Any] = {}
    exec(
        compile(ast.Module(body=[is_on], type_ignores=[]), "<binary-sensor>", "exec"),
        namespace,
    )
    fake_sensor = SimpleNamespace(
        _key="smart_failure_detected",
        coordinator=SimpleNamespace(data={"smart_failure_detected": None}),
    )

    assert namespace["is_on"].fget(fake_sensor) is None
