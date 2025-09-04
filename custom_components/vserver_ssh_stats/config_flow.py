"""Config flow for VServer SSH Stats."""
from __future__ import annotations

from typing import Any
import json

import voluptuous as vol
from homeassistant import config_entries

from . import DOMAIN

DEFAULT_INTERVAL = 30

DATA_SCHEMA = vol.Schema(
    {
        vol.Required("interval", default=DEFAULT_INTERVAL): int,
        vol.Required("name"): str,
        vol.Required("host"): str,
        vol.Required("username"): str,
        vol.Required("password"): str,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for VServer SSH Stats."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""
        if user_input is not None:
            server = {
                "name": user_input["name"],
                "host": user_input["host"],
                "username": user_input["username"],
                "password": user_input["password"],
            }
            data = {
                "interval": user_input["interval"],
                "servers_json": json.dumps([server]),
            }
            await self.async_set_unique_id("config")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="VServer SSH Stats", data=data)

        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA)
