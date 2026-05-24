"""VServer SSH Stats integration for Home Assistant."""
from __future__ import annotations

import asyncio
import logging
import socket
import json
import re
from datetime import UTC, datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

import voluptuous as vol
import paramiko

from .util import (
    DEFAULT_ACTION_COMMAND_TIMEOUT,
    DEFAULT_COMMAND_ALLOWLIST,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_INTERVAL,
    is_command_allowed,
    parse_command_allowlist,
    resolve_private_key_path,
)
_LOGGER = logging.getLogger(__name__)

DOMAIN = "vserver_ssh_stats"
PLATFORMS: list[str] = ["sensor", "binary_sensor", "button"]

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)
SUPPORTED_TARGET_OS = {"auto", "debian", "raspbian", "windows"}
ACTION_STATUS_EVENT = f"{DOMAIN}_action_status"
REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")


def _normalize_target_os(value: str | None) -> str:
    """Return a safe target OS value."""

    normalized = (value or "auto").strip().lower()
    return normalized if normalized in SUPPORTED_TARGET_OS else "auto"


def _cleanup_empty_device_entries(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove stale empty devices left behind by earlier registry identifiers."""

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    removed = 0
    try:
        devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    except AttributeError:  # pragma: no cover - compatibility with older HA versions
        devices = [
            device
            for device in device_registry.devices.values()
            if entry.entry_id in device.config_entries
        ]

    for device in devices:
        try:
            entities = er.async_entries_for_device(
                entity_registry,
                device.id,
                include_disabled_entities=True,
            )
        except TypeError:  # pragma: no cover - compatibility with older HA versions
            entities = er.async_entries_for_device(entity_registry, device.id)
        if entities:
            continue
        try:
            device_registry.async_update_device(
                device.id,
                remove_config_entry_id=entry.entry_id,
            )
        except TypeError:  # pragma: no cover - compatibility with older HA versions
            if device.config_entries != {entry.entry_id}:
                continue
            device_registry.async_remove_device(device.id)
        removed += 1

    if removed:
        _LOGGER.info(
            "Removed %s stale empty VServer SSH Stats device registry entr%s",
            removed,
            "y" if removed == 1 else "ies",
        )


def _positive_timeout(value: object, default: int) -> int:
    """Return *value* as a positive timeout in seconds."""

    try:
        timeout = int(value)
    except (TypeError, ValueError):
        return default
    return timeout if timeout > 0 else default


def _safe_remote_name(value: object) -> str:
    """Validate a remote service or container identifier."""

    name = str(cv.string(value)).strip()
    if not REMOTE_NAME_RE.match(name):
        raise vol.Invalid("Only letters, numbers, dot, underscore, dash and @ are allowed")
    return name


def _log_line_count(value: object) -> int:
    """Validate log tail line count."""

    return vol.All(vol.Coerce(int), vol.Range(min=1, max=1000))(value)


def _build_update_commands(target_os: str) -> list[str]:
    """Return update commands ordered by target OS preference."""

    linux_cmd = (
        "if command -v apt-get >/dev/null 2>&1; then "
        "sudo apt-get update && sudo apt-get -y upgrade; "
        "elif command -v dnf >/dev/null 2>&1; then "
        "sudo dnf -y upgrade; "
        "elif command -v yum >/dev/null 2>&1; then "
        "sudo yum -y update; "
        "else echo 'No supported package manager found'; fi"
    )
    windows_cmd = (
        "powershell.exe -NoProfile -NonInteractive -Command "
        "\"if (Get-Command winget -ErrorAction SilentlyContinue) { "
        "winget upgrade --all --accept-source-agreements --accept-package-agreements "
        "} else { Write-Output 'winget not available'; exit 1 }\""
    )
    return [windows_cmd, linux_cmd] if target_os == "windows" else [linux_cmd, windows_cmd]


def _build_package_list_update_commands(target_os: str) -> list[str]:
    """Return commands that refresh package metadata without upgrading packages."""

    linux_cmd = (
        "if command -v apt-get >/dev/null 2>&1; then "
        "sudo apt-get update; "
        "elif command -v dnf >/dev/null 2>&1; then "
        "sudo dnf -y makecache; "
        "elif command -v yum >/dev/null 2>&1; then "
        "sudo yum -y makecache; "
        "elif command -v pacman >/dev/null 2>&1; then "
        "sudo pacman -Sy --noconfirm; "
        "elif command -v zypper >/dev/null 2>&1; then "
        "sudo zypper --non-interactive refresh; "
        "elif command -v apk >/dev/null 2>&1; then "
        "sudo apk update; "
        "else echo 'No supported package manager found'; exit 1; fi"
    )
    windows_cmd = (
        "powershell.exe -NoProfile -NonInteractive -Command "
        "\"if (Get-Command winget -ErrorAction SilentlyContinue) { "
        "winget source update "
        "} else { Write-Output 'winget not available'; exit 1 }\""
    )
    return [windows_cmd, linux_cmd] if target_os == "windows" else [linux_cmd, windows_cmd]


def _build_reboot_commands(target_os: str) -> list[str]:
    """Return reboot commands ordered by target OS preference."""

    windows_cmd = "shutdown /r /t 0"
    linux_cmd = "sudo reboot &"
    return [windows_cmd, linux_cmd] if target_os == "windows" else [linux_cmd, windows_cmd]


def _build_clear_package_cache_commands(target_os: str) -> list[str]:
    """Return commands that clear package manager caches."""

    linux_cmd = (
        "if command -v apt-get >/dev/null 2>&1; then "
        "sudo apt-get clean; "
        "elif command -v dnf >/dev/null 2>&1; then "
        "sudo dnf clean all; "
        "elif command -v yum >/dev/null 2>&1; then "
        "sudo yum clean all; "
        "elif command -v pacman >/dev/null 2>&1; then "
        "sudo pacman -Sc --noconfirm; "
        "elif command -v zypper >/dev/null 2>&1; then "
        "sudo zypper clean --all; "
        "elif command -v apk >/dev/null 2>&1; then "
        "sudo apk cache clean; "
        "else echo 'No supported package manager found'; exit 1; fi"
    )
    windows_cmd = (
        "powershell.exe -NoProfile -NonInteractive -Command "
        "\"Write-Output 'Package cache cleanup is not supported on this Windows target'; exit 1\""
    )
    return [windows_cmd, linux_cmd] if target_os == "windows" else [linux_cmd, windows_cmd]


def _build_restart_service_commands(target_os: str, service: str) -> list[str]:
    """Return commands that restart a service."""

    linux_cmd = (
        f"if command -v systemctl >/dev/null 2>&1; then sudo systemctl restart {service}; "
        "else echo 'systemctl not available'; exit 1; fi"
    )
    windows_cmd = (
        "powershell.exe -NoProfile -NonInteractive -Command "
        f"\"Restart-Service -Name '{service}' -Force\""
    )
    return [windows_cmd, linux_cmd] if target_os == "windows" else [linux_cmd, windows_cmd]


def _build_docker_container_commands(action: str, container: str) -> list[str]:
    """Return commands for Docker container actions."""

    return [
        f"docker {action} {container}",
        f"sudo docker {action} {container}",
    ]


def _build_docker_prune_commands() -> list[str]:
    """Return commands that prune unused Docker resources."""

    return [
        "docker system prune -f",
        "sudo docker system prune -f",
    ]


def _build_diagnostics_commands() -> list[str]:
    """Return a compact remote diagnostics command."""

    linux_cmd = (
        "printf 'OS: '; "
        "(grep '^PRETTY_NAME' /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '\"' || uname -sr); "
        "printf '\\nKernel: '; uname -r 2>/dev/null; "
        "printf '\\nUptime: '; (uptime -p 2>/dev/null || uptime 2>/dev/null); "
        "printf '\\nDisk:\\n'; df -h -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null; "
        "printf '\\nFailed units:\\n'; (systemctl --failed --no-pager 2>/dev/null || true); "
        "printf '\\nDocker:\\n'; "
        "(docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Image}}' 2>/dev/null || true)"
    )
    windows_cmd = (
        "powershell.exe -NoProfile -NonInteractive -Command "
        "\"Get-ComputerInfo | Select-Object OsName,OsVersion,CsName,WindowsVersion | Format-List\""
    )
    return [linux_cmd, windows_cmd]


def _build_tail_logs_commands(target_os: str, service: str | None, lines: int) -> list[str]:
    """Return commands that fetch recent logs."""

    if service:
        linux_cmd = (
            f"journalctl -u {service} -n {lines} --no-pager 2>/dev/null || "
            f"sudo journalctl -u {service} -n {lines} --no-pager"
        )
        windows_cmd = (
            "powershell.exe -NoProfile -NonInteractive -Command "
            f"\"Get-EventLog -LogName System -Newest {lines} | Format-Table -AutoSize\""
        )
    else:
        linux_cmd = (
            f"journalctl -n {lines} --no-pager 2>/dev/null || "
            f"sudo journalctl -n {lines} --no-pager"
        )
        windows_cmd = (
            "powershell.exe -NoProfile -NonInteractive -Command "
            f"\"Get-EventLog -LogName System -Newest {lines} | Format-Table -AutoSize\""
        )
    return [windows_cmd, linux_cmd] if target_os == "windows" else [linux_cmd, windows_cmd]


def _exec_ssh_with_fallback(
    client: paramiko.SSHClient,
    commands: list[str],
    command_timeout: int,
) -> tuple[str, bool]:
    """Run commands in order and return the first successful output."""

    last_output = ""
    for command in commands:
        _, stdout, stderr = client.exec_command(command, timeout=command_timeout)
        output = stdout.read().decode() + stderr.read().decode()
        status = stdout.channel.recv_exit_status()
        if status == 0:
            return output, True
        last_output = output
    return last_output, False


def _exec_remote_commands(
    hass: HomeAssistant,
    data: dict,
    commands: list[str],
) -> tuple[str, bool]:
    """Connect via SSH, run command fallbacks, and return output/success."""

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_timeout = _positive_timeout(data.get("connect_timeout"), DEFAULT_CONNECT_TIMEOUT)
    command_timeout = _positive_timeout(
        data.get("command_timeout"), DEFAULT_ACTION_COMMAND_TIMEOUT
    )
    connect_args = {
        "hostname": data["host"],
        "username": data["username"],
        "port": data.get("port", 22),
        "password": data.get("password"),
        "timeout": connect_timeout,
        "banner_timeout": connect_timeout,
        "auth_timeout": connect_timeout,
    }
    key = resolve_private_key_path(hass, data.get("key"))
    if key:
        connect_args["key_filename"] = key
    client.connect(**{k: v for k, v in connect_args.items() if v})
    try:
        return _exec_ssh_with_fallback(client, commands, command_timeout)
    finally:
        client.close()


def _command_allowlist_for_host(hass: HomeAssistant, host: str) -> list[str]:
    """Return the configured run-command allowlist for *host*."""

    host_rules: list[str] = []
    fallback_rules: list[str] = []
    matched_host = False
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if not isinstance(entry_data, dict):
            continue
        rules = parse_command_allowlist(
            entry_data.get("command_allowlist", DEFAULT_COMMAND_ALLOWLIST)
        )
        if rules:
            fallback_rules.extend(rules)
        if any(server.get("host") == host for server in entry_data.get("servers", [])):
            matched_host = True
            host_rules.extend(rules)
    return host_rules if matched_host else fallback_rules


def _store_action_status(
    hass: HomeAssistant,
    host: str,
    action: str,
    output: str,
    success: bool,
) -> dict[str, object]:
    """Store and announce the latest action result for sensors and automations."""

    payload: dict[str, object] = {
        "host": host,
        "action": action,
        "status": "success" if success else "failed",
        "success": success,
        "output": output,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    status_store = hass.data.setdefault(DOMAIN, {}).setdefault("action_status", {})
    host_store = status_store.setdefault(host, {})
    host_store[action] = payload
    hass.bus.async_fire(ACTION_STATUS_EVENT, payload)
    return payload


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

    async def handle_get_local_ip(call: ServiceCall) -> ServiceResponse:
        """Handle service call to fetch the local IP."""
        try:
            ip = await _async_get_local_ip()
        except Exception as err:  # pragma: no cover - best effort
            _LOGGER.error("Failed to fetch local IP: %s", err)
            ip = ""
        hass.bus.async_fire(f"{DOMAIN}_local_ip", {"ip": ip})
        return {"ip": ip}

    async def handle_get_uptime(call: ServiceCall) -> ServiceResponse:
        """Handle service call to fetch system uptime."""
        try:
            uptime = await _async_get_uptime()
        except Exception as err:  # pragma: no cover - best effort
            _LOGGER.error("Failed to fetch uptime: %s", err)
            uptime = 0.0
        hass.bus.async_fire(f"{DOMAIN}_uptime", {"uptime": uptime})
        return {"uptime": uptime}

    async def handle_list_connections(call: ServiceCall) -> ServiceResponse:
        """Handle service call to list active SSH connections."""
        try:
            connections = await _async_list_ssh_connections()
        except Exception as err:  # pragma: no cover - command best effort
            _LOGGER.error("Listing SSH connections failed: %s", err)
            connections = []
        hass.bus.async_fire(f"{DOMAIN}_connections", {"connections": connections})
        return {"connections": connections}

    async def handle_run_command(call: ServiceCall) -> ServiceResponse:
        """Execute an arbitrary command on a server via SSH."""
        data = call.data
        command = data["command"]
        host = data["host"]
        allowlist = _command_allowlist_for_host(hass, host)
        if not is_command_allowed(command, allowlist):
            output = "Command blocked by VServer SSH Stats allowlist"
            _LOGGER.warning("Blocked disallowed SSH command for %s: %s", host, command)
            _store_action_status(hass, host, "run_command", output, False)
            hass.bus.async_fire(
                f"{DOMAIN}_command",
                {"host": host, "output": output, "success": False, "blocked": True},
            )
            return {"output": output, "success": False, "blocked": True}

        def _exec_cmd() -> tuple[str, bool]:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            connect_timeout = _positive_timeout(data.get("connect_timeout"), DEFAULT_CONNECT_TIMEOUT)
            command_timeout = _positive_timeout(
                data.get("command_timeout"), DEFAULT_ACTION_COMMAND_TIMEOUT
            )
            connect_args = {
                "hostname": data["host"],
                "username": data["username"],
                "port": data.get("port", 22),
                "password": data.get("password"),
                "timeout": connect_timeout,
                "banner_timeout": connect_timeout,
                "auth_timeout": connect_timeout,
            }
            key = resolve_private_key_path(hass, data.get("key"))
            if key:
                connect_args["key_filename"] = key
            client.connect(**{k: v for k, v in connect_args.items() if v})
            try:
                _, stdout, stderr = client.exec_command(command, timeout=command_timeout)
                output = stdout.read().decode() + stderr.read().decode()
                status = stdout.channel.recv_exit_status()
                return output, status == 0
            finally:
                client.close()

        try:
            output, success = await asyncio.to_thread(_exec_cmd)
        except Exception as err:  # pragma: no cover - best effort
            _LOGGER.error("Command execution failed: %s", err)
            output = ""
            success = False
        _store_action_status(hass, host, "run_command", output, success)
        hass.bus.async_fire(f"{DOMAIN}_command", {"host": host, "output": output, "success": success})
        return {"output": output, "success": success}

    async def _run_remote_action(
        call: ServiceCall,
        action: str,
        commands: list[str],
        event_suffix: str,
    ) -> ServiceResponse:
        """Run a remote action and keep status handling consistent."""

        data = dict(call.data)
        try:
            output, success = await asyncio.to_thread(
                _exec_remote_commands,
                hass,
                data,
                commands,
            )
        except Exception as err:  # pragma: no cover - best effort
            _LOGGER.error("%s failed for %s: %s", action, data.get("host"), err)
            output = str(err)
            success = False
        _store_action_status(hass, data["host"], action, output, success)
        hass.bus.async_fire(
            f"{DOMAIN}_{event_suffix}",
            {"host": data["host"], "output": output, "success": success},
        )
        return {"output": output, "success": success}

    async def handle_refresh(call: ServiceCall) -> ServiceResponse:
        """Request an immediate coordinator refresh for one or all servers."""

        host = call.data.get("host")
        coordinators = []
        for entry_data in hass.data.get(DOMAIN, {}).values():
            if not isinstance(entry_data, dict):
                continue
            for coordinator in entry_data.get("coordinators", []) or []:
                if host and coordinator.server.get("host") != host:
                    continue
                coordinators.append(coordinator)

        if not coordinators:
            output = "No matching coordinator found"
            if host:
                _store_action_status(hass, host, "refresh", output, False)
            return {"refreshed": 0, "success": False, "output": output}

        results = await asyncio.gather(
            *(coordinator.async_request_refresh() for coordinator in coordinators),
            return_exceptions=True,
        )
        success = not any(isinstance(result, Exception) for result in results)
        output = f"Requested refresh for {len(coordinators)} coordinator(s)"
        for coordinator in coordinators:
            _store_action_status(hass, coordinator.server["host"], "refresh", output, success)
        hass.bus.async_fire(
            f"{DOMAIN}_refresh",
            {"host": host, "refreshed": len(coordinators), "success": success},
        )
        return {"refreshed": len(coordinators), "success": success, "output": output}

    async def handle_update_package_list(call: ServiceCall) -> ServiceResponse:
        """Refresh package metadata on a server via SSH."""

        target_os = _normalize_target_os(call.data.get("target_os"))
        commands = _build_package_list_update_commands(target_os)
        return await _run_remote_action(call, "update_package_list", commands, "update_package_list")

    async def handle_update_packages(call: ServiceCall) -> ServiceResponse:
        """Update packages on a server via SSH."""

        target_os = _normalize_target_os(call.data.get("target_os"))
        commands = _build_update_commands(target_os)
        return await _run_remote_action(call, "update_packages", commands, "update_packages")

    async def handle_upgrade_packages(call: ServiceCall) -> ServiceResponse:
        """Upgrade packages on a server via SSH."""

        target_os = _normalize_target_os(call.data.get("target_os"))
        commands = _build_update_commands(target_os)
        return await _run_remote_action(call, "upgrade_packages", commands, "upgrade_packages")

    async def handle_reboot_host(call: ServiceCall) -> ServiceResponse:
        """Reboot a server via SSH."""

        target_os = _normalize_target_os(call.data.get("target_os"))
        commands = _build_reboot_commands(target_os)
        return await _run_remote_action(call, "reboot_host", commands, "reboot")

    async def handle_restart_service(call: ServiceCall) -> ServiceResponse:
        """Restart one service on a server via SSH."""

        target_os = _normalize_target_os(call.data.get("target_os"))
        commands = _build_restart_service_commands(target_os, call.data["service"])
        return await _run_remote_action(call, "restart_service", commands, "restart_service")

    async def handle_docker_container_action(call: ServiceCall, action: str) -> ServiceResponse:
        """Run one Docker container action."""

        commands = _build_docker_container_commands(action, call.data["container"])
        return await _run_remote_action(
            call,
            f"{action}_docker_container",
            commands,
            f"{action}_docker_container",
        )

    async def handle_start_docker_container(call: ServiceCall) -> ServiceResponse:
        """Start one Docker container."""

        return await handle_docker_container_action(call, "start")

    async def handle_stop_docker_container(call: ServiceCall) -> ServiceResponse:
        """Stop one Docker container."""

        return await handle_docker_container_action(call, "stop")

    async def handle_restart_docker_container(call: ServiceCall) -> ServiceResponse:
        """Restart one Docker container."""

        return await handle_docker_container_action(call, "restart")

    async def handle_prune_docker(call: ServiceCall) -> ServiceResponse:
        """Prune unused Docker resources."""

        return await _run_remote_action(
            call,
            "prune_docker",
            _build_docker_prune_commands(),
            "prune_docker",
        )

    async def handle_clear_package_cache(call: ServiceCall) -> ServiceResponse:
        """Clear package manager caches on a server via SSH."""

        target_os = _normalize_target_os(call.data.get("target_os"))
        commands = _build_clear_package_cache_commands(target_os)
        return await _run_remote_action(
            call,
            "clear_package_cache",
            commands,
            "clear_package_cache",
        )

    async def handle_get_server_diagnostics(call: ServiceCall) -> ServiceResponse:
        """Return a compact remote diagnostics report."""

        return await _run_remote_action(
            call,
            "get_server_diagnostics",
            _build_diagnostics_commands(),
            "server_diagnostics",
        )

    async def handle_tail_logs(call: ServiceCall) -> ServiceResponse:
        """Fetch recent logs from the remote host."""

        target_os = _normalize_target_os(call.data.get("target_os"))
        commands = _build_tail_logs_commands(
            target_os,
            call.data.get("service"),
            call.data["lines"],
        )
        return await _run_remote_action(call, "tail_logs", commands, "tail_logs")

    remote_action_schema_fields = {
        vol.Required("host"): cv.string,
        vol.Required("username"): cv.string,
        vol.Optional("password"): cv.string,
        vol.Optional("key"): cv.string,
        vol.Optional("port", default=22): cv.port,
        vol.Optional("connect_timeout", default=DEFAULT_CONNECT_TIMEOUT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=300)
        ),
        vol.Optional("command_timeout", default=DEFAULT_ACTION_COMMAND_TIMEOUT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=3600)
        ),
    }
    os_action_schema_fields = {
        **remote_action_schema_fields,
        vol.Optional("target_os", default="auto"): vol.In(SUPPORTED_TARGET_OS),
    }
    docker_container_schema_fields = {
        **remote_action_schema_fields,
        vol.Required("container"): _safe_remote_name,
    }
    restart_service_schema_fields = {
        **os_action_schema_fields,
        vol.Required("service"): _safe_remote_name,
    }
    tail_logs_schema_fields = {
        **os_action_schema_fields,
        vol.Optional("service"): _safe_remote_name,
        vol.Optional("lines", default=100): _log_line_count,
    }

    hass.services.async_register(
        DOMAIN,
        "get_local_ip",
        handle_get_local_ip,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "get_uptime",
        handle_get_uptime,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "list_connections",
        handle_list_connections,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "refresh",
        handle_refresh,
        schema=vol.Schema({vol.Optional("host"): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
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
                vol.Optional("connect_timeout", default=DEFAULT_CONNECT_TIMEOUT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=300)
                ),
                vol.Optional("command_timeout", default=DEFAULT_ACTION_COMMAND_TIMEOUT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=3600)
                ),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "update_package_list",
        handle_update_package_list,
        schema=vol.Schema(os_action_schema_fields),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "update_packages",
        handle_update_packages,
        schema=vol.Schema(os_action_schema_fields),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "upgrade_packages",
        handle_upgrade_packages,
        schema=vol.Schema(os_action_schema_fields),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "reboot_host",
        handle_reboot_host,
        schema=vol.Schema(os_action_schema_fields),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "restart_service",
        handle_restart_service,
        schema=vol.Schema(restart_service_schema_fields),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "start_docker_container",
        handle_start_docker_container,
        schema=vol.Schema(docker_container_schema_fields),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "stop_docker_container",
        handle_stop_docker_container,
        schema=vol.Schema(docker_container_schema_fields),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "restart_docker_container",
        handle_restart_docker_container,
        schema=vol.Schema(docker_container_schema_fields),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "prune_docker",
        handle_prune_docker,
        schema=vol.Schema(os_action_schema_fields),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "clear_package_cache",
        handle_clear_package_cache,
        schema=vol.Schema(os_action_schema_fields),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "get_server_diagnostics",
        handle_get_server_diagnostics,
        schema=vol.Schema(os_action_schema_fields),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "tail_logs",
        handle_tail_logs,
        schema=vol.Schema(tail_logs_schema_fields),
        supports_response=SupportsResponse.OPTIONAL,
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
        "interval": data.get("interval") or DEFAULT_INTERVAL,
        "connect_timeout": data.get("connect_timeout") or DEFAULT_CONNECT_TIMEOUT,
        "command_timeout": data.get("command_timeout") or DEFAULT_COMMAND_TIMEOUT,
        "command_allowlist": data.get("command_allowlist", DEFAULT_COMMAND_ALLOWLIST),
        "servers": servers,
    }
    _cleanup_empty_device_entries(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a VServer SSH Stats config entry."""
    _LOGGER.debug("Unloading VServer SSH Stats entry")
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
