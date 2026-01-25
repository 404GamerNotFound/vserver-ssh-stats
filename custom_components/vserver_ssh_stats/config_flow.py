"""Config flow for VServer SSH Stats."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.config_entries import ConfigEntry, OptionsFlow

from . import DOMAIN
from .ssh_discovery import discover_ssh_hosts, guess_local_network
from .util import resolve_private_key_path

DEFAULT_INTERVAL = 30


def _build_server_schema(
    hosts: list[str],
    include_interval: bool,
    interval_default: int,
    default_name: Any,
    defaults: dict[str, Any] | None = None,
) -> vol.Schema:
    """Create the data schema for a single server entry."""

    defaults = defaults or {}
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

    schema: dict[Any, Any] = {}
    if include_interval:
        schema[vol.Required("interval", default=defaults.get("interval", interval_default))] = (
            selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, step=1, mode=selector.NumberSelectorMode.BOX)
            )
        )

    schema[vol.Required("name", default=defaults.get("name", default_name))] = str
    schema[vol.Required("host", default=defaults.get("host", default_host))] = host_field
    schema[vol.Required("port", default=defaults.get("port", 22))] = vol.All(
        vol.Coerce(int), vol.Range(min=1, max=65535)
    )
    schema[vol.Required("username", default=defaults.get("username", vol.UNDEFINED))] = str
    schema[vol.Optional("password")] = str
    schema[vol.Optional("key")] = str
    schema[vol.Optional("add_another", default=defaults.get("add_another", False))] = bool
    return vol.Schema(schema)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for VServer SSH Stats."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_host: str | None = None
        self._discovered_name: str | None = None
        self._servers: list[dict[str, Any]] = []
        self._interval: int = DEFAULT_INTERVAL

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""

        errors: dict[str, str] = {}
        defaults = user_input or {}
        first_server = not self._servers

        if user_input is not None:
            if first_server:
                self._interval = user_input["interval"]
            if not user_input.get("password") and not user_input.get("key"):
                errors["base"] = "auth"
            else:
                host = user_input["host"]
                if any(server["host"] == host for server in self._servers):
                    errors["host"] = "duplicate_host"
                elif self._host_already_configured(host):
                    errors["host"] = "host_in_use"
                else:
                    server: dict[str, Any] = {
                        "name": user_input["name"],
                        "host": host,
                        "username": user_input["username"],
                        "port": user_input["port"],
                    }
                    if user_input.get("password"):
                        server["password"] = user_input["password"]
                    key_input = user_input.get("key")
                    if key_input:
                        resolved = resolve_private_key_path(self.hass, key_input)
                        if not Path(resolved).exists():
                            errors["key"] = "key_missing"
                            defaults = user_input
                        else:
                            server["key"] = resolved
                    if errors:
                        defaults = user_input
                    else:
                        self._servers.append(server)
                        if user_input.get("add_another"):
                            hosts = await self._get_discovered_hosts()
                            return self.async_show_form(
                                step_id="user",
                                data_schema=_build_server_schema(
                                    hosts,
                                    include_interval=False,
                                    interval_default=self._interval,
                                    default_name=vol.UNDEFINED,
                                ),
                            )

                        hosts_for_id = ",".join(sorted(server["host"] for server in self._servers))
                        unique_id = hashlib.sha256(hosts_for_id.encode()).hexdigest()
                        await self.async_set_unique_id(unique_id)
                        self._abort_if_unique_id_configured()
                        data = {
                            "interval": self._interval,
                            "servers_json": json.dumps(self._servers),
                        }
                        title = (
                            self._servers[0]["name"]
                            if len(self._servers) == 1
                            else "VServer SSH Stats"
                        )
                        return self.async_create_entry(title=title, data=data)

        hosts = await self._get_discovered_hosts()
        default_name = self._discovered_name if first_server else vol.UNDEFINED
        return self.async_show_form(
            step_id="user",
            data_schema=_build_server_schema(
                hosts,
                include_interval=first_server,
                interval_default=self._interval,
                default_name=default_name,
                defaults=defaults,
            ),
            errors=errors,
        )

    async def async_step_zeroconf(self, discovery_info: Any):
        """Handle zeroconf discovery."""
        if isinstance(discovery_info, Mapping):
            host = discovery_info.get("host") or discovery_info.get("ip_address")
            name = discovery_info.get("hostname") or discovery_info.get("name") or host
        else:
            host = (
                getattr(discovery_info, "host", None)
                or getattr(discovery_info, "ip_address", None)
            )
            name = (
                getattr(discovery_info, "hostname", None)
                or getattr(discovery_info, "name", None)
                or host
            )
        if not host:
            return self.async_abort(reason="unknown")
        host = host.lower()
        await self.async_set_unique_id(host)
        self._abort_if_unique_id_configured()
        if self._host_already_configured(host):
            return self.async_abort(reason="already_configured")
        self._discovered_host = host
        self._discovered_name = name
        self.context["title_placeholders"] = {"name": self._discovered_name}
        return await self.async_step_user()

    async def _get_discovered_hosts(self) -> list[str]:
        """Return a list of hosts with an open SSH port."""
        if self._discovered_host:
            return [self._discovered_host]

        networks: list[str] = []
        try:
            # Try to use Home Assistant's network helper to get all local
            # IPv4 addresses (this includes the host network when running
            # inside the supervised container).
            from homeassistant.helpers.network import async_get_ipv4_addresses

            addresses = await async_get_ipv4_addresses(self.hass, include_loopback=False)
            networks = [f"{addr}/24" for addr in addresses]
        except Exception:  # pragma: no cover - helper not available
            networks = [guess_local_network()]

        hosts: set[str] = set()
        for network in networks:
            try:
                hosts.update(await discover_ssh_hosts(network))
            except OSError:
                # If discovery for a network fails, skip it and continue
                continue

        return sorted(hosts)

    def _host_already_configured(self, host: str) -> bool:
        """Return True if *host* is already configured in another entry."""

        for entry in self._async_current_entries():
            try:
                servers = json.loads(entry.data.get("servers_json", "[]"))
            except ValueError:
                continue
            if any(server.get("host") == host for server in servers):
                return True
        return False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler for this config entry."""

        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(OptionsFlow):
    """Handle options flow for VServer SSH Stats."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialise the options flow."""

        self.config_entry = config_entry
        self._interval: int = config_entry.data.get("interval", DEFAULT_INTERVAL)
        try:
            self._existing_servers: list[dict[str, Any]] = json.loads(
                config_entry.data.get("servers_json", "[]")
            )
        except ValueError:
            self._existing_servers = []
        for server in self._existing_servers:
            key = resolve_private_key_path(self.hass, server.get("key")) if self.hass else server.get("key")
            if key:
                server["key"] = key
        self._pending_servers: list[dict[str, Any]] = []

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage the initial options step."""

        if user_input is not None:
            self._interval = user_input["interval"]
            if user_input.get("reconfigure_servers"):
                hosts = await self._get_discovered_hosts()
                return self.async_show_form(
                    step_id="servers",
                    data_schema=_build_server_schema(
                        hosts,
                        include_interval=False,
                        interval_default=self._interval,
                        default_name=vol.UNDEFINED,
                    ),
                )

            self._update_entry(self._existing_servers)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("interval", default=self._interval): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, step=1, mode=selector.NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional("reconfigure_servers", default=False): bool,
                }
            ),
        )

    async def async_step_servers(self, user_input: dict[str, Any] | None = None):
        """Collect server information during options flow."""

        errors: dict[str, str] = {}
        defaults = user_input or {}

        if user_input is not None:
            if not user_input.get("password") and not user_input.get("key"):
                errors["base"] = "auth"
            else:
                host = user_input["host"]
                if any(server["host"] == host for server in self._pending_servers):
                    errors["host"] = "duplicate_host"
                elif self._host_already_configured(host):
                    errors["host"] = "host_in_use"
                else:
                    server: dict[str, Any] = {
                        "name": user_input["name"],
                        "host": host,
                        "username": user_input["username"],
                        "port": user_input["port"],
                    }
                    if user_input.get("password"):
                        server["password"] = user_input["password"]
                    key_input = user_input.get("key")
                    if key_input:
                        resolved = resolve_private_key_path(self.hass, key_input)
                        if not Path(resolved).exists():
                            errors["key"] = "key_missing"
                            defaults = user_input
                        else:
                            server["key"] = resolved
                    if errors:
                        defaults = user_input
                    else:
                        self._pending_servers.append(server)
                        if user_input.get("add_another"):
                            hosts = await self._get_discovered_hosts()
                            return self.async_show_form(
                                step_id="servers",
                                data_schema=_build_server_schema(
                                    hosts,
                                    include_interval=False,
                                    interval_default=self._interval,
                                    default_name=vol.UNDEFINED,
                                ),
                            )

                        self._update_entry(self._pending_servers)
                        return self.async_create_entry(title="", data={})

        hosts = await self._get_discovered_hosts()
        return self.async_show_form(
            step_id="servers",
            data_schema=_build_server_schema(
                hosts,
                include_interval=False,
                interval_default=self._interval,
                default_name=vol.UNDEFINED,
                defaults=defaults,
            ),
            errors=errors,
        )

    def _update_entry(self, servers: list[dict[str, Any]]) -> None:
        """Persist updated configuration back to the config entry."""

        data = {
            "interval": self._interval,
            "servers_json": json.dumps(servers),
        }
        self.hass.config_entries.async_update_entry(self.config_entry, data=data)
        self.hass.async_create_task(
            self.hass.config_entries.async_reload(self.config_entry.entry_id)
        )

    async def _get_discovered_hosts(self) -> list[str]:
        """Return a list of hosts with an open SSH port."""

        networks: list[str] = []
        try:
            from homeassistant.helpers.network import async_get_ipv4_addresses

            addresses = await async_get_ipv4_addresses(self.hass, include_loopback=False)
            networks = [f"{addr}/24" for addr in addresses]
        except Exception:  # pragma: no cover - helper not available
            networks = [guess_local_network()]

        hosts: set[str] = set()
        for network in networks:
            try:
                hosts.update(await discover_ssh_hosts(network))
            except OSError:
                continue

        return sorted(hosts)

    def _host_already_configured(self, host: str) -> bool:
        """Return True if *host* is already configured in another entry."""

        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id == self.config_entry.entry_id:
                continue
            try:
                servers = json.loads(entry.data.get("servers_json", "[]"))
            except ValueError:
                continue
            if any(server.get("host") == host for server in servers):
                return True
        return False
