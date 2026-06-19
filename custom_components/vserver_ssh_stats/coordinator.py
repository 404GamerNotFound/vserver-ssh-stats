"""Coordinator helpers shared across platforms."""
from __future__ import annotations

import asyncio
import logging
import re
import socket
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import DOMAIN
from .ssh_collector import (
    async_run_custom_command,
    async_sample,
    async_sample_docker,
    async_sample_packages,
    async_sample_storage,
)
from .util import (
    DEFAULT_BACKOFF_FAILURE_THRESHOLD,
    DEFAULT_BACKOFF_MAX_INTERVAL,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_DOCKER_INTERVAL,
    DEFAULT_INTERVAL,
    DEFAULT_PACKAGE_INTERVAL,
    DEFAULT_SLOW_COMMAND_TIMEOUT,
    DEFAULT_STORAGE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)
COORDINATORS_KEY = "coordinators"
COORDINATOR_LOCK_KEY = "coordinators_lock"
CUSTOM_COORDINATORS_KEY = "custom_sensor_coordinators"
CUSTOM_COORDINATOR_LOCK_KEY = "custom_sensor_coordinators_lock"


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
        storage_interval: int,
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
        self.storage_interval = storage_interval
        self.slow_command_timeout = slow_command_timeout
        self.consecutive_failures = 0
        self.current_interval = interval
        self._last_package_attempt = 0.0
        self._last_docker_attempt = 0.0
        self._last_storage_attempt = 0.0
        self._slow_refresh_task: asyncio.Task[None] | None = None
        self._docker_state_revision = 0

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
                self.server.get("host_key_fingerprints"),
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
            "container_lookup",
            "container_stats",
            "docker_unhealthy_containers",
            "docker_restart_count_total",
            "docker_collection_error",
            "docker_collection_time_ms",
            "docker_images_size_bytes",
            "docker_containers_size_bytes",
            "docker_volumes_size_bytes",
            "docker_build_cache_size_bytes",
        }
        for key in list(data):
            if key in docker_keys or key.startswith("container_"):
                data.pop(key, None)

    def _clear_storage_data(self, data: dict[str, Any]) -> None:
        """Remove stale SMART/NVMe fields before applying a fresh sample."""

        for key in (
            "storage_devices",
            "storage_device_lookup",
            "smart_failed_devices",
            "smart_failure_detected",
            "storage_tools_available",
            "storage_stats_partial",
            "storage_devices_seen",
            "storage_devices_collected",
            "storage_device_errors",
            "raid_detail_arrays",
            "storage_collection_error",
            "storage_collection_time_ms",
        ):
            data.pop(key, None)

    def force_slow_refresh(self) -> None:
        """Make the next refresh run all slow collectors."""

        self._last_package_attempt = 0.0
        self._last_docker_attempt = 0.0
        self._last_storage_attempt = 0.0

    async def async_wait_for_slow_refresh(self) -> None:
        """Wait until a slow collector scheduled by the latest refresh finishes."""

        task = self._slow_refresh_task
        if task and task is not asyncio.current_task():
            await task

    def apply_docker_action_state(self, container_name: str, action: str) -> None:
        """Publish the state guaranteed by a successful Docker action."""

        # Invalidate Docker samples that started before this action completed.
        self._docker_state_revision += 1
        if not isinstance(self.data, dict):
            return
        container_stats = self.data.get("container_stats")
        if not isinstance(container_stats, list):
            return

        changed = False
        updated_stats: list[Any] = []
        for container in container_stats:
            if not isinstance(container, dict) or container.get("name") != container_name:
                updated_stats.append(container)
                continue
            updated_container = dict(container)
            updated_container["running"] = action != "stop"
            updated_stats.append(updated_container)
            changed = True
        if not changed:
            return

        updated_data = dict(self.data)
        updated_data["container_stats"] = updated_stats
        updated_data["container_details"] = updated_stats
        updated_data["container_lookup"] = {
            self._sanitize_container_name(str(container.get("name") or "")): container
            for container in updated_stats
            if isinstance(container, dict) and container.get("name")
        }
        self.async_set_updated_data(updated_data)

    @staticmethod
    def _sanitize_container_name(name: str) -> str:
        """Return the lookup key used for a container name."""

        return re.sub(r"[^a-zA-Z0-9_]+", "_", name).lower()

    async def async_request_docker_refresh(self) -> None:
        """Refresh only Docker data without blocking the regular base poll."""

        active_task = self._slow_refresh_task
        if active_task and not active_task.done():
            await active_task
        self._last_docker_attempt = time.monotonic()
        self._slow_refresh_task = self.hass.async_create_task(
            self._async_update_slow_data(["docker"])
        )
        await self._slow_refresh_task

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
        if self._slow_data_due(self._last_storage_attempt, self.storage_interval, now):
            self._last_storage_attempt = now
            due_collectors.append("storage")
        if not due_collectors:
            return

        self._slow_refresh_task = self.hass.async_create_task(
            self._async_update_slow_data(due_collectors)
        )

    async def _async_update_slow_data(self, due_collectors: list[str]) -> None:
        """Collect and publish slow metrics independently from the base poll."""

        try:
            for collector in due_collectors:
                docker_revision = (
                    self._docker_state_revision if collector == "docker" else None
                )
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
                            self.server.get("host_key_fingerprints"),
                        )
                    elif collector == "docker":
                        result = await async_sample_docker(
                            self.server["host"],
                            self.server["username"],
                            self.server.get("password"),
                            self.server.get("key"),
                            self.server.get("port", 22),
                            self.server.get("target_os", "auto"),
                            self.connect_timeout,
                            self.slow_command_timeout,
                            self.server.get("host_key_fingerprints"),
                        )
                    elif collector == "storage":
                        result = await async_sample_storage(
                            self.server["host"],
                            self.server["username"],
                            self.server.get("password"),
                            self.server.get("key"),
                            self.server.get("port", 22),
                            self.server.get("target_os", "auto"),
                            self.connect_timeout,
                            self.slow_command_timeout,
                            self.server.get("host_key_fingerprints"),
                        )
                    else:
                        continue
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

                if (
                    collector == "docker"
                    and docker_revision != self._docker_state_revision
                ):
                    _LOGGER.debug(
                        "Discarding stale Docker sample for %s after a container action",
                        self.server["host"],
                    )
                    continue

                merged = dict(self.data or {})
                if collector == "docker" and "docker" in result:
                    self._clear_docker_data(merged)
                if collector == "storage" and "storage_devices" in result:
                    self._clear_storage_data(merged)
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


class CustomCommandCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that runs one user-configured SSH command."""

    def __init__(
        self,
        hass: HomeAssistant,
        server: dict[str, Any],
        definition: dict[str, Any],
        connect_timeout: int,
    ) -> None:
        """Initialize a custom command coordinator."""

        interval = max(5, int(definition["interval"]))
        super().__init__(
            hass,
            _LOGGER,
            name=f"{server['name']} custom sensor {definition['name']}",
            update_interval=timedelta(seconds=interval),
        )
        self.server = server
        self.definition = definition
        self.connect_timeout = connect_timeout
        self.command_timeout = max(1, min(3600, int(definition["timeout"])))

    async def _async_update_data(self) -> dict[str, Any]:
        """Execute the configured command and publish its output."""

        try:
            output, timing = await async_run_custom_command(
                self.server["host"],
                self.server["username"],
                self.server.get("password"),
                self.server.get("key"),
                self.server.get("port", 22),
                self.definition["command"],
                self.connect_timeout,
                self.command_timeout,
                self.server.get("host_key_fingerprints"),
            )
        except Exception as err:
            message = str(err) or err.__class__.__name__
            raise UpdateFailed(
                f"Custom sensor {self.definition['name']} failed on "
                f"{self.server['host']}: {message}"
            ) from err
        return {
            "output": output.strip(),
            "updated_at": datetime.now(UTC).isoformat(),
            **timing,
        }


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
        storage_interval = entry_data.get("storage_interval")
        if storage_interval is None:
            storage_interval = DEFAULT_STORAGE_INTERVAL
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
                    storage_interval,
                    slow_command_timeout,
                )
            )

        entry_data[COORDINATORS_KEY] = coordinators
        for coordinator in coordinators:
            hass.async_create_task(coordinator.async_request_refresh())
        return coordinators


async def async_get_or_create_custom_sensor_coordinators(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> list[CustomCommandCoordinator]:
    """Return one independent coordinator per configured custom sensor."""

    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinators = entry_data.get(CUSTOM_COORDINATORS_KEY)
    if coordinators is not None:
        return coordinators

    lock = entry_data.setdefault(CUSTOM_COORDINATOR_LOCK_KEY, asyncio.Lock())
    async with lock:
        coordinators = entry_data.get(CUSTOM_COORDINATORS_KEY)
        if coordinators is not None:
            return coordinators

        servers = {
            server.get("host"): server
            for server in entry_data.get("servers", [])
            if server.get("host") and server.get("name")
        }
        connect_timeout = entry_data.get("connect_timeout") or DEFAULT_CONNECT_TIMEOUT
        coordinators = []
        for definition in entry_data.get("custom_sensors", []):
            if not isinstance(definition, dict):
                continue
            server = servers.get(definition.get("server_host"))
            if not server or not all(
                definition.get(key)
                for key in ("id", "name", "command", "interval", "timeout")
            ):
                continue
            try:
                coordinators.append(
                    CustomCommandCoordinator(hass, server, definition, connect_timeout)
                )
            except (KeyError, TypeError, ValueError):
                _LOGGER.warning(
                    "Ignoring invalid custom sensor definition %s",
                    definition.get("id"),
                )

        entry_data[CUSTOM_COORDINATORS_KEY] = coordinators
        for coordinator in coordinators:
            hass.async_create_task(coordinator.async_request_refresh())
        return coordinators
