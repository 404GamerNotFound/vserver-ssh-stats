"""Coordinator helpers shared across platforms."""
from __future__ import annotations

import asyncio
import logging
import socket
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import DOMAIN
from .ssh_collector import async_sample, async_sample_docker, async_sample_packages
from .util import (
    DEFAULT_BACKOFF_FAILURE_THRESHOLD,
    DEFAULT_BACKOFF_MAX_INTERVAL,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_DOCKER_INTERVAL,
    DEFAULT_INTERVAL,
    DEFAULT_PACKAGE_INTERVAL,
    DEFAULT_SLOW_COMMAND_TIMEOUT,
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
        package_interval: int,
        docker_interval: int,
        slow_command_timeout: int,
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
        self.package_interval = package_interval
        self.docker_interval = docker_interval
        self.slow_command_timeout = slow_command_timeout
        self.consecutive_failures = 0
        self.current_interval = interval
        self._last_package_attempt = 0.0
        self._last_docker_attempt = 0.0
        self._slow_refresh_task: asyncio.Task[None] | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the server."""
        try:
            base_data = await async_sample(
                self.server["host"],
                self.server["username"],
                self.server.get("password"),
                self.server.get("key"),
                self.server.get("port", 22),
                self.server.get("target_os", "auto"),
                self.connect_timeout,
                self.command_timeout,
                self.server.get("monitored_ports"),
            )
            data = self._merge_base_data(base_data)
            if not data.get("collection_error"):
                self._schedule_slow_data(data)
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
                for key, value in data.items():
                    if key == "port_checks" or key.startswith(
                        ("port_open_", "port_response_time_ms_", "port_error_")
                    ):
                        preserved[key] = value
                preserved["collection_error"] = data["collection_error"]
                preserved["last_collection_failed"] = True
                return preserved
            data["last_collection_failed"] = True
            return data
        if data.get("mac_addresses"):
            self.server["mac_addresses"] = data["mac_addresses"]
        self._record_success()
        return data

    def _merge_base_data(self, base_data: dict[str, Any]) -> dict[str, Any]:
        """Merge fast collector data over the previous full snapshot."""

        if not isinstance(self.data, dict) or not self.data:
            return dict(base_data or {})
        merged = dict(self.data)
        merged.update(base_data or {})
        return merged

    def _slow_data_due(self, last_attempt: float, interval: int, now: float) -> bool:
        """Return whether a slow collector should run now."""

        return interval > 0 and (last_attempt <= 0 or now - last_attempt >= interval)

    def _clear_docker_data(self, data: dict[str, Any]) -> None:
        """Remove stale Docker-owned fields before applying a fresh Docker sample."""

        docker_keys = {
            "docker",
            "containers",
            "container_details",
            "container_stats",
            "docker_unhealthy_containers",
            "docker_restart_count_total",
            "docker_collection_error",
            "docker_collection_time_ms",
        }
        for key in list(data):
            if key in docker_keys or key.startswith("container_"):
                data.pop(key, None)

    def force_slow_refresh(self) -> None:
        """Make the next refresh run slow package and Docker collectors."""

        self._last_package_attempt = 0.0
        self._last_docker_attempt = 0.0

    def _schedule_slow_data(self, data: dict[str, Any]) -> None:
        """Schedule due package and Docker collectors without blocking base polling."""

        if data.get("os") == "Windows":
            return
        if self._slow_refresh_task and not self._slow_refresh_task.done():
            return

        now = time.monotonic()
        due_collectors: list[str] = []
        if self._slow_data_due(self._last_docker_attempt, self.docker_interval, now):
            self._last_docker_attempt = now
            due_collectors.append("docker")
        if self._slow_data_due(self._last_package_attempt, self.package_interval, now):
            self._last_package_attempt = now
            due_collectors.append("package")
        if not due_collectors:
            return

        self._slow_refresh_task = self.hass.async_create_task(
            self._async_update_slow_data(due_collectors)
        )

    async def _async_update_slow_data(self, due_collectors: list[str]) -> None:
        """Collect and publish slow metrics independently from the base poll."""

        try:
            for collector in due_collectors:
                try:
                    if collector == "package":
                        result = await async_sample_packages(
                            self.server["host"],
                            self.server["username"],
                            self.server.get("password"),
                            self.server.get("key"),
                            self.server.get("port", 22),
                            self.server.get("target_os", "auto"),
                            self.connect_timeout,
                            self.slow_command_timeout,
                        )
                    else:
                        result = await async_sample_docker(
                            self.server["host"],
                            self.server["username"],
                            self.server.get("password"),
                            self.server.get("key"),
                            self.server.get("port", 22),
                            self.server.get("target_os", "auto"),
                            self.connect_timeout,
                            self.slow_command_timeout,
                        )
                except Exception as err:
                    _LOGGER.debug(
                        "%s collector failed for %s: %s",
                        collector,
                        self.server["host"],
                        err,
                    )
                    result = {
                        f"{collector}_collection_error": (
                            str(err) or err.__class__.__name__
                        )
                    }

                merged = dict(self.data or {})
                if collector == "docker" and "docker" in result:
                    self._clear_docker_data(merged)
                merged.update(result)
                self.async_set_updated_data(merged)
        finally:
            self._slow_refresh_task = None

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
        package_interval = entry_data.get("package_interval") or DEFAULT_PACKAGE_INTERVAL
        docker_interval = entry_data.get("docker_interval") or DEFAULT_DOCKER_INTERVAL
        slow_command_timeout = (
            entry_data.get("slow_command_timeout") or DEFAULT_SLOW_COMMAND_TIMEOUT
        )
        coordinators = []
        for server in entry_data.get("servers", []):
            if not server.get("name"):
                continue
            coordinators.append(
                VServerCoordinator(
                    hass,
                    server,
                    interval,
                    connect_timeout,
                    command_timeout,
                    package_interval,
                    docker_interval,
                    slow_command_timeout,
                )
            )

        entry_data[COORDINATORS_KEY] = coordinators
        for coordinator in coordinators:
            hass.async_create_task(coordinator.async_request_refresh())
        return coordinators
