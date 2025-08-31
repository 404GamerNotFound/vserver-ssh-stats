"""VServer SSH Stats integration for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

_LOGGER = logging.getLogger(__name__)

DOMAIN = "vserver_ssh_stats"

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the VServer SSH Stats integration."""
    _LOGGER.debug("Setting up VServer SSH Stats")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up VServer SSH Stats from a config entry."""
    _LOGGER.debug("Setting up VServer SSH Stats entry")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a VServer SSH Stats config entry."""
    _LOGGER.debug("Unloading VServer SSH Stats entry")
    return True
