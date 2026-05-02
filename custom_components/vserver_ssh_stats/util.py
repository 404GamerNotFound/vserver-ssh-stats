"""Utility helpers for the VServer SSH Stats integration."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from homeassistant.core import HomeAssistant

DEFAULT_INTERVAL = 30
DEFAULT_CONNECT_TIMEOUT = 10
DEFAULT_COMMAND_TIMEOUT = 15
DEFAULT_ACTION_COMMAND_TIMEOUT = 300


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
