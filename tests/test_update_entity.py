"""Tests for release parsing and update entity setup compatibility."""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "vserver_ssh_stats"


def _parse_latest_version():
    """Load the pure tag-parsing helper without importing Home Assistant."""

    tree = ast.parse((INTEGRATION / "update.py").read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_parse_latest_version"
    )
    namespace: dict[str, Any] = {"Any": Any}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "<update-helper>", "exec"),
        namespace,
    )
    return namespace["_parse_latest_version"]


def test_strips_leading_v_from_tag_name() -> None:
    """GitHub tags are typically prefixed with v; the stored version is bare."""

    parse = _parse_latest_version()

    assert parse("v1.4.51") == "1.4.51"
    assert parse("1.4.51") == "1.4.51"


def test_blank_or_missing_tag_returns_none() -> None:
    """A missing release payload should not report a false version."""

    parse = _parse_latest_version()

    assert parse("") is None
    assert parse(None) is None
    assert parse("   ") is None


class StrictVersion:
    """Model AwesomeVersion's string conversion and rejection of sentinel comparisons."""

    def __str__(self) -> str:
        return "1.5.10"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, (str, StrictVersion)):
            raise ValueError("Not a valid AwesomeVersion object")
        return str(self) == str(other)


def _update_setup_namespace(version):
    """Load the setup and entity with HA stand-ins, including its cached-attribute setter."""
    sentinel = object()

    class UpdateEntity:
        @property
        def _attr_installed_version(self):
            return self.__dict__["__attr_installed_version"]

        @_attr_installed_version.setter
        def _attr_installed_version(self, value):
            # Preserve HA's comparison order: sentinel == AwesomeVersion must fail.
            old = self.__dict__.get("__attr_installed_version", sentinel)
            if old == value and type(old) is type(value):
                return
            self.__dict__["__attr_installed_version"] = value

        @property
        def installed_version(self):
            return self._attr_installed_version

    class CoordinatorEntity:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, coordinator):
            self.coordinator = coordinator

    coordinator = SimpleNamespace(data=None, async_refresh=AsyncMock())
    namespace = {
        "CoordinatorEntity": CoordinatorEntity,
        "UpdateEntity": UpdateEntity,
        "VServerReleaseCoordinator": lambda hass: coordinator,
        "DeviceInfo": dict,
        "DeviceEntryType": SimpleNamespace(SERVICE="service"),
        "DOMAIN": "vserver_ssh_stats",
        "async_get_integration": AsyncMock(return_value=SimpleNamespace(version=version)),
    }
    tree = ast.parse((INTEGRATION / "update.py").read_text())
    nodes = [
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        *[
            node for node in tree.body
            if getattr(node, "name", None) in {
                "VServerIntegrationUpdateEntity", "async_setup_entry"
            }
        ],
    ]
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                "<update-setup>", "exec"),
        namespace,
    )
    return namespace, coordinator


@pytest.mark.parametrize(
    ("version", "expected"),
    [(StrictVersion(), "1.5.10"), ("1.5.10", "1.5.10"), (None, None)],
    ids=["version-object", "legacy-string", "missing-version"],
)
def test_update_setup_normalizes_loader_version_before_entity_assignment(version, expected) -> None:
    """New version objects, legacy strings and absent versions all permit setup."""
    namespace, coordinator = _update_setup_namespace(version)
    entities = []
    asyncio.run(namespace["async_setup_entry"](
        object(), SimpleNamespace(entry_id="test-entry"), entities.extend
    ))
    coordinator.async_refresh.assert_awaited_once()
    assert len(entities) == 1
    entity = entities[0]
    assert entity.installed_version == expected
    assert entity.installed_version is None or type(entity.installed_version) is str
    # An unavailable release check falls back to the normalized installed version.
    assert entity.latest_version == expected
    json.dumps({"installed_version": entity.installed_version})
    coordinator.data = {"latest_version": "1.5.11"}
    assert entity.latest_version == "1.5.11"
