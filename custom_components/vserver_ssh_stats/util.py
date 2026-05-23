"""Utility helpers for the VServer SSH Stats integration."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from homeassistant.core import HomeAssistant

DEFAULT_INTERVAL = 30
DEFAULT_CONNECT_TIMEOUT = 10
DEFAULT_COMMAND_TIMEOUT = 15
DEFAULT_ACTION_COMMAND_TIMEOUT = 300
DEFAULT_COMMAND_ALLOWLIST = ""
DEFAULT_BACKOFF_FAILURE_THRESHOLD = 3
DEFAULT_BACKOFF_MAX_INTERVAL = 300


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
