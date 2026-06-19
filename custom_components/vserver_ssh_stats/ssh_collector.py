"""SSH utilities for VServer SSH Stats integration."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import time
from typing import Any, Dict, Optional

import paramiko

from .net_cache import EnergyStatsCache, NetStatsCache, ProcessPeakCache
from .remote_script import REMOTE_SCRIPT
from .ssh_security import configure_pinned_host_keys
from .util import (
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_CONNECT_TIMEOUT,
    normalize_mac_addresses,
    parse_monitored_ports,
)

_LOGGER = logging.getLogger(__name__)

net_cache = NetStatsCache()
disk_io_cache = NetStatsCache()
energy_cache = EnergyStatsCache()
process_peak_cache = ProcessPeakCache()

DEFAULT_PORT_CHECK_TIMEOUT = 3
MAX_CUSTOM_COMMAND_OUTPUT = 16 * 1024


def _read_custom_command_channel(
    channel: Any,
    command_timeout: int,
) -> tuple[str, str, int, bool]:
    """Drain stdout and stderr concurrently with bounded retained output."""

    stdout_data = bytearray()
    stderr_data = bytearray()
    output_truncated = False
    deadline = time.monotonic() + command_timeout
    while True:
        read_data = False
        while channel.recv_ready():
            chunk = channel.recv(4096)
            read_data = True
            remaining = MAX_CUSTOM_COMMAND_OUTPUT - len(stdout_data)
            if remaining > 0:
                stdout_data.extend(chunk[:remaining])
            output_truncated |= len(chunk) > max(remaining, 0)
        while channel.recv_stderr_ready():
            chunk = channel.recv_stderr(4096)
            read_data = True
            remaining = MAX_CUSTOM_COMMAND_OUTPUT - len(stderr_data)
            if remaining > 0:
                stderr_data.extend(chunk[:remaining])
        if channel.exit_status_ready() and not (
            channel.recv_ready() or channel.recv_stderr_ready()
        ):
            break
        if time.monotonic() >= deadline:
            channel.close()
            raise TimeoutError(
                f"Custom command timed out after {command_timeout} seconds"
            )
        if not read_data:
            time.sleep(0.01)

    status = channel.recv_exit_status()
    return (
        stdout_data.decode("utf-8", "replace"),
        stderr_data.decode("utf-8", "replace"),
        status,
        output_truncated,
    )


def _run_ssh(
    host: str,
    username: str,
    password: Optional[str],
    key: Optional[str],
    port: int,
    cmd: str,
    stdin_data: Optional[str],
    connect_timeout: int,
    command_timeout: int,
    host_key_fingerprints: object,
) -> tuple[str, Dict[str, float]]:
    ssh = paramiko.SSHClient()
    configure_pinned_host_keys(ssh, host_key_fingerprints)
    started = time.monotonic()
    ssh.connect(
        hostname=host,
        port=port,
        username=username,
        password=password,
        key_filename=key,
        timeout=connect_timeout,
        banner_timeout=connect_timeout,
        auth_timeout=connect_timeout,
    )
    connected = time.monotonic()
    try:
        stdin, stdout, stderr = ssh.exec_command(cmd, timeout=command_timeout)
        if stdin_data is not None:
            stdin.write(stdin_data)
            stdin.flush()
            stdin.channel.shutdown_write()
        try:
            out = stdout.read().decode("utf-8", "ignore")
            err = stderr.read().decode("utf-8", "ignore")
            status = stdout.channel.recv_exit_status()
        except socket.timeout as err:
            raise TimeoutError(
                f"Remote collector command timed out after {command_timeout} seconds"
            ) from err
        if status != 0 and not out:
            raise RuntimeError(err.strip() or f"Remote collector exited with status {status}")
        finished = time.monotonic()
        return out, {
            "connect_time_ms": (connected - started) * 1000,
            "collection_time_ms": (finished - started) * 1000,
        }
    finally:
        ssh.close()


def _run_custom_command(
    host: str,
    username: str,
    password: Optional[str],
    key: Optional[str],
    port: int,
    command: str,
    connect_timeout: int,
    command_timeout: int,
    host_key_fingerprints: object,
) -> tuple[str, Dict[str, Any]]:
    """Run one configured command and return its stdout and timing data."""

    ssh = paramiko.SSHClient()
    configure_pinned_host_keys(ssh, host_key_fingerprints)
    started = time.monotonic()
    ssh.connect(
        hostname=host,
        port=port,
        username=username,
        password=password,
        key_filename=key,
        timeout=connect_timeout,
        banner_timeout=connect_timeout,
        auth_timeout=connect_timeout,
    )
    connected = time.monotonic()
    try:
        _, stdout, _ = ssh.exec_command(command, timeout=command_timeout)
        output, error_output, status, output_truncated = _read_custom_command_channel(
            stdout.channel,
            command_timeout,
        )
        if status != 0:
            detail = error_output.strip() or output.strip()
            if len(detail) > MAX_CUSTOM_COMMAND_OUTPUT:
                detail = detail[:MAX_CUSTOM_COMMAND_OUTPUT]
            raise RuntimeError(detail or f"Custom command exited with status {status}")
        finished = time.monotonic()
        return output[:MAX_CUSTOM_COMMAND_OUTPUT], {
            "connect_time_ms": (connected - started) * 1000,
            "collection_time_ms": (finished - started) * 1000,
            "output_truncated": output_truncated,
        }
    finally:
        ssh.close()


async def async_run_custom_command(
    host: str,
    username: str,
    password: Optional[str],
    key: Optional[str],
    port: int,
    command: str,
    connect_timeout: int,
    command_timeout: int,
    host_key_fingerprints: object,
) -> tuple[str, Dict[str, Any]]:
    """Run one configured custom sensor command outside the event loop."""

    return await asyncio.to_thread(
        _run_custom_command,
        host,
        username,
        password,
        key,
        port,
        command,
        connect_timeout,
        command_timeout,
        host_key_fingerprints,
    )


def _sanitize(name: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9_]+", "_", name).lower()


def _safe_int(value: Any) -> Optional[int]:
    """Return *value* as int or ``None`` when conversion fails."""

    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    """Return *value* as float or ``None`` when conversion fails."""

    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_bool(value: Any) -> Optional[bool]:
    """Return *value* as bool or ``None`` when it is not explicit."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return None


