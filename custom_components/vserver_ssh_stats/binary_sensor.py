"""Binary sensor platform for VServer SSH Stats."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .coordinator import VServerCoordinator, async_get_or_create_coordinators


class VServerOnlineBinarySensor(CoordinatorEntity[VServerCoordinator], BinarySensorEntity):
    """Binary sensor representing host availability."""

    def __init__(self, coordinator: VServerCoordinator, server_name: str) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        host = coordinator.server["host"]
        self._attr_unique_id = f"{host}_online"
        self._attr_name = f"{server_name} Online"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, host)},
            name=server_name,
        )
        self._last_seen: str | None = None

    @property
    def is_on(self) -> bool:
        """Return True when the host is reachable."""
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional availability context."""
        return {"last_seen": self._last_seen}

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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up VServer SSH Stats binary sensors based on a config entry."""
    entities: list[VServerOnlineBinarySensor] = []
    coordinators = await async_get_or_create_coordinators(hass, entry)
    for coordinator in coordinators:
        name = coordinator.server.get("name")
        if not name:
            continue
        entities.append(VServerOnlineBinarySensor(coordinator, name))
    async_add_entities(entities, update_before_add=True)
