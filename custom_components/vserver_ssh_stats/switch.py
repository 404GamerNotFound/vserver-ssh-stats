"""Switch platform for Docker containers monitored by VServer SSH Stats."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .coordinator import VServerCoordinator, async_get_or_create_coordinators
from .docker_entities import (
    build_container_action_data,
    container_names_from_registry,
    container_names_from_stats,
    find_container,
)
from .util import DEFAULT_CONNECT_TIMEOUT, build_container_device_info


class VServerContainerSwitch(CoordinatorEntity[VServerCoordinator], SwitchEntity):
    """Start or stop one Docker container."""

    _attr_icon = "mdi:docker"
    _unrecorded_attributes = frozenset(
        {
            "container_id",
            "image",
            "status",
            "health_state",
            "restart_count",
            "restart_policy",
            "compose_project",
            "compose_service",
            "swarm_service",
        }
    )

    def __init__(
        self,
        coordinator: VServerCoordinator,
        container_name: str,
        sanitized_name: str,
        connect_timeout: int,
    ) -> None:
        """Initialize the container switch."""

        super().__init__(coordinator)
        self._container_name = container_name
        self._sanitized_name = sanitized_name
        self._connect_timeout = connect_timeout
        host = coordinator.server["host"]
        server_name = coordinator.server.get("name") or host
        self._attr_unique_id = f"{host}_container_{sanitized_name}_running"
        self._attr_name = f"{server_name} {container_name} Running"
        self._attr_device_info = build_container_device_info(
            DOMAIN,
            coordinator.server,
            container_name,
            sanitized_name,
        )

    @property
    def available(self) -> bool:
        """Return whether current container inventory is available."""

        return self.coordinator.last_update_success and self._container is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether the container is running."""

        container = self._container
        return bool(container.get("running")) if container is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the latest Docker inventory details."""

        container = self._container
        if container is None:
            return {}
        return {
            "container_id": container.get("id"),
            "image": container.get("image"),
            "status": container.get("status"),
            "health_state": container.get("health_state"),
            "restart_count": container.get("restart_count"),
            "restart_policy": container.get("restart_policy"),
            "compose_project": container.get("compose_project"),
            "compose_service": container.get("compose_service"),
            "swarm_service": container.get("swarm_service"),
        }

    @property
    def _container(self) -> dict[str, Any] | None:
        """Return current data for this container."""

        return find_container(self.coordinator.data, self._sanitized_name)

    def update_container_name(self, container_name: str) -> None:
        """Keep the action target aligned with fresh Docker inventory."""

        self._container_name = container_name
        self._attr_device_info = build_container_device_info(
            DOMAIN,
            self.coordinator.server,
            container_name,
            self._sanitized_name,
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start the container."""

        await self._async_run_action("start_docker_container")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the container."""

        await self._async_run_action("stop_docker_container")

    async def _async_run_action(self, service: str) -> None:
        """Call one container action service."""

        await self.hass.services.async_call(
            DOMAIN,
            service,
            build_container_action_data(
                self.coordinator.server,
                self._connect_timeout,
                self._container_name,
            ),
            blocking=True,
        )


@dataclass
class ServerContainerSwitchRegistry:
    """Track container switches created for one server."""

    coordinator: VServerCoordinator
    connect_timeout: int
    known_containers: set[str] = field(default_factory=set)
    entities_by_container: dict[str, VServerContainerSwitch] = field(
        default_factory=dict
    )

    def create_entities(
        self,
        names: dict[str, str],
    ) -> list[VServerContainerSwitch]:
        """Create switches for container names not seen before."""

        entities: list[VServerContainerSwitch] = []
        for sanitized, raw_name in names.items():
            if sanitized in self.known_containers:
                self.entities_by_container[sanitized].update_container_name(raw_name)
                continue
            self.known_containers.add(sanitized)
            entity = VServerContainerSwitch(
                self.coordinator,
                raw_name,
                sanitized,
                self.connect_timeout,
            )
            self.entities_by_container[sanitized] = entity
            entities.append(entity)
        return entities

    def create_entities_from_stats(
        self,
        stats: Iterable[dict[str, Any]] | None,
    ) -> list[VServerContainerSwitch]:
        """Create switches from fresh coordinator data."""

        return self.create_entities(container_names_from_stats(stats))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up Docker container switches."""

    data = hass.data[DOMAIN][entry.entry_id]
    connect_timeout = data.get("connect_timeout") or DEFAULT_CONNECT_TIMEOUT
    entity_registry = er.async_get(hass)
    try:
        registry_entries = er.async_entries_for_config_entry(
            entity_registry,
            entry.entry_id,
        )
    except AttributeError:  # pragma: no cover - older Home Assistant versions
        registry_entries = [
            registry_entry
            for registry_entry in entity_registry.entities.values()
            if registry_entry.config_entry_id == entry.entry_id
        ]

    entities: list[VServerContainerSwitch] = []
    coordinators = await async_get_or_create_coordinators(hass, entry)
    for coordinator in coordinators:
        server_name = coordinator.server.get("name") or coordinator.server["host"]
        registry = ServerContainerSwitchRegistry(coordinator, connect_timeout)
        entities.extend(
            registry.create_entities(
                container_names_from_registry(
                    registry_entries,
                    coordinator.server["host"],
                    server_name,
                    "_running",
                    " Running",
                )
            )
        )
        stats = coordinator.data if isinstance(coordinator.data, dict) else {}
        entities.extend(
            registry.create_entities_from_stats(stats.get("container_stats"))
        )

        def _make_listener(
            container_registry: ServerContainerSwitchRegistry,
        ) -> Callable[[], None]:
            def _handle_update() -> None:
                current = container_registry.coordinator.data
                stats = (
                    current.get("container_stats")
                    if isinstance(current, dict)
                    else None
                )
                new_entities = container_registry.create_entities_from_stats(stats)
                if new_entities:
                    async_add_entities(new_entities)

            return _handle_update

        remove_listener = coordinator.async_add_listener(_make_listener(registry))
        entry.async_on_unload(remove_listener)

    async_add_entities(entities)