def _safe_list(value: Any) -> list:
    """Return *value* if it is a list, otherwise an empty list."""

    if isinstance(value, list):
        return value
    if value not in (None, []):
        _LOGGER.debug("Expected list but received %s", type(value))
    return []


def _temperature_status(value: Any) -> Optional[str]:
    """Return a coarse temperature state independent of the raw temperature sensor."""

    temp = _safe_float(value)
    if temp is None:
        return None
    if temp >= 85:
        return "critical"
    if temp >= 70:
        return "warning"
    return "ok"


WINDOWS_REMOTE_SCRIPT = (
    "powershell -NoProfile -NonInteractive -Command "
    "\"$boot=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime; "
    "$uptime=[int]((Get-Date)-$boot).TotalSeconds; "
    "$obj=[ordered]@{"
    "cpu=$null;mem=$null;disk=$null;disk_capacity_total=$null;uptime=$uptime;"
    "temp=$null;rx=$null;tx=$null;ram=$null;cores=$env:NUMBER_OF_PROCESSORS;"
    "os='Windows';pkg_count=$null;pkg_list='';docker=0;containers='';"
    "load_1=$null;load_5=$null;load_15=$null;cpu_freq=$null;vnc='no';web='no';"
    "ssh='yes';power_w=$null;energy_uj=$null;energy_range_uj=$null;"
    "container_stats=@();disk_stats=@();top_processes=@();mac_address='';"
    "mac_addresses=@();swap_usage=$null;swap_total=$null;reboot_required=$false;"
    "security_updates=$null;last_boot=$boot.ToUniversalTime().ToString('o');"
    "kernel_version='Windows';primary_ip='';failed_systemd_units=$null;"
    "failed_systemd_units_list=@();journal_errors=$null;root_fs_readonly=$null;"
    "disk_read_bytes=$null;disk_write_bytes=$null}; "
    "$obj | ConvertTo-Json -Compress\""
)


CollectionCommand = tuple[str, Optional[str]]
SLOW_RESULT_KEYS = {
    "pkg_count",
    "pkg_list",
    "security_updates",
    "docker",
    "containers",
    "container_details",
    "container_stats",
    "docker_unhealthy_containers",
    "docker_restart_count_total",
    "docker_images_size_bytes",
    "docker_containers_size_bytes",
    "docker_volumes_size_bytes",
    "docker_build_cache_size_bytes",
}


async def _async_check_tcp_port(host: str, port: int, timeout: int) -> dict[str, Any]:
    """Check whether one TCP port is reachable from Home Assistant."""

    started = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return {
            "port": port,
            "protocol": "tcp",
            "open": True,
            "response_time_ms": round((time.monotonic() - started) * 1000, 2),
            "error": None,
        }
    except Exception as err:
        return {
            "port": port,
            "protocol": "tcp",
            "open": False,
            "response_time_ms": round((time.monotonic() - started) * 1000, 2),
            "error": str(err) or err.__class__.__name__,
        }


