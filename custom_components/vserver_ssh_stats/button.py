"""Button platform for VServer SSH Stats."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable

from homeassistant.components.button import ButtonEntity
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
from .util import (
    DEFAULT_CONNECT_TIMEOUT,
    build_container_device_info,
    build_device_info,
)

_LOGGER = logging.getLogger(__name__)

ACTION_BUTTONS: tuple[tuple[str, str], ...] = (
    ("refresh", "Refresh now"),
    ("update_package_list", "Update package list"),
    ("upgrade_packages", "Upgrade packages"),
    ("update_packages", "Update packages"),
    ("prune_docker", "Prune Docker"),
    ("clear_package_cache", "Clear package cache"),
    ("reboot_host", "Reboot host"),
)


class VServerActionButton(ButtonEntity):
    """Representation of a VServer action as a button."""

    def __init__(
        self,
        hass: HomeAssistant,
        server: Dict[str, Any],
        action: str,
        name: str,
        connect_timeout: int,
    ) -> None:
        """Initialize the button."""
        self.hass = hass
        self._server = server
        self._action = action
        self._connect_timeout = connect_timeout
        host = server["host"]
        self._attr_unique_id = f"{host}_{action}"
        self._attr_name = f"{server['name']} {name}"
        self._attr_device_info = build_device_info(DOMAIN, server)

    async def async_press(self) -> None:
        """Call the underlying service when the button is pressed."""
        if self._action == "refresh":
            data = {"host": self._server["host"]}
        else:
            data = {
                "host": self._server["host"],
                "username": self._server["username"],
                "port": self._server.get("port", 22),
                "target_os": self._server.get("target_os", "auto"),
                "connect_timeout": self._connect_timeout,
            }
            if self._server.get("password"):
                data["password"] = self._server["password"]
            if self._server.get("key"):
                data["key"] = self._server["key"]
        await self.hass.services.async_call(DOMAIN, self._action, data, blocking=True)


class VServerContainerRestartButton(
    CoordinatorEntity[VServerCoordinator], ButtonEntity
):
    """Restart one Docker container."""

    _attr_icon = "mdi:restart"

    def __init__(
        self,
        coordinator: VServerCoordinator,
        container_name: str,
        sanitized_name: str,
        connect_timeout: int,
    ) -> None:
        """Initialize the restart button."""

        super().__init__(coordinator)
        self._container_name = container_name
        self._sanitized_name = sanitized_name
        self._connect_timeout = connect_timeout
        host = coordinator.server["host"]
        server_name = coordinator.server.get("name") or host
        self._attr_unique_id = f"{host}_container_{sanitized_name}_restart"
        self._attr_name = f"{server_name} {container_name} Restart"
        self._attr_device_info = build_container_device_info(
            DOMAIN,
            coordinator.server,
            container_name,
            sanitized_name,
        )

    @property
    def available(self) -> bool:
        """Return whether current container inventory is available."""

        return (
            self.coordinator.last_update_success
            and find_container(self.coordinator.data, self._sanitized_name) is not None
        )

    def update_container_name(self, container_name: str) -> None:
        """Keep the action target aligned with fresh Docker inventory."""

        self._container_name = container_name
        self._attr_device_info = build_container_device_info(
            DOMAIN,
            self.coordinator.server,
            container_name,
            self._sanitized_name,
        )

    async def async_press(self) -> None:
        """Restart the container."""

        await self.hass.services.async_call(
            DOMAIN,
            "restart_docker_container",
            build_container_action_data(
                self.coordinator.server,
                self._connect_timeout,
                self._container_name,
            ),
            blocking=True,
        )


@dataclass
class ServerContainerButtonRegistry:
    """Track container restart buttons created for one server."""

    coordinator: VServerCoordinator
    connect_timeout: int
    known_containers: set[str] = field(default_factory=set)
    entities_by_container: dict[str, VServerContainerRestartButton] = field(
        default_factory=dict
    )

    def create_entities(
        self,
        names: dict[str, str],
    ) -> list[VServerContainerRestartButton]:
        """Create buttons for container names not seen before."""

        entities: list[VServerContainerRestartButton] = []
        for sanitized, raw_name in names.items():
            if sanitized in self.known_containers:
                self.entities_by_container[sanitized].update_container_name(raw_name)
                continue
            self.known_containers.add(sanitized)
            entity = VServerContainerRestartButton(
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
    ) -> list[VServerContainerRestartButton]:
        """Create buttons from fresh coordinator data."""

        return self.create_entities(container_names_from_stats(stats))


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up buttons for VServer SSH Stats based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    servers = data.get("servers", [])
    connect_timeout = data.get("connect_timeout") or DEFAULT_CONNECT_TIMEOUT
    entities: list[ButtonEntity] = []
    for srv in servers:
        name = srv.get("name")
        if not name:
            continue
        for action, button_name in ACTION_BUTTONS:
            entities.append(
                VServerActionButton(hass, srv, action, button_name, connect_timeout)
            )

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
    coordinators = await async_get_or_create_coordinators(hass, entry)
    for coordinator in coordinators:
        server_name = coordinator.server.get("name") or coordinator.server["host"]
        registry = ServerContainerButtonRegistry(coordinator, connect_timeout)
        entities.extend(
            registry.create_entities(
                container_names_from_registry(
                    registry_entries,
                    coordinator.server["host"],
                    server_name,
                    "_restart",
                    " Restart",
                )
            )
        )
        stats = coordinator.data if isinstance(coordinator.data, dict) else {}
        entities.extend(
            registry.create_entities_from_stats(stats.get("container_stats"))
        )

        def _make_listener(
            container_registry: ServerContainerButtonRegistry,
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
