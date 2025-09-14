"""Config flow for VServer SSH Stats."""
from __future__ import annotations

from typing import Any
import json

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from . import DOMAIN
from .ssh_discovery import discover_ssh_hosts, guess_local_network

DEFAULT_INTERVAL = 30


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for VServer SSH Stats."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""
        if user_input is not None:
            if not user_input.get("password") and not user_input.get("key"):
                hosts = await self._get_discovered_hosts()
                return self.async_show_form(
                    step_id="user",
                    data_schema=self._build_schema(hosts),
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

        hosts = await self._get_discovered_hosts()
        return self.async_show_form(step_id="user", data_schema=self._build_schema(hosts))

    async def _get_discovered_hosts(self) -> list[str]:
        """Return a list of hosts with an open SSH port."""
        network = guess_local_network()
        try:
            return await discover_ssh_hosts(network)
        except OSError:
            # If discovery fails, fall back to manual entry
            return []

    def _build_schema(self, hosts: list[str]) -> vol.Schema:
        """Create the data schema for the form using *hosts* if provided."""
        host_field: Any
        default_host: Any
        if hosts:
            host_field = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[selector.SelectOptionDict(value=h, label=h) for h in hosts],
                    custom_value=True,
                )
            )
            default_host = hosts[0]
        else:
            host_field = str
            default_host = vol.UNDEFINED
        return vol.Schema(
            {
                vol.Required("interval", default=DEFAULT_INTERVAL): int,
                vol.Required("name"): str,
                vol.Required("host", default=default_host): host_field,
                vol.Required("username"): str,
                vol.Optional("password"): str,
                vol.Optional("key"): str,
            }
        )