async def _async_check_monitored_ports(
    host: str,
    monitored_ports: object,
    connect_timeout: int,
) -> list[dict[str, Any]]:
    """Return TCP reachability results for configured ports."""

    try:
        ports = parse_monitored_ports(monitored_ports)
    except ValueError:
        ports = []
    if not ports:
        return []

    timeout = max(1, min(connect_timeout, DEFAULT_PORT_CHECK_TIMEOUT))
    results = await asyncio.gather(
        *(_async_check_tcp_port(host, port, timeout) for port in ports)
    )
    return list(results)


def _add_port_check_results(
    result: Dict[str, Any],
    port_checks: list[dict[str, Any]],
) -> None:
    """Flatten TCP port check results into coordinator data."""

    result["port_checks"] = port_checks
    for check in port_checks:
        port = check["port"]
        result[f"port_open_{port}"] = check["open"]
        result[f"port_response_time_ms_{port}"] = check["response_time_ms"]
        result[f"port_error_{port}"] = check["error"]


def _build_collection_commands(
    target_os: Optional[str],
    collector_mode: str = "base",
    pkg_timeout: int | None = None,
    docker_timeout: int | None = None,
    storage_timeout: int | None = None,
) -> list[CollectionCommand]:
    """Return collection commands ordered by target OS preference."""

    normalized = (target_os or "auto").strip().lower()
    env_parts = [f"VSERVER_SSH_STATS_MODE={collector_mode}"]
    if pkg_timeout is not None:
        env_parts.append(f"VSERVER_SSH_STATS_PKG_TIMEOUT={int(pkg_timeout)}")
    if docker_timeout is not None:
        env_parts.append(f"VSERVER_SSH_STATS_DOCKER_TIMEOUT={int(docker_timeout)}")
    if storage_timeout is not None:
        env_parts.append(f"VSERVER_SSH_STATS_STORAGE_TIMEOUT={int(storage_timeout)}")
    env = " ".join(env_parts)
    linux_commands: list[CollectionCommand] = [
        (f"{env} bash -s", REMOTE_SCRIPT),
        (f"{env} /bin/bash -s", REMOTE_SCRIPT),
    ]
    windows_command: CollectionCommand = (WINDOWS_REMOTE_SCRIPT, None)
    if collector_mode != "base":
        return [] if normalized == "windows" else linux_commands
    if normalized == "windows":
        return [windows_command, *linux_commands]
    return [*linux_commands, windows_command]


def _parse_json_output(output: str) -> Dict[str, Any]:
    """Parse JSON output while tolerating surrounding text."""

    stripped = output.strip()
    if not stripped:
        return {}

    try:
        return json.loads(stripped)
    except json.JSONDecodeError as err:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = stripped[start : end + 1]
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                _LOGGER.debug(
                    "Failed to parse extracted JSON substring from SSH output: %s",
                    candidate[:200],
                )
                raise err
            _LOGGER.debug(
                "Parsed JSON from SSH output after trimming prefix/suffix (prefix length %s, suffix length %s)",
                start,
                len(stripped) - end - 1,
            )
            return parsed

        _LOGGER.debug("SSH output missing JSON object: %s", stripped[:200])
        raise err


async def _async_collect_raw(
    host: str,
    username: str,
    password: Optional[str],
    key: Optional[str],
    port: int,
    target_os: Optional[str],
    connect_timeout: int,
    command_timeout: int,
    collector_mode: str,
    pkg_timeout: int | None = None,
    docker_timeout: int | None = None,
    storage_timeout: int | None = None,
    host_key_fingerprints: object = None,
) -> tuple[Dict[str, Any] | None, Dict[str, float], Exception | None]:
    """Run one collector mode and return parsed remote JSON."""

    data: Dict[str, Any] | None = None
    timing: Dict[str, float] = {}
    last_error: Exception | None = None
    for cmd, stdin_data in _build_collection_commands(
        target_os,
        collector_mode,
        pkg_timeout,
        docker_timeout,
        storage_timeout,
    ):
        try:
            out, timing = await asyncio.to_thread(
                _run_ssh,
                host,
                username,
                password,
                key,
                port,
                cmd,
                stdin_data,
                connect_timeout,
                command_timeout,
                host_key_fingerprints,
            )
            data = _parse_json_output(out)
            break
        except Exception as err:
            last_error = err
            _LOGGER.debug(
                "%s collector command failed for %s: %s",
                collector_mode,
                host,
                err,
            )
    return data, timing, last_error


