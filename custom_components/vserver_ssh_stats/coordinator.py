"""Coordinator helpers shared across platforms."""
from __future__ import annotations

import asyncio
import logging
import socket
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import DOMAIN
from .ssh_collector import async_sample

_LOGGER = logging.getLogger(__name__)
COORDINATORS_KEY = "coordinators"
COORDINATOR_LOCK_KEY = "coordinators_lock"


class VServerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls a server via SSH."""

    def __init__(self, hass: HomeAssistant, server: dict[str, Any], interval: int) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=server["name"],
            update_interval=timedelta(seconds=interval),
        )
        self.server = server

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the server."""
        try:
            return await async_sample(
                self.server["host"],
                self.server["username"],
                self.server.get("password"),
                self.server.get("key"),
                self.server.get("port", 22),
                self.server.get("target_os", "auto"),
            )
        except socket.gaierror as err:
            raise UpdateFailed(f"Unable to resolve host: {self.server['host']}") from err


async def async_get_or_create_coordinators(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> list[VServerCoordinator]:
    """Return coordinators for a config entry, creating them once."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinators = entry_data.get(COORDINATORS_KEY)
    if coordinators is not None:
        return coordinators

    lock = entry_data.setdefault(COORDINATOR_LOCK_KEY, asyncio.Lock())
    async with lock:
        coordinators = entry_data.get(COORDINATORS_KEY)
        if coordinators is not None:
            return coordinators

        interval = entry_data.get("interval", 30)
        coordinators = []
        for server in entry_data.get("servers", []):
            if not server.get("name"):
                continue
            coordinators.append(VServerCoordinator(hass, server, interval))

        if coordinators:
            await asyncio.gather(
                *(coordinator.async_config_entry_first_refresh() for coordinator in coordinators)
            )

        entry_data[COORDINATORS_KEY] = coordinators
        return coordinators
