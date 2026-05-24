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
from .util import (
    DEFAULT_BACKOFF_FAILURE_THRESHOLD,
    DEFAULT_BACKOFF_MAX_INTERVAL,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)
COORDINATORS_KEY = "coordinators"
COORDINATOR_LOCK_KEY = "coordinators_lock"


class VServerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls a server via SSH."""

    def __init__(
        self,
        hass: HomeAssistant,
        server: dict[str, Any],
        interval: int,
        connect_timeout: int,
        command_timeout: int,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=server["name"],
            update_interval=timedelta(seconds=interval),
        )
        self.server = server
        self.base_interval = interval
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self.consecutive_failures = 0
        self.current_interval = interval

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the server."""
        try:
            data = await async_sample(
                self.server["host"],
                self.server["username"],
                self.server.get("password"),
                self.server.get("key"),
                self.server.get("port", 22),
                self.server.get("target_os", "auto"),
                self.connect_timeout,
                self.command_timeout,
            )
            if not data:
                data = {"collection_error": f"No data returned from host: {self.server['host']}"}
        except socket.gaierror as err:
            self._record_failure()
            raise UpdateFailed(f"Unable to resolve host: {self.server['host']}") from err
        except UpdateFailed:
            self._record_failure()
            raise
        except Exception as err:
            self._record_failure()
            message = str(err) or err.__class__.__name__
            raise UpdateFailed(f"Unable to update host {self.server['host']}: {message}") from err
        if data.get("collection_error"):
            self._record_failure()
            if isinstance(self.data, dict) and self.data:
                preserved = dict(self.data)
                preserved["collection_error"] = data["collection_error"]
                preserved["last_collection_failed"] = True
                return preserved
            data["last_collection_failed"] = True
            return data
        if data.get("mac_addresses"):
            self.server["mac_addresses"] = data["mac_addresses"]
        self._record_success()
        return data

    def _record_success(self) -> None:
        """Reset backoff after a successful update."""

        self.consecutive_failures = 0
        self._set_poll_interval(self.base_interval)

    def _record_failure(self) -> None:
        """Increase polling interval after repeated failures."""

        self.consecutive_failures += 1
        if self.consecutive_failures < DEFAULT_BACKOFF_FAILURE_THRESHOLD:
            return
        exponent = self.consecutive_failures - DEFAULT_BACKOFF_FAILURE_THRESHOLD + 1
        interval = min(
            self.base_interval * (2 ** exponent),
            DEFAULT_BACKOFF_MAX_INTERVAL,
        )
        self._set_poll_interval(interval)

    def _set_poll_interval(self, interval: int) -> None:
        """Apply a runtime-only polling interval."""

        if self.current_interval == interval:
            return
        self.current_interval = interval
        self.update_interval = timedelta(seconds=interval)


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

        interval = entry_data.get("interval") or DEFAULT_INTERVAL
        connect_timeout = entry_data.get("connect_timeout") or DEFAULT_CONNECT_TIMEOUT
        configured_command_timeout = entry_data.get("command_timeout") or DEFAULT_COMMAND_TIMEOUT
        command_timeout = max(configured_command_timeout, DEFAULT_COMMAND_TIMEOUT)
        coordinators = []
        for server in entry_data.get("servers", []):
            if not server.get("name"):
                continue
            coordinators.append(
                VServerCoordinator(hass, server, interval, connect_timeout, command_timeout)
            )

        entry_data[COORDINATORS_KEY] = coordinators
        for coordinator in coordinators:
            hass.async_create_task(coordinator.async_request_refresh())
        return coordinators