def _process_docker_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Docker collector output into coordinator data fields."""

    cont_stats = _safe_list(data.get("container_stats"))
    containers_raw = data.get("containers", "")
    if isinstance(containers_raw, str) and "," in containers_raw:
        containers = ", ".join(
            [part.strip() for part in containers_raw.split(",") if part.strip()]
        )
    else:
        containers = containers_raw

    processed_containers: list[Dict[str, Any]] = []
    for container in cont_stats:
        if not isinstance(container, dict):
            continue
        name = str(container.get("name") or "").strip()
        if not name:
            continue
        memory_usage_bytes = _safe_int(container.get("memory_usage_bytes"))
        memory_limit_bytes = _safe_int(container.get("memory_limit_bytes"))
        memory_limit_usage = (
            round(memory_usage_bytes / memory_limit_bytes * 100, 2)
            if memory_usage_bytes is not None
            and memory_limit_bytes is not None
            and memory_limit_bytes > 0
            else None
        )
        memory_limit_reached = (
            memory_usage_bytes >= memory_limit_bytes
            if memory_usage_bytes is not None
            and memory_limit_bytes is not None
            and memory_limit_bytes > 0
            else None
        )
        running = _safe_bool(container.get("running"))
        if running is None:
            running = (
                str(container.get("status") or "")
                .lower()
                .startswith(("up ", "restarting"))
            )
        processed_containers.append(
            {
                "id": str(container.get("id") or ""),
                "name": name,
                "cpu": _safe_float(container.get("cpu")),
                "mem": _safe_float(container.get("mem")),
                "memory_usage_bytes": memory_usage_bytes,
                "memory_limit_bytes": (
                    memory_limit_bytes
                    if memory_limit_bytes is not None and memory_limit_bytes > 0
                    else None
                ),
                "memory_limit_usage": memory_limit_usage,
                "memory_limit_reached": memory_limit_reached,
                "pids": _safe_int(container.get("pids")),
                "cpu_throttled_periods": _safe_int(
                    container.get("cpu_throttled_periods")
                ),
                "cpu_throttled_seconds": (
                    round(throttled_usec / 1_000_000, 3)
                    if (throttled_usec := _safe_int(container.get("cpu_throttled_usec")))
                    is not None
                    else None
                ),
                "image": str(container.get("image") or ""),
                "status": str(container.get("status") or ""),
                "restart_count": _safe_int(container.get("restart_count")),
                "ports": str(container.get("ports") or ""),
                "health_state": str(container.get("health_state") or ""),
                "running": running,
                "restart_policy": str(container.get("restart_policy") or ""),
                "compose_project": str(container.get("compose_project") or ""),
                "compose_service": str(container.get("compose_service") or ""),
                "swarm_service": str(container.get("swarm_service") or ""),
            }
        )
    if not containers and processed_containers:
        containers = ", ".join(container["name"] for container in processed_containers)

    docker_unhealthy_containers = 0
    docker_restart_count_total = 0
    for container in processed_containers:
        health_state = str(container.get("health_state") or "").lower()
        status = str(container.get("status") or "").lower()
        exited_with_error = status.startswith("exited") and not status.startswith(
            "exited (0)"
        )
        if health_state in {"unhealthy", "dead"} or exited_with_error:
            docker_unhealthy_containers += 1
        restart_count = _safe_int(container.get("restart_count"))
        if restart_count is not None:
            docker_restart_count_total += restart_count

    result: Dict[str, Any] = {
        "docker": _safe_int(data.get("docker")),
        "containers": containers,
        "container_details": processed_containers,
        "container_lookup": {
            _sanitize(container["name"]): container
            for container in processed_containers
        },
        "docker_unhealthy_containers": docker_unhealthy_containers,
        "docker_restart_count_total": docker_restart_count_total,
        "container_stats": processed_containers,
        "docker_images_size_bytes": _safe_int(data.get("docker_images_size_bytes")),
        "docker_containers_size_bytes": _safe_int(
            data.get("docker_containers_size_bytes")
        ),
        "docker_volumes_size_bytes": _safe_int(data.get("docker_volumes_size_bytes")),
        "docker_build_cache_size_bytes": _safe_int(
            data.get("docker_build_cache_size_bytes")
        ),
    }
    for container in processed_containers:
        cname = _sanitize(container.get("name", ""))
        if not cname:
            continue
        result[f"container_{cname}_cpu"] = _safe_float(container.get("cpu"))
        result[f"container_{cname}_mem"] = _safe_float(container.get("mem"))
        result[f"container_{cname}_memory_usage_bytes"] = _safe_int(
            container.get("memory_usage_bytes")
        )
        result[f"container_{cname}_memory_limit_bytes"] = _safe_int(
            container.get("memory_limit_bytes")
        )
        result[f"container_{cname}_memory_limit_usage"] = _safe_float(
            container.get("memory_limit_usage")
        )
        result[f"container_{cname}_memory_limit_reached"] = container.get(
            "memory_limit_reached"
        )
        result[f"container_{cname}_pids"] = _safe_int(container.get("pids"))
        result[f"container_{cname}_cpu_throttled_periods"] = _safe_int(
            container.get("cpu_throttled_periods")
        )
        result[f"container_{cname}_cpu_throttled_seconds"] = _safe_float(
            container.get("cpu_throttled_seconds")
        )
    return result


def _process_storage_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize SMART/NVMe and mdadm output into coordinator data."""

    devices: list[Dict[str, Any]] = []
    used_keys: set[str] = set()
    failed_count = 0
    for device in _safe_list(data.get("storage_devices")):
        if not isinstance(device, dict):
            continue
        name = str(device.get("name") or "").strip()
        if not name:
            continue
        serial = str(device.get("serial") or "").strip()
        key = _sanitize(serial) or _sanitize(name)
        if key in used_keys:
            key = f"{key}_{_sanitize(name)}"
        used_keys.add(key)
        status = str(device.get("smart_status") or "unknown").lower()
        if status == "failed":
            failed_count += 1
        normalized = {
            "key": key,
            "name": name,
            "path": str(device.get("path") or ""),
            "model": str(device.get("model") or ""),
            "serial": serial,
            "protocol": str(device.get("protocol") or ""),
            "smart_status": status,
            "temperature": _safe_float(device.get("temperature")),
            "wear_percent": _safe_float(device.get("wear_percent")),
            "media_errors": _safe_int(device.get("media_errors")),
            "reallocated_sectors": _safe_int(device.get("reallocated_sectors")),
            "pending_sectors": _safe_int(device.get("pending_sectors")),
            "uncorrectable_sectors": _safe_int(device.get("uncorrectable_sectors")),
            "power_on_hours": _safe_int(device.get("power_on_hours")),
        }
        devices.append(normalized)

    storage_tools_available = bool(_safe_int(data.get("storage_tools_available")))
    storage_stats_partial = bool(_safe_int(data.get("storage_stats_partial")))
    storage_devices_seen = _safe_int(data.get("storage_devices_seen"))
    storage_incomplete = storage_stats_partial or (
        bool(storage_devices_seen) and not storage_tools_available
    )

    return {
        "storage_devices": devices,
        "storage_device_lookup": {device["key"]: device for device in devices},
        "smart_failed_devices": failed_count,
        "smart_failure_detected": (
            True if failed_count > 0 else None if storage_incomplete else False
        ),
        "storage_tools_available": storage_tools_available,
        "storage_stats_partial": storage_stats_partial,
        "storage_devices_seen": storage_devices_seen,
        "storage_devices_collected": _safe_int(data.get("storage_devices_collected")),
        "storage_device_errors": _safe_int(data.get("storage_device_errors")),
        "raid_detail_arrays": [
            detail
            for detail in _safe_list(data.get("raid_details"))
            if isinstance(detail, dict)
        ],
    }


