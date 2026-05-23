"""Binary sensor platform for VServer SSH Stats."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .coordinator import VServerCoordinator, async_get_or_create_coordinators
from .util import build_device_info


BINARY_SENSORS: tuple[tuple[str, str, str], ...] = (
    ("reboot_required", "Reboot Required", "mdi:restart-alert"),
    ("root_fs_readonly", "Root Filesystem Read-only", "mdi:file-lock"),
)


class VServerOnlineBinarySensor(CoordinatorEntity[VServerCoordinator], BinarySensorEntity):
    """Binary sensor representing host availability."""

    def __init__(self, coordinator: VServerCoordinator, server_name: str) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        host = coordinator.server["host"]
        self._attr_unique_id = f"{host}_online"
        self._attr_name = f"{server_name} Online"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_info = build_device_info(DOMAIN, coordinator.server)
        self._last_seen: str | None = None

    @property
    def is_on(self) -> bool:
        """Return True when the host is reachable."""
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional availability context."""
        return {
            "last_seen": self._last_seen,
            "consecutive_failures": self.coordinator.consecutive_failures,
            "current_poll_interval": self.coordinator.current_interval,
        }

    @property
    def available(self) -> bool:
        """Always keep the entity available so automations can read an off state."""
        return True

    @property
    def should_poll(self) -> bool:
        """Coordinator handles polling."""
        return False

    @property
    def force_update(self) -> bool:
        """Do not force recorder updates for unchanged states."""
        return False

    @property
    def icon(self) -> str:
        """Return a dynamic icon for availability."""
        return "mdi:lan-connect" if self.is_on else "mdi:lan-disconnect"

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.last_update_success and self.coordinator.data:
            self._last_seen = datetime.now(UTC).isoformat()
        super()._handle_coordinator_update()


class VServerDiagnosticBinarySensor(
    CoordinatorEntity[VServerCoordinator],
    BinarySensorEntity,
):
    """Binary sensor for independent diagnostic flags reported by the collector."""

    def __init__(
        self,
        coordinator: VServerCoordinator,
        server_name: str,
        key: str,
        name: str,
        icon: str,
    ) -> None:
        """Initialize the binary diagnostic sensor."""

        super().__init__(coordinator)
        host = coordinator.server["host"]
        self._key = key
        self._icon = icon
        self._attr_unique_id = f"{host}_{key}"
        self._attr_name = f"{server_name} {name}"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_info = build_device_info(DOMAIN, coordinator.server)

    @property
    def is_on(self) -> bool | None:
        """Return the diagnostic flag value or unknown when the field is absent."""

        if not isinstance(self.coordinator.data, dict) or self._key not in self.coordinator.data:
            return None
        return bool(self.coordinator.data.get(self._key))

    @property
    def icon(self) -> str:
        """Return the configured icon."""

        return self._icon


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up VServer SSH Stats binary sensors based on a config entry."""
    entities: list[BinarySensorEntity] = []
    coordinators = await async_get_or_create_coordinators(hass, entry)
    for coordinator in coordinators:
        name = coordinator.server.get("name")
        if not name:
            continue
        entities.append(VServerOnlineBinarySensor(coordinator, name))
        for key, binary_name, icon in BINARY_SENSORS:
            entities.append(
                VServerDiagnosticBinarySensor(coordinator, name, key, binary_name, icon)
            )
    async_add_entities(entities, update_before_add=True)
