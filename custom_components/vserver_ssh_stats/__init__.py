"""VServer SSH Stats integration for Home Assistant."""
from __future__ import annotations

import asyncio
import logging
import socket
import json

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

import voluptuous as vol
import paramiko

from .util import resolve_private_key_path
_LOGGER = logging.getLogger(__name__)

DOMAIN = "vserver_ssh_stats"
PLATFORMS: list[str] = ["sensor", "button"]

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


def _get_local_ip() -> str:
    """Return the local IP address of the host."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        ip = sock.getsockname()[0]
    except Exception:  # pragma: no cover - best effort
        ip = "127.0.0.1"
    finally:
        sock.close()
    return ip


async def _async_get_local_ip() -> str:
    """Async wrapper to get the local IP."""
    return await asyncio.to_thread(_get_local_ip)


async def _async_get_uptime() -> float:
    """Return system uptime in seconds."""
    def _read_uptime() -> float:
        with open("/proc/uptime", "r", encoding="utf-8") as f:
            return float(f.readline().split()[0])

    return await asyncio.to_thread(_read_uptime)


async def _async_list_ssh_connections() -> list[str]:
    """Return a list of IPs with active SSH sessions."""
    proc = await asyncio.create_subprocess_exec(
        "who",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode().strip())
    connections: list[str] = []
    for line in stdout.decode().splitlines():
        if "(" in line and ")" in line:
            ip = line.split("(")[-1].split(")")[0]
            connections.append(ip)
    return connections


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the VServer SSH Stats integration."""
    _LOGGER.debug("Setting up VServer SSH Stats")

    async def handle_get_local_ip(call: ServiceCall) -> None:
        """Handle service call to fetch the local IP."""
        try:
            ip = await _async_get_local_ip()
        except Exception as err:  # pragma: no cover - best effort
            _LOGGER.error("Failed to fetch local IP: %s", err)
            ip = ""
        hass.bus.async_fire(f"{DOMAIN}_local_ip", {"ip": ip})

    async def handle_get_uptime(call: ServiceCall) -> None:
        """Handle service call to fetch system uptime."""
        try:
            uptime = await _async_get_uptime()
        except Exception as err:  # pragma: no cover - best effort
            _LOGGER.error("Failed to fetch uptime: %s", err)
            uptime = 0.0
        hass.bus.async_fire(f"{DOMAIN}_uptime", {"uptime": uptime})

    async def handle_list_connections(call: ServiceCall) -> None:
        """Handle service call to list active SSH connections."""
        try:
            connections = await _async_list_ssh_connections()
        except Exception as err:  # pragma: no cover - command best effort
            _LOGGER.error("Listing SSH connections failed: %s", err)
            connections = []
        hass.bus.async_fire(f"{DOMAIN}_connections", {"connections": connections})

    async def handle_run_command(call: ServiceCall) -> None:
        """Execute an arbitrary command on a server via SSH."""
        data = call.data

        def _exec_cmd() -> str:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            connect_args = {
                "hostname": data["host"],
                "username": data["username"],
                "port": data.get("port", 22),
                "password": data.get("password"),
            }
            key = resolve_private_key_path(hass, data.get("key"))
            if key:
                connect_args["key_filename"] = key
            client.connect(**{k: v for k, v in connect_args.items() if v})
            _, stdout, stderr = client.exec_command(data["command"])
            output = stdout.read().decode() + stderr.read().decode()
            client.close()
            return output

        try:
            output = await asyncio.to_thread(_exec_cmd)
        except Exception as err:  # pragma: no cover - best effort
            _LOGGER.error("Command execution failed: %s", err)
            output = ""
        hass.bus.async_fire(f"{DOMAIN}_command", {"output": output})

    async def handle_update_packages(call: ServiceCall) -> None:
        """Update packages on a server via SSH."""
        data = call.data

        def _exec_update() -> str:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            connect_args = {
                "hostname": data["host"],
                "username": data["username"],
                "port": data.get("port", 22),
                "password": data.get("password"),
            }
            key = resolve_private_key_path(hass, data.get("key"))
            if key:
                connect_args["key_filename"] = key
            client.connect(**{k: v for k, v in connect_args.items() if v})
            cmd = (
                "if command -v apt-get >/dev/null 2>&1; then "
                "sudo apt-get update && sudo apt-get -y upgrade; "
                "elif command -v dnf >/dev/null 2>&1; then "
                "sudo dnf -y upgrade; "
                "elif command -v yum >/dev/null 2>&1; then "
                "sudo yum -y update; "
                "else echo 'No supported package manager found'; fi"
            )
            _, stdout, stderr = client.exec_command(cmd)
            output = stdout.read().decode() + stderr.read().decode()
            client.close()
            return output

        try:
            output = await asyncio.to_thread(_exec_update)
        except Exception as err:  # pragma: no cover - best effort
            _LOGGER.error("Package update failed: %s", err)
            output = ""
        hass.bus.async_fire(f"{DOMAIN}_update_packages", {"output": output})

    async def handle_reboot_host(call: ServiceCall) -> None:
        """Reboot a server via SSH."""
        data = call.data

        def _exec_reboot() -> str:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            connect_args = {
                "hostname": data["host"],
                "username": data["username"],
                "port": data.get("port", 22),
                "password": data.get("password"),
            }
            key = resolve_private_key_path(hass, data.get("key"))
            if key:
                connect_args["key_filename"] = key
            client.connect(**{k: v for k, v in connect_args.items() if v})
            try:
                client.exec_command("sudo reboot &")
            finally:
                client.close()
            return "reboot triggered"

        try:
            output = await asyncio.to_thread(_exec_reboot)
        except Exception as err:  # pragma: no cover - best effort
            _LOGGER.error("Reboot failed: %s", err)
            output = ""
        hass.bus.async_fire(f"{DOMAIN}_reboot", {"output": output})

    hass.services.async_register(DOMAIN, "get_local_ip", handle_get_local_ip)
    hass.services.async_register(DOMAIN, "get_uptime", handle_get_uptime)
    hass.services.async_register(DOMAIN, "list_connections", handle_list_connections)
    hass.services.async_register(
        DOMAIN,
        "run_command",
        handle_run_command,
        schema=vol.Schema(
            {
                vol.Required("host"): cv.string,
                vol.Required("username"): cv.string,
                vol.Required("command"): cv.string,
                vol.Optional("password"): cv.string,
                vol.Optional("key"): cv.string,
                vol.Optional("port", default=22): cv.port,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "update_packages",
        handle_update_packages,
        schema=vol.Schema(
            {
                vol.Required("host"): cv.string,
                vol.Required("username"): cv.string,
                vol.Optional("password"): cv.string,
                vol.Optional("key"): cv.string,
                vol.Optional("port", default=22): cv.port,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "reboot_host",
        handle_reboot_host,
        schema=vol.Schema(
            {
                vol.Required("host"): cv.string,
                vol.Required("username"): cv.string,
                vol.Optional("password"): cv.string,
                vol.Optional("key"): cv.string,
                vol.Optional("port", default=22): cv.port,
            }
        ),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up VServer SSH Stats from a config entry."""
    _LOGGER.debug("Setting up VServer SSH Stats entry")
    data = entry.data
    hass.data.setdefault(DOMAIN, {})
    try:
        servers = json.loads(data.get("servers_json", "[]"))
    except ValueError:  # pragma: no cover - validation handled in flow
        servers = []
    for server in servers:
        key = resolve_private_key_path(hass, server.get("key"))
        if key:
            server["key"] = key
    hass.data[DOMAIN][entry.entry_id] = {
        "interval": data.get("interval"),
        "servers": servers,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a VServer SSH Stats config entry."""
    _LOGGER.debug("Unloading VServer SSH Stats entry")
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
