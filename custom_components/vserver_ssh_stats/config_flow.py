"""Config flow for VServer SSH Stats."""
from __future__ import annotations

from typing import Any
import json

import voluptuous as vol
from homeassistant import config_entries

from . import DOMAIN

DEFAULT_INTERVAL = 30

DEFAULT_SERVERS_JSON = (
    "[{\"name\": \"vps1\", \"host\": \"203.0.113.10\", "
    "\"username\": \"root\", \"password\": \"deinpasswort\"}]"
)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required("mqtt_host", default="homeassistant"): str,
        vol.Required("mqtt_port", default=1883): int,
        vol.Optional("mqtt_user", default=""): str,
        vol.Optional("mqtt_pass", default=""): str,
        vol.Required("interval", default=DEFAULT_INTERVAL): int,
        vol.Optional("servers_json", default=DEFAULT_SERVERS_JSON): str,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for VServer SSH Stats."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                json.loads(user_input["servers_json"])
            except ValueError:
                errors["servers_json"] = "invalid_json"
            else:
                await self.async_set_unique_id("config")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="VServer SSH Stats", data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )
