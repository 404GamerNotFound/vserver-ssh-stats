"""Config flow for VServer SSH Stats."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import DOMAIN
from .ssh_discovery import discover_ssh_hosts, guess_local_network
from .ssh_security import parse_host_key_fingerprints
from .util import (
    DEFAULT_COMMAND_ALLOWLIST,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_CUSTOM_SENSOR_INTERVAL,
    DEFAULT_CUSTOM_SENSOR_TIMEOUT,
    DEFAULT_DOCKER_INTERVAL,
    DEFAULT_INTERVAL,
    DEFAULT_PACKAGE_INTERVAL,
    DEFAULT_SLOW_COMMAND_TIMEOUT,
    DEFAULT_STORAGE_INTERVAL,
    MIN_CUSTOM_SENSOR_INTERVAL,
    parse_monitored_ports,
    resolve_private_key_path,
)


def _coerce_positive_int(value: Any, default: int) -> int:
    """Return *value* as a positive integer or *default* when invalid."""

    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _coerce_nonnegative_int(value: Any, default: int) -> int:
    """Return *value* as a non-negative integer or *default* when invalid."""

    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


def _number_box(min_value: int = 1, max_value: int | None = None) -> selector.NumberSelector:
    """Create a consistent numeric box selector."""

    config: dict[str, Any] = {
        "min": min_value,
        "step": 1,
        "mode": selector.NumberSelectorMode.BOX,
    }
    if max_value is not None:
        config["max"] = max_value
    return selector.NumberSelector(selector.NumberSelectorConfig(**config))


def _password_selector() -> Any:
    """Return a password selector when supported by the Home Assistant version."""

    text_selector = getattr(selector, "TextSelector", None)
    text_selector_config = getattr(selector, "TextSelectorConfig", None)
    text_selector_type = getattr(selector, "TextSelectorType", None)
    password_type = getattr(text_selector_type, "PASSWORD", None)
    if text_selector and text_selector_config and password_type:
        return text_selector(text_selector_config(type=password_type))
    return str


def _textarea_selector() -> Any:
    """Return a multiline text selector when supported by the Home Assistant version."""

    text_selector = getattr(selector, "TextSelector", None)
    text_selector_config = getattr(selector, "TextSelectorConfig", None)
    text_selector_type = getattr(selector, "TextSelectorType", None)
    text_type = getattr(text_selector_type, "TEXT", None)
    if text_selector and text_selector_config and text_type:
        try:
            return text_selector(text_selector_config(type=text_type, multiline=True))
        except TypeError:
            return text_selector(text_selector_config(type=text_type))
    return str


def _format_monitored_ports(value: object) -> str:
    """Return monitored ports formatted for text input."""

    try:
        ports = parse_monitored_ports(value)
    except ValueError:
        return str(value or "")
    return ", ".join(str(port) for port in ports)


def _format_host_key_fingerprints(value: object) -> str:
    """Return configured host-key fingerprints formatted for text input."""

    try:
        fingerprints = parse_host_key_fingerprints(value)
    except ValueError:
        return str(value or "")
    return "\n".join(fingerprints)


def _build_server_schema(
    hosts: list[str],
    include_interval: bool,
    interval_default: int,
    default_name: Any,
    defaults: dict[str, Any] | None = None,
    *,
    include_add_another: bool = True,
    editing_existing: bool = False,
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
        schema[vol.Required("interval", default=defaults.get("interval", interval_default))] = _number_box()

    schema[vol.Required("name", default=defaults.get("name", default_name))] = vol.All(
        str, vol.Length(min=1)
    )
    schema[vol.Required("host", default=defaults.get("host", default_host))] = host_field
    schema[vol.Required("port", default=defaults.get("port", 22))] = vol.All(
        vol.Coerce(int), vol.Range(min=1, max=65535)
    )
    schema[
        vol.Required(
            "host_key_fingerprints",
            default=_format_host_key_fingerprints(
                defaults.get("host_key_fingerprints", "")
            ),
        )
    ] = _textarea_selector()
    schema[vol.Required("username", default=defaults.get("username", vol.UNDEFINED))] = vol.All(
        str, vol.Length(min=1)
    )
    schema[vol.Optional("target_os", default=defaults.get("target_os", "auto"))] = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value="auto", label="Auto-detect"),
                selector.SelectOptionDict(value="debian", label="Debian/Ubuntu/Linux"),
                selector.SelectOptionDict(value="raspbian", label="Raspberry Pi OS"),
                selector.SelectOptionDict(value="windows", label="Windows (experimental)"),
            ],
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )
    schema[
        vol.Optional(
            "monitored_ports",
            default=_format_monitored_ports(defaults.get("monitored_ports", "")),
        )
    ] = _textarea_selector()
    schema[vol.Optional("password")] = _password_selector()
    if editing_existing:
        schema[vol.Optional("clear_password", default=defaults.get("clear_password", False))] = bool
        schema[vol.Optional("key", default=defaults.get("key", ""))] = str
        schema[vol.Optional("clear_key", default=defaults.get("clear_key", False))] = bool
    else:
        schema[vol.Optional("key", default=defaults.get("key", ""))] = str
    if include_add_another:
        schema[vol.Optional("add_another", default=defaults.get("add_another", False))] = bool
    return vol.Schema(schema)


def _build_options_schema(
    interval: int,
    connect_timeout: int,
    command_timeout: int,
    package_interval: int,
    docker_interval: int,
    storage_interval: int,
    slow_command_timeout: int,
    command_allowlist: str,
) -> vol.Schema:
    """Create the top-level options schema."""

    return vol.Schema(
        {
            vol.Required("interval", default=interval): _number_box(),
            vol.Required("connect_timeout", default=connect_timeout): _number_box(max_value=300),
            vol.Required("command_timeout", default=command_timeout): _number_box(max_value=300),
            vol.Required("package_interval", default=package_interval): _number_box(),
            vol.Required("docker_interval", default=docker_interval): _number_box(),
            vol.Required("storage_interval", default=storage_interval): _number_box(min_value=0),
            vol.Required("slow_command_timeout", default=slow_command_timeout): _number_box(
                max_value=3600
            ),
            vol.Optional("command_allowlist", default=command_allowlist): _textarea_selector(),
            vol.Optional("edit_server", default=False): bool,
            vol.Optional("add_server", default=False): bool,
            vol.Optional("remove_server", default=False): bool,
            vol.Optional("reconfigure_servers", default=False): bool,
            vol.Optional("add_custom_sensor", default=False): bool,
            vol.Optional("edit_custom_sensor", default=False): bool,
            vol.Optional("remove_custom_sensor", default=False): bool,
        }
    )


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
                self._interval = _coerce_positive_int(user_input["interval"], DEFAULT_INTERVAL)
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
                        "target_os": user_input.get("target_os", "auto"),
                    }
                    try:
                        server["host_key_fingerprints"] = parse_host_key_fingerprints(
                            user_input.get("host_key_fingerprints")
                        )
                    except ValueError:
                        errors["host_key_fingerprints"] = "invalid_host_key_fingerprints"
                    try:
                        server["monitored_ports"] = parse_monitored_ports(
                            user_input.get("monitored_ports")
                        )
                    except ValueError:
                        errors["monitored_ports"] = "invalid_ports"
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
                            "connect_timeout": DEFAULT_CONNECT_TIMEOUT,
                            "command_timeout": DEFAULT_COMMAND_TIMEOUT,
                            "package_interval": DEFAULT_PACKAGE_INTERVAL,
                            "docker_interval": DEFAULT_DOCKER_INTERVAL,
                            "storage_interval": DEFAULT_STORAGE_INTERVAL,
                            "slow_command_timeout": DEFAULT_SLOW_COMMAND_TIMEOUT,
                            "command_allowlist": DEFAULT_COMMAND_ALLOWLIST,
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

        self._config_entry = config_entry
        self._interval = _coerce_positive_int(config_entry.data.get("interval"), DEFAULT_INTERVAL)
        self._connect_timeout = _coerce_positive_int(
            config_entry.data.get("connect_timeout"), DEFAULT_CONNECT_TIMEOUT
        )
        self._command_timeout = _coerce_positive_int(
            config_entry.data.get("command_timeout"), DEFAULT_COMMAND_TIMEOUT
        )
        self._package_interval = _coerce_positive_int(
            config_entry.data.get("package_interval"), DEFAULT_PACKAGE_INTERVAL
        )
        self._docker_interval = _coerce_positive_int(
            config_entry.data.get("docker_interval"), DEFAULT_DOCKER_INTERVAL
        )
        self._storage_interval = _coerce_nonnegative_int(
            config_entry.data.get("storage_interval"), DEFAULT_STORAGE_INTERVAL
        )
        self._slow_command_timeout = _coerce_positive_int(
            config_entry.data.get("slow_command_timeout"), DEFAULT_SLOW_COMMAND_TIMEOUT
        )
        self._command_allowlist = str(
            config_entry.data.get("command_allowlist", DEFAULT_COMMAND_ALLOWLIST)
        )
        try:
            self._existing_servers: list[dict[str, Any]] = json.loads(
                config_entry.data.get("servers_json", "[]")
            )
        except ValueError:
            self._existing_servers = []
        try:
            custom_sensors = json.loads(
                config_entry.data.get("custom_sensors_json", "[]")
            )
            self._custom_sensors: list[dict[str, Any]] = (
                custom_sensors if isinstance(custom_sensors, list) else []
            )
        except ValueError:
            self._custom_sensors = []
        self._pending_servers: list[dict[str, Any]] = []
        self._selected_server_index: int | None = None
        self._selected_custom_sensor_index: int | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage the initial options step."""

        errors: dict[str, str] = {}
        if user_input is not None:
            self._apply_common_options(user_input)
            actions = [
                action
                for action in (
                    "edit_server",
                    "add_server",
                    "remove_server",
                    "reconfigure_servers",
                    "add_custom_sensor",
                    "edit_custom_sensor",
                    "remove_custom_sensor",
                )
                if user_input.get(action)
            ]
            if len(actions) > 1:
                errors["base"] = "single_action"
            elif actions == ["edit_server"]:
                if not self._existing_servers:
                    errors["base"] = "no_servers"
                else:
                    return self.async_show_form(
                        step_id="select_server",
                        data_schema=self._build_server_selector_schema(),
                    )
            elif actions == ["add_server"]:
                self._pending_servers = []
                return await self.async_step_add_server()
            elif actions == ["remove_server"]:
                if len(self._existing_servers) <= 1:
                    errors["base"] = "cannot_remove_last_server"
                else:
                    return self.async_show_form(
                        step_id="remove_server",
                        data_schema=self._remove_server_form_schema(),
                    )
            elif actions == ["reconfigure_servers"]:
                self._pending_servers = []
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
            elif actions == ["add_custom_sensor"]:
                return await self.async_step_add_custom_sensor()
            elif actions == ["edit_custom_sensor"]:
                if not self._custom_sensors:
                    errors["base"] = "no_custom_sensors"
                else:
                    return self.async_show_form(
                        step_id="select_custom_sensor",
                        data_schema=self._build_custom_sensor_selector_schema(),
                    )
            elif actions == ["remove_custom_sensor"]:
                if not self._custom_sensors:
                    errors["base"] = "no_custom_sensors"
                else:
                    return self.async_show_form(
                        step_id="remove_custom_sensor",
                        data_schema=self._remove_custom_sensor_form_schema(),
                    )
            else:
                self._update_entry(self._existing_servers)
                return self.async_create_entry(title="", data={})

        return self._show_init_form(errors)

    def _show_init_form(self, errors: dict[str, str] | None = None):
        """Return the top-level options form."""

        return self.async_show_form(
            step_id="init",
            data_schema=_build_options_schema(
                self._interval,
                self._connect_timeout,
                self._command_timeout,
                self._package_interval,
                self._docker_interval,
                self._storage_interval,
                self._slow_command_timeout,
                self._command_allowlist,
            ),
            errors=errors or {},
        )

    def _apply_common_options(self, user_input: dict[str, Any]) -> None:
        """Store common options selected on the first options step."""

        self._interval = _coerce_positive_int(user_input.get("interval"), DEFAULT_INTERVAL)
        self._connect_timeout = _coerce_positive_int(
            user_input.get("connect_timeout"), DEFAULT_CONNECT_TIMEOUT
        )
        self._command_timeout = _coerce_positive_int(
            user_input.get("command_timeout"), DEFAULT_COMMAND_TIMEOUT
        )
        self._package_interval = _coerce_positive_int(
            user_input.get("package_interval"), DEFAULT_PACKAGE_INTERVAL
        )
        self._docker_interval = _coerce_positive_int(
            user_input.get("docker_interval"), DEFAULT_DOCKER_INTERVAL
        )
        self._storage_interval = _coerce_nonnegative_int(
            user_input.get("storage_interval"), DEFAULT_STORAGE_INTERVAL
        )
        self._slow_command_timeout = _coerce_positive_int(
            user_input.get("slow_command_timeout"), DEFAULT_SLOW_COMMAND_TIMEOUT
        )
        self._command_allowlist = str(
            user_input.get("command_allowlist", DEFAULT_COMMAND_ALLOWLIST)
        )

    def _server_select_options(self) -> list[selector.SelectOptionDict]:
        """Return selector options for all configured servers."""

        return [
            selector.SelectOptionDict(
                value=str(index),
                label=f"{server.get('name') or server.get('host')} ({server.get('host')}:{server.get('port', 22)})",
            )
            for index, server in enumerate(self._existing_servers)
        ]

    def _server_select_selector(self) -> selector.SelectSelector:
        """Return a selector for choosing a configured server."""

        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=self._server_select_options(),
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

    def _server_host_select_selector(self) -> selector.SelectSelector:
        """Return a selector whose values are stable server hosts."""

        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(
                        value=str(server["host"]),
                        label=f"{server.get('name') or server['host']} ({server['host']})",
                    )
                    for server in self._existing_servers
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

    def _custom_sensor_select_options(self) -> list[selector.SelectOptionDict]:
        """Return selector options for all custom sensors."""

        server_names = {
            server.get("host"): server.get("name") or server.get("host")
            for server in self._existing_servers
        }
        return [
            selector.SelectOptionDict(
                value=str(index),
                label=(
                    f"{definition.get('name')} "
                    f"({server_names.get(definition.get('server_host'), definition.get('server_host'))})"
                ),
            )
            for index, definition in enumerate(self._custom_sensors)
        ]

    def _build_custom_sensor_selector_schema(self) -> vol.Schema:
        """Create a schema for selecting one custom sensor."""

        options = self._custom_sensor_select_options()
        return vol.Schema(
            {
                vol.Required(
                    "custom_sensor",
                    default=options[0]["value"] if options else vol.UNDEFINED,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

    def _remove_custom_sensor_form_schema(self) -> vol.Schema:
        """Create a form for selecting and confirming custom sensor removal."""

        schema = dict(self._build_custom_sensor_selector_schema().schema)
        schema[vol.Optional("confirm_remove", default=False)] = bool
        return vol.Schema(schema)

    def _custom_sensor_form_schema(
        self,
        defaults: dict[str, Any] | None = None,
    ) -> vol.Schema:
        """Create the add/edit form for one custom command sensor."""

        defaults = defaults or {}
        first_host = (
            self._existing_servers[0]["host"] if self._existing_servers else vol.UNDEFINED
        )
        return vol.Schema(
            {
                vol.Required("name", default=defaults.get("name", vol.UNDEFINED)): vol.All(
                    str, vol.Strip(), vol.Length(min=1, max=100)
                ),
                vol.Required(
                    "server_host", default=defaults.get("server_host", first_host)
                ): self._server_host_select_selector(),
                vol.Required(
                    "command", default=defaults.get("command", vol.UNDEFINED)
                ): vol.All(str, vol.Strip(), vol.Length(min=1, max=4096)),
                vol.Required(
                    "interval",
                    default=defaults.get("interval", DEFAULT_CUSTOM_SENSOR_INTERVAL),
                ): _number_box(min_value=MIN_CUSTOM_SENSOR_INTERVAL),
                vol.Required(
                    "timeout",
                    default=defaults.get("timeout", DEFAULT_CUSTOM_SENSOR_TIMEOUT),
                ): _number_box(max_value=3600),
            }
        )

    def _build_server_selector_schema(self) -> vol.Schema:
        """Create a schema for selecting one configured server."""

        options = self._server_select_options()
        return vol.Schema(
            {
                vol.Required("server", default=options[0]["value"] if options else vol.UNDEFINED): (
                    self._server_select_selector()
                )
            }
        )

    def _remove_server_form_schema(self) -> vol.Schema:
        """Create a schema for removing one configured server."""

        options = self._server_select_options()
        return vol.Schema(
            {
                vol.Required("server", default=options[0]["value"] if options else vol.UNDEFINED): (
                    self._server_select_selector()
                ),
                vol.Optional("confirm_remove", default=False): bool,
            }
        )

    async def async_step_select_server(self, user_input: dict[str, Any] | None = None):
        """Select the server to edit."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._selected_server_index = int(user_input["server"])
                self._existing_servers[self._selected_server_index]
                return await self.async_step_edit_server()
            except (KeyError, TypeError, ValueError, IndexError):
                errors["base"] = "no_servers"

        return self.async_show_form(
            step_id="select_server",
            data_schema=self._build_server_selector_schema(),
            errors=errors,
        )

    async def async_step_edit_server(self, user_input: dict[str, Any] | None = None):
        """Edit an existing server in place."""

        if self._selected_server_index is None:
            return await self.async_step_select_server()

        try:
            current = self._existing_servers[self._selected_server_index]
        except IndexError:
            self._selected_server_index = None
            return await self.async_step_select_server()

        errors: dict[str, str] = {}
        defaults = dict(current)
        defaults.pop("password", None)
        if user_input is not None:
            defaults.update(user_input)
            server = self._server_from_input(
                user_input,
                errors,
                existing=current,
                ignore_index=self._selected_server_index,
            )
            if server is not None and not errors:
                old_host = current.get("host")
                if old_host != server.get("host"):
                    for definition in self._custom_sensors:
                        if definition.get("server_host") == old_host:
                            definition["server_host"] = server["host"]
                self._existing_servers[self._selected_server_index] = server
                self._update_entry(self._existing_servers)
                return self.async_create_entry(title="", data={})

        hosts = await self._get_discovered_hosts()
        return self.async_show_form(
            step_id="edit_server",
            data_schema=_build_server_schema(
                hosts,
                include_interval=False,
                interval_default=self._interval,
                default_name=current.get("name", vol.UNDEFINED),
                defaults=defaults,
                include_add_another=False,
                editing_existing=True,
            ),
            errors=errors,
        )

    async def async_step_select_custom_sensor(
        self, user_input: dict[str, Any] | None = None
    ):
        """Select one custom sensor to edit."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._selected_custom_sensor_index = int(user_input["custom_sensor"])
                self._custom_sensors[self._selected_custom_sensor_index]
                return await self.async_step_edit_custom_sensor()
            except (KeyError, TypeError, ValueError, IndexError):
                errors["base"] = "no_custom_sensors"
        return self.async_show_form(
            step_id="select_custom_sensor",
            data_schema=self._build_custom_sensor_selector_schema(),
            errors=errors,
        )

    async def async_step_add_custom_sensor(
        self, user_input: dict[str, Any] | None = None
    ):
        """Add one scheduled custom command sensor."""

        errors: dict[str, str] = {}
        defaults = user_input or {}
        if user_input is not None:
            definition = self._custom_sensor_from_input(user_input, errors)
            if definition is not None:
                definition["id"] = uuid.uuid4().hex
                self._custom_sensors.append(definition)
                self._update_entry(self._existing_servers)
                return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="add_custom_sensor",
            data_schema=self._custom_sensor_form_schema(defaults),
            errors=errors,
        )

    async def async_step_edit_custom_sensor(
        self, user_input: dict[str, Any] | None = None
    ):
        """Edit one scheduled custom command sensor."""

        if self._selected_custom_sensor_index is None:
            return await self.async_step_select_custom_sensor()
        try:
            current = self._custom_sensors[self._selected_custom_sensor_index]
        except IndexError:
            self._selected_custom_sensor_index = None
            return await self.async_step_select_custom_sensor()

        errors: dict[str, str] = {}
        defaults = dict(current)
        if user_input is not None:
            defaults.update(user_input)
            definition = self._custom_sensor_from_input(
                user_input,
                errors,
                existing=current,
                ignore_index=self._selected_custom_sensor_index,
            )
            if definition is not None:
                self._custom_sensors[self._selected_custom_sensor_index] = definition
                self._update_entry(self._existing_servers)
                return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="edit_custom_sensor",
            data_schema=self._custom_sensor_form_schema(defaults),
            errors=errors,
        )

    async def async_step_remove_custom_sensor(
        self, user_input: dict[str, Any] | None = None
    ):
        """Remove one configured custom command sensor."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                index = int(user_input["custom_sensor"])
                self._custom_sensors[index]
            except (KeyError, TypeError, ValueError, IndexError):
                errors["base"] = "no_custom_sensors"
            else:
                if not user_input.get("confirm_remove"):
                    errors["base"] = "confirm_remove"
                else:
                    self._custom_sensors.pop(index)
                    self._update_entry(self._existing_servers)
                    return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="remove_custom_sensor",
            data_schema=self._remove_custom_sensor_form_schema(),
            errors=errors,
        )

    def _custom_sensor_from_input(
        self,
        user_input: dict[str, Any],
        errors: dict[str, str],
        *,
        existing: dict[str, Any] | None = None,
        ignore_index: int | None = None,
    ) -> dict[str, Any] | None:
        """Validate and normalize one custom command sensor definition."""

        name = str(user_input["name"]).strip()
        server_host = str(user_input["server_host"])
        if server_host not in {server.get("host") for server in self._existing_servers}:
            errors["server_host"] = "no_servers"
        if any(
            index != ignore_index
            and definition.get("server_host") == server_host
            and str(definition.get("name", "")).casefold() == name.casefold()
            for index, definition in enumerate(self._custom_sensors)
        ):
            errors["name"] = "duplicate_custom_sensor"
        if errors:
            return None
        return {
            "id": (existing or {}).get("id"),
            "name": name,
            "server_host": server_host,
            "command": str(user_input["command"]).strip(),
            "interval": max(MIN_CUSTOM_SENSOR_INTERVAL, int(user_input["interval"])),
            "timeout": max(1, min(3600, int(user_input["timeout"]))),
        }

    async def async_step_add_server(self, user_input: dict[str, Any] | None = None):
        """Append one or more servers to the current entry."""

        errors: dict[str, str] = {}
        defaults = user_input or {}
        if user_input is not None:
            server = self._server_from_input(
                user_input,
                errors,
                pending_servers=self._pending_servers,
            )
            if server is not None and not errors:
                self._pending_servers.append(server)
                if user_input.get("add_another"):
                    hosts = await self._get_discovered_hosts()
                    return self.async_show_form(
                        step_id="add_server",
                        data_schema=_build_server_schema(
                            hosts,
                            include_interval=False,
                            interval_default=self._interval,
                            default_name=vol.UNDEFINED,
                        ),
                    )

                self._update_entry([*self._existing_servers, *self._pending_servers])
                return self.async_create_entry(title="", data={})

        hosts = await self._get_discovered_hosts()
        return self.async_show_form(
            step_id="add_server",
            data_schema=_build_server_schema(
                hosts,
                include_interval=False,
                interval_default=self._interval,
                default_name=vol.UNDEFINED,
                defaults=defaults,
            ),
            errors=errors,
        )

    async def async_step_remove_server(self, user_input: dict[str, Any] | None = None):
        """Remove one configured server."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                index = int(user_input["server"])
                self._existing_servers[index]
            except (KeyError, TypeError, ValueError, IndexError):
                errors["base"] = "no_servers"
            else:
                if not user_input.get("confirm_remove"):
                    errors["base"] = "confirm_remove"
                elif len(self._existing_servers) <= 1:
                    errors["base"] = "cannot_remove_last_server"
                else:
                    servers = [
                        server
                        for current_index, server in enumerate(self._existing_servers)
                        if current_index != index
                    ]
                    self._update_entry(servers)
                    return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="remove_server",
            data_schema=self._remove_server_form_schema(),
            errors=errors,
        )

    def _server_from_input(
        self,
        user_input: dict[str, Any],
        errors: dict[str, str],
        *,
        existing: dict[str, Any] | None = None,
        ignore_index: int | None = None,
        pending_servers: list[dict[str, Any]] | None = None,
        check_current_entry: bool = True,
    ) -> dict[str, Any] | None:
        """Validate server form input and return a normalized server definition."""

        host = str(user_input["host"]).strip()
        current_servers = []
        if check_current_entry:
            current_servers = [
                server
                for index, server in enumerate(self._existing_servers)
                if ignore_index is None or index != ignore_index
            ]
        if any(server.get("host") == host for server in current_servers):
            errors["host"] = "duplicate_host"
        elif pending_servers and any(server.get("host") == host for server in pending_servers):
            errors["host"] = "duplicate_host"
        elif self._host_already_configured(host):
            errors["host"] = "host_in_use"

        server = dict(existing or {})
        server.update(
            {
                "name": str(user_input["name"]).strip(),
                "host": host,
                "username": str(user_input["username"]).strip(),
                "port": user_input["port"],
                "target_os": user_input.get("target_os", "auto"),
            }
        )
        try:
            server["host_key_fingerprints"] = parse_host_key_fingerprints(
                user_input.get("host_key_fingerprints")
            )
        except ValueError:
            errors["host_key_fingerprints"] = "invalid_host_key_fingerprints"
        try:
            server["monitored_ports"] = parse_monitored_ports(
                user_input.get("monitored_ports")
            )
        except ValueError:
            errors["monitored_ports"] = "invalid_ports"

        password = user_input.get("password")
        if user_input.get("clear_password"):
            server.pop("password", None)
        elif password:
            server["password"] = password
        elif existing is None:
            server.pop("password", None)

        key_input = user_input.get("key")
        key = key_input.strip() if isinstance(key_input, str) else key_input
        if user_input.get("clear_key"):
            server.pop("key", None)
        elif key:
            resolved = resolve_private_key_path(self.hass, key)
            if not Path(resolved).exists():
                errors["key"] = "key_missing"
            else:
                server["key"] = resolved
        elif existing is None:
            server.pop("key", None)

        if not server.get("password") and not server.get("key"):
            errors["base"] = "auth"

        return None if errors else server

    async def async_step_servers(self, user_input: dict[str, Any] | None = None):
        """Collect replacement server information during options flow."""

        errors: dict[str, str] = {}
        defaults = user_input or {}

        if user_input is not None:
            server = self._server_from_input(
                user_input,
                errors,
                pending_servers=self._pending_servers,
                check_current_entry=False,
            )
            if server is not None and not errors:
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

        valid_hosts = {server.get("host") for server in servers}
        self._custom_sensors = [
            definition
            for definition in self._custom_sensors
            if definition.get("server_host") in valid_hosts
        ]
        data = {
            "interval": self._interval,
            "connect_timeout": self._connect_timeout,
            "command_timeout": self._command_timeout,
            "package_interval": self._package_interval,
            "docker_interval": self._docker_interval,
            "storage_interval": self._storage_interval,
            "slow_command_timeout": self._slow_command_timeout,
            "command_allowlist": self._command_allowlist,
            "servers_json": json.dumps(servers),
            "custom_sensors_json": json.dumps(self._custom_sensors),
        }
        title = servers[0]["name"] if len(servers) == 1 else "VServer SSH Stats"
        self.hass.config_entries.async_update_entry(self._config_entry, title=title, data=data)
        self.hass.async_create_task(
            self.hass.config_entries.async_reload(self._config_entry.entry_id)
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
            if entry.entry_id == self._config_entry.entry_id:
                continue
            try:
                servers = json.loads(entry.data.get("servers_json", "[]"))
            except ValueError:
                continue
            if any(server.get("host") == host for server in servers):
                return True
        return False
