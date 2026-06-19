"""Utility helpers for the VServer SSH Stats integration."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

try:
    from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
except ImportError:  # pragma: no cover - compatibility with older Home Assistant versions
    CONNECTION_NETWORK_MAC = "mac"

DEFAULT_INTERVAL = 30
DEFAULT_CONNECT_TIMEOUT = 10
DEFAULT_COMMAND_TIMEOUT = 45
DEFAULT_PACKAGE_INTERVAL = 12 * 60 * 60
DEFAULT_DOCKER_INTERVAL = 30 * 60
DEFAULT_STORAGE_INTERVAL = 60 * 60
DEFAULT_SLOW_COMMAND_TIMEOUT = 180
DEFAULT_ACTION_COMMAND_TIMEOUT = 300
DEFAULT_COMMAND_ALLOWLIST = ""
DEFAULT_CUSTOM_SENSOR_INTERVAL = 60 * 60
DEFAULT_CUSTOM_SENSOR_TIMEOUT = 30
MIN_CUSTOM_SENSOR_INTERVAL = 5
DEFAULT_BACKOFF_FAILURE_THRESHOLD = 3
DEFAULT_BACKOFF_MAX_INTERVAL = 300

MAC_PATTERN = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
PORT_SPLIT_PATTERN = re.compile(r"[\s,;]+")


def parse_monitored_ports(value: object) -> list[int]:
    """Return a de-duplicated list of TCP ports from user input."""

    if value in (None, ""):
        return []

    if isinstance(value, str):
        raw_ports: list[object] = [
            part.strip() for part in PORT_SPLIT_PATTERN.split(value) if part.strip()
        ]
    elif isinstance(value, list):
        raw_ports = value
    elif isinstance(value, tuple):
        raw_ports = list(value)
    else:
        raw_ports = [value]

    ports: list[int] = []
    for raw_port in raw_ports:
        if isinstance(raw_port, bool):
            raise ValueError("Invalid port")
        try:
            port = int(str(raw_port).strip())
        except (TypeError, ValueError) as err:
            raise ValueError("Invalid port") from err
        if port < 1 or port > 65535:
            raise ValueError("Port out of range")
        if port not in ports:
            ports.append(port)
    return ports


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
    """Return stable device info for one configured server."""

    host = server["host"]
    mac_addresses = normalize_mac_addresses(server.get("mac_addresses"))
    return DeviceInfo(
        identifiers={(domain, host)},
        connections={(CONNECTION_NETWORK_MAC, mac) for mac in mac_addresses},
        name=server.get("name") or host,
    )


def build_container_device_info(
    domain: str,
    server: dict,
    container_name: str,
    sanitized_name: str,
) -> DeviceInfo:
    """Return device info for one Docker container below its host."""

    host = server["host"]
    server_name = server.get("name") or host
    return DeviceInfo(
        identifiers={(domain, f"{host}_container_{sanitized_name}")},
        name=f"{server_name} {container_name}",
        manufacturer="Docker",
        model="Container",
        via_device=(domain, host),
    )


def build_storage_device_info(
    domain: str,
    server: dict,
    device: dict,
) -> DeviceInfo:
    """Return device info for one physical storage device below its host."""

    host = server["host"]
    server_name = server.get("name") or host
    key = device["key"]
    model = device.get("model") or "Storage device"
    protocol = str(device.get("protocol") or "storage").upper()
    return DeviceInfo(
        identifiers={(domain, f"{host}_storage_{key}")},
        name=f"{server_name} {device.get('name') or key}",
        manufacturer=protocol,
        model=model,
        via_device=(domain, host),
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
