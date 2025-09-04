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
        vol.Optional("password"): str,
        vol.Optional("key"): str,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for VServer SSH Stats."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""
        if user_input is not None:
            if not user_input.get("password") and not user_input.get("key"):
                return self.async_show_form(
                    step_id="user",
                    data_schema=DATA_SCHEMA,
                    errors={"base": "auth"},
                )
            server: dict[str, Any] = {
                "name": user_input["name"],
                "host": user_input["host"],
                "username": user_input["username"],
            }
            if user_input.get("password"):
                server["password"] = user_input["password"]
            if user_input.get("key"):
                server["key"] = user_input["key"]
            data = {
                "interval": user_input["interval"],
                "servers_json": json.dumps([server]),
            }
            await self.async_set_unique_id(server["host"])  # Prevent duplicate hosts
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=server["name"], data=data)

        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA)