def _has_usable_docker_metrics(result: Dict[str, Any]) -> bool:
    """Return whether running containers contain a credible stats sample."""

    running_containers = [
        container
        for container in result.get("container_stats", [])
        if isinstance(container, dict) and container.get("running") is True
    ]
    if not running_containers:
        return True
    return any(
        (_safe_float(container.get("cpu")) or 0) > 0
        or (_safe_float(container.get("mem")) or 0) > 0
        for container in running_containers
    )


def _drop_slow_result_keys(result: Dict[str, Any]) -> Dict[str, Any]:
    """Remove fields owned by the slower package and Docker collectors."""

    return {
        key: value
        for key, value in result.items()
        if key not in SLOW_RESULT_KEYS and not key.startswith("container_")
    }


async def async_sample(
    host: str,
    username: str,
    password: Optional[str],
    key: Optional[str],
    port: int,
    target_os: Optional[str] = "auto",
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    command_timeout: int = DEFAULT_COMMAND_TIMEOUT,
    monitored_ports: object = None,
    host_key_fingerprints: object = None,
) -> Dict[str, Any]:
    port_check_task = asyncio.create_task(
        _async_check_monitored_ports(host, monitored_ports, connect_timeout)
    )
    data, timing, last_error = await _async_collect_raw(
        host,
        username,
        password,
        key,
        port,
        target_os,
        connect_timeout,
        command_timeout,
        "base",
        host_key_fingerprints=host_key_fingerprints,
    )

    if data is None:
        _LOGGER.debug("Failed to collect SSH response from %s: %s", host, last_error)
        data = {
            "collection_error": str(last_error) if last_error else "No collector output",
        }
    port_checks = await port_check_task
    now = time.time()

    rx = _safe_int(data.get("rx"))
    tx = _safe_int(data.get("tx"))
    if rx is None or tx is None:
        _LOGGER.debug("Missing RX/TX stats for host %s", host)
        net_in = net_out = None
    else:
        net_in_raw, net_out_raw = net_cache.compute(host, rx, tx, now)
        net_in = round(net_in_raw, 2)
        net_out = round(net_out_raw, 2)

    disk_read_bytes = _safe_int(data.get("disk_read_bytes"))
    disk_write_bytes = _safe_int(data.get("disk_write_bytes"))
    if disk_read_bytes is None or disk_write_bytes is None:
        disk_io_read = disk_io_write = None
    else:
        read_raw, write_raw = disk_io_cache.compute(
            host, disk_read_bytes, disk_write_bytes, now
        )
        disk_io_read = round(read_raw, 2)
        disk_io_write = round(write_raw, 2)

    disk_stats = _safe_list(data.get("disk_stats"))
    top_processes_raw = _safe_list(data.get("top_processes"))

    disk_total_bytes = _safe_int(data.get("disk_capacity_total"))
    disk_total_gib = (
        round(disk_total_bytes / (1024 ** 3), 2) if disk_total_bytes is not None else None
    )

    swap_total_bytes = _safe_int(data.get("swap_total"))
    swap_total_gib = (
        round(swap_total_bytes / (1024 ** 3), 2) if swap_total_bytes is not None else None
    )

    power_value = _safe_float(data.get("power_w"))
    if power_value is not None:
        power_value = round(power_value, 2)

    energy_uj = _safe_int(data.get("energy_uj"))
    energy_range = _safe_int(data.get("energy_range_uj"))
    energy_total_kwh_raw = energy_cache.compute(host, energy_uj, energy_range)
    energy_total_kwh = (
        round(energy_total_kwh_raw, 5) if energy_total_kwh_raw is not None else None
    )

    docker_result = _process_docker_data(data)

    mac_addresses = normalize_mac_addresses(data.get("mac_addresses"))
    primary_mac = normalize_mac_addresses(data.get("mac_address"))
    for mac in primary_mac:
        if mac not in mac_addresses:
            mac_addresses.insert(0, mac)

    failed_units = [
        str(unit).strip()
        for unit in _safe_list(data.get("failed_systemd_units_list"))
        if str(unit).strip()
    ]

    top_processes: list[Dict[str, Any]] = []
    for process in top_processes_raw[:5]:
        if not isinstance(process, dict):
            continue
        command = str(process.get("command") or "").strip()
        if not command:
            continue
        top_processes.append(
            {
                "pid": _safe_int(process.get("pid")),
                "command": command,
                "cpu": _safe_float(process.get("cpu")),
                "mem": _safe_float(process.get("mem")),
            }
        )
    top_process_summary = ", ".join(process["command"] for process in top_processes)

    uptime_seconds = _safe_int(data.get("uptime"))
    process_total = _safe_int(data.get("process_total"))
    process_zombies = _safe_int(data.get("process_zombies"))
    process_peak = None
    if process_total is not None and uptime_seconds is not None:
        process_peak = process_peak_cache.compute(host, process_total, uptime_seconds)

    conntrack_count = _safe_int(data.get("conntrack_count"))
    conntrack_max = _safe_int(data.get("conntrack_max"))
    conntrack_usage = None
    if conntrack_count is not None and conntrack_max:
        conntrack_usage = round(conntrack_count / conntrack_max * 100, 2)

    raid_arrays = [
        raid
        for raid in _safe_list(data.get("raid_arrays"))
        if isinstance(raid, dict)
    ]
    raid_degraded = _safe_int(data.get("software_raid_degraded"))
    raid_rebuild_active = _safe_int(data.get("software_raid_rebuild_active"))

    result: Dict[str, Any] = {
        "cpu": _safe_int(data.get("cpu")),
        "mem": _safe_int(data.get("mem")),
        "disk": _safe_int(data.get("disk")),
        "disk_capacity_total": disk_total_gib,
        "disk_io_read": disk_io_read,
        "disk_io_write": disk_io_write,
        "uptime": uptime_seconds,
        "temp": _safe_float(data.get("temp")),
        "cpu_temperature_status": _temperature_status(data.get("temp")),
        "net_in": net_in,
        "net_out": net_out,
        "ram": _safe_int(data.get("ram")),
        "cores": _safe_int(data.get("cores")),
        "os": data.get("os", ""),
        "last_boot": data.get("last_boot") or None,
        "kernel_version": data.get("kernel_version") or None,
        "mac_address": mac_addresses[0] if mac_addresses else None,
        "network_primary_mac": mac_addresses[0] if mac_addresses else None,
        "mac_addresses": mac_addresses,
        "primary_ip": data.get("primary_ip") or None,
        "top_processes": top_process_summary,
        "top_process_details": top_processes,
        "process_total": process_total,
        "process_running": _safe_int(data.get("process_running")),
        "process_zombies": process_zombies,
        "process_peak_since_boot": process_peak,
        "zombie_processes_detected": (
            process_zombies > 0 if process_zombies is not None else None
        ),
        "tcp_established": _safe_int(data.get("tcp_established")),
        "tcp_time_wait": _safe_int(data.get("tcp_time_wait")),
        "sockets_used": _safe_int(data.get("sockets_used")),
        "tcp_sockets_in_use": _safe_int(data.get("tcp_sockets_in_use")),
        "conntrack_count": conntrack_count,
        "conntrack_max": conntrack_max,
        "conntrack_usage": conntrack_usage,
        "conntrack_near_capacity": (
            conntrack_usage >= 80 if conntrack_usage is not None else None
        ),
        "software_raid_arrays": _safe_int(data.get("software_raid_arrays")),
        "software_raid_degraded": (
            bool(raid_degraded) if raid_degraded is not None else None
        ),
        "software_raid_rebuild_active": (
            bool(raid_rebuild_active) if raid_rebuild_active is not None else None
        ),
        "software_raid_rebuild_progress": _safe_float(
            data.get("software_raid_rebuild_progress")
        ),
        "software_raid_rebuild_remaining_minutes": _safe_float(
            data.get("software_raid_rebuild_remaining_minutes")
        ),
        "raid_arrays": raid_arrays,
        "load_1": data.get("load_1"),
        "load_5": data.get("load_5"),
        "load_15": data.get("load_15"),
        "cpu_freq": data.get("cpu_freq"),
        "vnc": data.get("vnc", ""),
        "web": data.get("web", ""),
        "ssh": data.get("ssh", ""),
        "power_w": power_value,
        "energy_kwh_total": energy_total_kwh,
        "swap_usage": _safe_int(data.get("swap_usage")),
        "swap_total": swap_total_gib,
        "reboot_required": bool(_safe_int(data.get("reboot_required"))),
        "root_fs_readonly": bool(_safe_int(data.get("root_fs_readonly"))),
        "failed_systemd_units": _safe_int(data.get("failed_systemd_units")),
        "failed_systemd_units_list": ", ".join(failed_units),
        "failed_systemd_units_details": failed_units,
        "journal_errors": _safe_int(data.get("journal_errors")),
        "ssh_connect_time_ms": round(timing.get("connect_time_ms", 0), 2),
        "collection_time_ms": round(timing.get("collection_time_ms", 0), 2),
        "collection_error": data.get("collection_error"),
        "last_collection_failed": bool(data.get("collection_error")),
    }

    processed_disks: list[Dict[str, Any]] = []
    for disk in disk_stats:
        name = disk.get("name") or disk.get("mount")
        mount = disk.get("mount")
        key_source = ""
        if name and mount and name != mount:
            key_source = f"{name}_{mount}"
        elif name:
            key_source = name
        elif mount:
            key_source = mount
        sanitized = _sanitize(key_source) if key_source else None
        if not sanitized:
            continue
        total_bytes = _safe_int(disk.get("total")) or 0
        free_bytes = _safe_int(disk.get("free")) or 0
        total_gib = round(total_bytes / (1024 ** 3), 2)
        free_gib = round(free_bytes / (1024 ** 3), 2)
        label = name or mount or sanitized
        if mount and name and mount != name:
            label = f"{name} ({mount})"
        processed_disks.append(
            {
                "name": name,
                "mount": mount,
                "total": total_gib,
                "free": free_gib,
                "label": label,
                "key": sanitized,
            }
        )
        result[f"disk_{sanitized}_total"] = total_gib
        result[f"disk_{sanitized}_free"] = free_gib
    result["disk_stats"] = processed_disks

    result.update(docker_result)

    _add_port_check_results(result, port_checks)

    if data.get("os") == "Windows":
        return result
    return _drop_slow_result_keys(result)


