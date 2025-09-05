"""Button platform for VServer SSH Stats."""
from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


class VServerActionButton(ButtonEntity):
    """Representation of a VServer action as a button."""

    def __init__(self, hass: HomeAssistant, server: Dict[str, Any], action: str, name: str) -> None:
        """Initialize the button."""
        self.hass = hass
        self._server = server
        self._action = action
        host = server["host"]
        self._attr_unique_id = f"{host}_{action}"
        self._attr_name = f"{server['name']} {name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, host)},
            name=server["name"],
        )

    async def async_press(self) -> None:
        """Call the underlying service when the button is pressed."""
        data = {
            "host": self._server["host"],
            "username": self._server["username"],
            "port": self._server.get("port", 22),
        }
        if self._server.get("password"):
            data["password"] = self._server["password"]
        if self._server.get("key"):
            data["key"] = self._server["key"]
        await self.hass.services.async_call(DOMAIN, self._action, data, blocking=True)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up buttons for VServer SSH Stats based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    servers = data.get("servers", [])
    entities: list[VServerActionButton] = []
    for srv in servers:
        name = srv.get("name")
        if not name:
            continue
        entities.append(VServerActionButton(hass, srv, "update_packages", "Update packages"))
        entities.append(VServerActionButton(hass, srv, "reboot_host", "Reboot host"))
    async_add_entities(entities)
