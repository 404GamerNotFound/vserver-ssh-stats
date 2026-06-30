"""Regression tests for the per-server recorder purge button."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).parents[1]
BUTTON_PATH = ROOT / "custom_components" / "vserver_ssh_stats" / "button.py"


def _load_node(name: str, namespace: dict[str, Any]) -> Any:
    tree = ast.parse(BUTTON_PATH.read_text())
    node = next(
        candidate
        for candidate in tree.body
        if isinstance(candidate, (ast.FunctionDef, ast.ClassDef)) and candidate.name == name
    )
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), str(BUTTON_PATH), "exec"),
        namespace,
    )
    return namespace[name]


class FakeDeviceRegistry:
    """Small device registry double with identifier lookup."""

    def __init__(self, devices: list[SimpleNamespace]) -> None:
        self.devices = {device.id: device for device in devices}

    def async_get_device(self, *, identifiers):
        return next(
            (device for device in self.devices.values() if device.identifiers == identifiers),
            None,
        )


def _device(
    device_id: str,
    identifier: str,
    via_device_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=device_id,
        identifiers={("vserver_ssh_stats", identifier)},
        via_device_id=via_device_id,
    )


def _registry_entry(
    entity_id: str,
    device_id: str,
    config_entry_id: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        entity_id=entity_id,
        device_id=device_id,
        config_entry_id=config_entry_id,
    )


def test_entity_ids_are_limited_to_server_hierarchy_and_config_entry() -> None:
    """The purge selection includes descendants without crossing ownership boundaries."""

    entity_ids_for_server = _load_node(
        "_entity_ids_for_server",
        {"DOMAIN": "vserver_ssh_stats"},
    )
    device_registry = FakeDeviceRegistry(
        [
            _device("host", "192.0.2.10"),
            _device("container", "192.0.2.10_container_app", "host"),
            _device("nested", "192.0.2.10_nested", "container"),
            _device("other-host", "192.0.2.20"),
        ]
    )
    entries = [
        _registry_entry("sensor.server_cpu", "host", "entry-1"),
        _registry_entry("sensor.server_container_cpu", "container", "entry-1"),
        _registry_entry("sensor.server_nested", "nested", "entry-1"),
        _registry_entry("sensor.foreign_on_same_device", "host", "entry-2"),
        _registry_entry("sensor.other_server_cpu", "other-host", "entry-1"),
    ]
    entity_registry = SimpleNamespace(entities={entry.entity_id: entry for entry in entries})

    assert entity_ids_for_server(
        device_registry,
        entity_registry,
        "192.0.2.10",
        "entry-1",
    ) == [
        "sensor.server_container_cpu",
        "sensor.server_cpu",
        "sensor.server_nested",
    ]


def test_button_calls_recorder_for_all_selected_entities_with_user_context() -> None:
    """Pressing the button requests a full entity purge with the caller's context."""

    namespace: dict[str, Any] = {
        "Any": Any,
        "ButtonEntity": object,
        "Dict": dict,
        "DOMAIN": "vserver_ssh_stats",
        "EntityCategory": SimpleNamespace(CONFIG="config"),
        "HomeAssistant": object,
        "HomeAssistantError": RuntimeError,
        "build_device_info": lambda domain, server: {"identifiers": {(domain, server["host"])}},
    }
    namespace["_entity_ids_for_server"] = _load_node(
        "_entity_ids_for_server",
        {"DOMAIN": "vserver_ssh_stats"},
    )
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class FakeServices:
        def has_service(self, domain: str, service: str) -> bool:
            return (domain, service) == ("recorder", "purge_entities")

        async def async_call(self, *args, **kwargs) -> None:
            calls.append((args, kwargs))

    device_registry = FakeDeviceRegistry([_device("host", "192.0.2.10")])
    entry = _registry_entry("sensor.server_cpu", "host", "entry-1")
    entity_registry = SimpleNamespace(entities={entry.entity_id: entry})
    namespace["dr"] = SimpleNamespace(async_get=lambda hass: hass.device_registry)
    namespace["er"] = SimpleNamespace(async_get=lambda hass: hass.entity_registry)
    button_class = _load_node("VServerPurgeHistoryButton", namespace)
    hass = SimpleNamespace(
        services=FakeServices(),
        device_registry=device_registry,
        entity_registry=entity_registry,
    )
    button = button_class(hass, {"host": "192.0.2.10", "name": "Server"}, "entry-1")
    context = object()
    button._context = context

    asyncio.run(button.async_press())

    assert calls == [
        (
            (
                "recorder",
                "purge_entities",
                {"entity_id": ["sensor.server_cpu"], "keep_days": 0},
            ),
            {"blocking": True, "context": context},
        )
    ]
    assert button._attr_unique_id == "192.0.2.10_purge_history"


def test_retention_button_calls_integration_service_with_configured_keep_days() -> None:
    """Pressing the retention button delegates to the integration purge service."""

    namespace: dict[str, Any] = {
        "Any": Any,
        "ButtonEntity": object,
        "Dict": dict,
        "DEFAULT_HISTORY_RETENTION_DAYS": 10,
        "DOMAIN": "vserver_ssh_stats",
        "EntityCategory": SimpleNamespace(CONFIG="config"),
        "HomeAssistant": object,
        "build_device_info": lambda domain, server: {"identifiers": {(domain, server["host"])}},
    }
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class FakeServices:
        async def async_call(self, *args, **kwargs) -> None:
            calls.append((args, kwargs))

    button_class = _load_node("VServerPurgeHistoryKeepDaysButton", namespace)
    hass = SimpleNamespace(services=FakeServices())
    button = button_class(
        hass,
        {
            "host": "192.0.2.10",
            "name": "Server",
            "history_retention_days": 21,
        },
    )
    context = object()
    button._context = context

    asyncio.run(button.async_press())

    assert calls == [
        (
            (
                "vserver_ssh_stats",
                "purge_history_keep_days",
                {"host": "192.0.2.10", "keep_days": 21},
            ),
            {"blocking": True, "context": context},
        )
    ]
    assert button._attr_unique_id == "192.0.2.10_purge_history_keep_days"