async def async_sample_packages(
    host: str,
    username: str,
    password: Optional[str],
    key: Optional[str],
    port: int,
    target_os: Optional[str] = "auto",
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    command_timeout: int = DEFAULT_COMMAND_TIMEOUT,
    host_key_fingerprints: object = None,
) -> Dict[str, Any]:
    """Collect package update metrics with the slow collector mode."""

    data, timing, last_error = await _async_collect_raw(
        host,
        username,
        password,
        key,
        port,
        target_os,
        connect_timeout,
        command_timeout,
        "packages",
        pkg_timeout=command_timeout,
        host_key_fingerprints=host_key_fingerprints,
    )
    if data is None:
        return {
            "package_collection_error": (
                str(last_error) if last_error else "No package collector output"
            )
        }
    if _safe_int(data.get("pkg_updates_complete")) != 1:
        return {"package_collection_error": "Package update collection did not complete"}

    pkg_count = _safe_int(data.get("pkg_count"))
    result: Dict[str, Any] = {
        "package_collection_error": None,
        "package_collection_time_ms": round(timing.get("collection_time_ms", 0), 2),
    }
    if pkg_count is not None:
        result["pkg_count"] = pkg_count
        result["pkg_list"] = data.get("pkg_list", "")

    security_updates = _safe_int(data.get("security_updates"))
    if security_updates is not None:
        result["security_updates"] = security_updates
    return result


