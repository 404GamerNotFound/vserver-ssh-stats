"""Button platform for VServer SSH Stats."""
from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import DOMAIN
from .util import DEFAULT_CONNECT_TIMEOUT, build_device_info

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


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up buttons for VServer SSH Stats based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    servers = data.get("servers", [])
    connect_timeout = data.get("connect_timeout") or DEFAULT_CONNECT_TIMEOUT
    entities: list[VServerActionButton] = []
    for srv in servers:
        name = srv.get("name")
        if not name:
            continue
        for action, button_name in ACTION_BUTTONS:
            entities.append(
                VServerActionButton(hass, srv, action, button_name, connect_timeout)
            )
    async_add_entities(entities)
