"""Utility helpers for the VServer SSH Stats integration."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

try:
    from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
except ImportError:  # pragma: no cover - compatibility with older Home Assistant versions
    CONNECTION_NETWORK_MAC = "mac"

DEFAULT_INTERVAL = 30
DEFAULT_CONNECT_TIMEOUT = 10
DEFAULT_COMMAND_TIMEOUT = 15
DEFAULT_ACTION_COMMAND_TIMEOUT = 300
DEFAULT_COMMAND_ALLOWLIST = ""
DEFAULT_BACKOFF_FAILURE_THRESHOLD = 3
DEFAULT_BACKOFF_MAX_INTERVAL = 300

MAC_PATTERN = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def parse_command_allowlist(value: object) -> list[str]:
    """Return normalized run-command allowlist rules.

    Empty rules mean no allowlist is configured. Rules ending in ``*`` match
    command prefixes; all other rules require an exact command match.
    """

    if not isinstance(value, str):
        return []
    return [line.strip() for line in value.splitlines() if line.strip()]


def is_command_allowed(command: str, rules: list[str]) -> bool:
    """Return whether *command* is allowed by the optional allowlist."""

    if not rules:
        return True

    normalized = command.strip()
    for rule in rules:
        if rule.endswith("*") and normalized.startswith(rule[:-1].strip()):
            return True
        if normalized == rule:
            return True
    return False


def normalize_mac_address(value: object) -> str | None:
    """Return a normalized MAC address or None."""

    if not isinstance(value, str):
        return None
    mac = value.strip().lower().replace("-", ":")
    if not MAC_PATTERN.match(mac) or mac == "00:00:00:00:00:00":
        return None
    return mac


def normalize_mac_addresses(value: object) -> list[str]:
    """Return a de-duplicated list of normalized MAC addresses."""

    raw_values = value if isinstance(value, list) else [value]
    addresses: list[str] = []
    for raw_value in raw_values:
        mac = normalize_mac_address(raw_value)
        if mac and mac not in addresses:
            addresses.append(mac)
    return addresses


def build_device_info(domain: str, server: dict) -> DeviceInfo:
    """Return device info with optional MAC connections for registry merging."""

    host = server["host"]
    mac_addresses = normalize_mac_addresses(server.get("mac_addresses"))
    if mac_addresses:
        return DeviceInfo(
            connections={(CONNECTION_NETWORK_MAC, mac) for mac in mac_addresses},
            default_name=server.get("name") or host,
        )
    return DeviceInfo(
        identifiers={(domain, host)},
        name=server.get("name") or host,
    )


def resolve_private_key_path(hass: HomeAssistant, key: Optional[str]) -> Optional[str]:
    """Return an absolute path for an SSH private key.

    Keys may be provided as absolute paths, paths relative to the Home Assistant
    configuration directory, or with a leading ``~`` to refer to the container
    user's home. ``None`` or empty values pass through unchanged.
    """

    if not key:
        return None

    path = Path(key).expanduser()
    if not path.is_absolute():
        path = Path(hass.config.path(str(path)))
    return str(path)