async def async_sample_docker(
    host: str,
    username: str,
    password: Optional[str],
    key: Optional[str],
    port: int,
    target_os: Optional[str] = "auto",
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    command_timeout: int = DEFAULT_COMMAND_TIMEOUT,
    host_key_fingerprints: object = None,
) -> Dict[str, Any]:
    """Collect Docker metrics with the slow collector mode."""

    quick_timeout = min(command_timeout, 30)
    outer_timeout = command_timeout + (quick_timeout * 3) + 15
    data, timing, last_error = await _async_collect_raw(
        host,
        username,
        password,
        key,
        port,
        target_os,
        connect_timeout,
        outer_timeout,
        "docker",
        docker_timeout=command_timeout,
        host_key_fingerprints=host_key_fingerprints,
    )
    if data is None:
        return {
            "docker_collection_error": (
                str(last_error) if last_error else "No Docker collector output"
            )
        }
    if _safe_int(data.get("docker_stats_complete")) != 1:
        return {"docker_collection_error": "Docker stats collection did not complete"}
    if _safe_int(data.get("docker")) is None:
        return {"docker_collection_error": "Docker state was not reported"}

    result = _process_docker_data(data)
    if not _has_usable_docker_metrics(result):
        return {
            "docker_collection_error": (
                "Docker returned only empty or zero CPU and memory samples"
            )
        }
    if _safe_int(data.get("docker_stats_partial")) == 1:
        result["docker_collection_error"] = (
            "Docker container inventory was collected, but some detail metrics timed out"
        )
    else:
        result["docker_collection_error"] = None
    result["docker_collection_time_ms"] = round(timing.get("collection_time_ms", 0), 2)
    return result


