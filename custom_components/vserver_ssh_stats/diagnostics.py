"""Diagnostics support for VServer SSH Stats."""
from __future__ import annotations

import json
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import DOMAIN

TO_REDACT = {"host", "username", "password", "key"}


def _load_servers(entry: ConfigEntry) -> list[dict[str, Any]]:
    """Safely load server definitions from a config entry."""

    try:
        return json.loads(entry.data.get("servers_json", "[]"))
    except ValueError:
        return []


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    servers = _load_servers(config_entry)
    redacted_servers = [async_redact_data(server, TO_REDACT) for server in servers]
    try:
        custom_sensors = json.loads(config_entry.data.get("custom_sensors_json", "[]"))
    except ValueError:
        custom_sensors = []
    return {
        "entry": {
            "title": config_entry.title,
            "entry_id": config_entry.entry_id,
            "unique_id": config_entry.unique_id,
            "interval": config_entry.data.get("interval"),
            "connect_timeout": config_entry.data.get("connect_timeout"),
            "command_timeout": config_entry.data.get("command_timeout"),
            "package_interval": config_entry.data.get("package_interval"),
            "docker_interval": config_entry.data.get("docker_interval"),
            "storage_interval": config_entry.data.get("storage_interval"),
            "slow_command_timeout": config_entry.data.get("slow_command_timeout"),
            "command_allowlist_configured": bool(config_entry.data.get("command_allowlist")),
            "custom_sensor_count": len(custom_sensors) if isinstance(custom_sensors, list) else 0,
        },
        "servers": redacted_servers,
        "options": config_entry.options,
        "domain": DOMAIN,
    }