async def async_sample_storage(
    host: str,
    username: str,
    password: Optional[str],
    key: Optional[str],
    port: int,
    target_os: Optional[str] = "auto",
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    command_timeout: int = DEFAULT_COMMAND_TIMEOUT,
    host_key_fingerprints: object = None,
) -> Dict[str, Any]:
    """Collect SMART, NVMe, and mdadm metrics with a slow collector mode."""

    per_command_timeout = min(max(command_timeout, 1), 20)
    data, timing, last_error = await _async_collect_raw(
        host,
        username,
        password,
        key,
        port,
        target_os,
        connect_timeout,
        command_timeout,
        "storage",
        storage_timeout=per_command_timeout,
        host_key_fingerprints=host_key_fingerprints,
    )
    if data is None:
        return {
            "storage_collection_error": (
                str(last_error) if last_error else "No storage collector output"
            )
        }
    if _safe_int(data.get("storage_stats_complete")) != 1:
        return {"storage_collection_error": "Storage health collection did not complete"}

    result = _process_storage_data(data)
    devices_seen = result.get("storage_devices_seen") or 0
    device_errors = result.get("storage_device_errors") or 0
    if result.get("storage_stats_partial"):
        result["storage_collection_error"] = (
            f"Storage health collection was partial: {device_errors} of "
            f"{devices_seen} devices could not be read"
        )
    elif devices_seen > 0 and not result.get("storage_tools_available"):
        result["storage_collection_error"] = (
            "No SMART/NVMe collection tool is available on the remote host"
        )
    else:
        result["storage_collection_error"] = None
    result["storage_collection_time_ms"] = round(
        timing.get("collection_time_ms", 0), 2
    )
    return result
