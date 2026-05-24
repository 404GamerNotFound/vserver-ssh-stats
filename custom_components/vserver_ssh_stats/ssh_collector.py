"""SSH utilities for VServer SSH Stats integration."""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from typing import Any, Dict, Optional

import paramiko

from .net_cache import EnergyStatsCache, NetStatsCache
from .remote_script import REMOTE_SCRIPT
from .util import DEFAULT_COMMAND_TIMEOUT, DEFAULT_CONNECT_TIMEOUT, normalize_mac_addresses

_LOGGER = logging.getLogger(__name__)

net_cache = NetStatsCache()
disk_io_cache = NetStatsCache()
energy_cache = EnergyStatsCache()


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
) -> tuple[str, Dict[str, float]]:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
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


def _build_collection_commands(target_os: Optional[str]) -> list[CollectionCommand]:
    """Return collection commands ordered by target OS preference."""

    normalized = (target_os or "auto").strip().lower()
    linux_commands: list[CollectionCommand] = [
        ("bash -s", REMOTE_SCRIPT),
        ("/bin/bash -s", REMOTE_SCRIPT),
    ]
    windows_command: CollectionCommand = (WINDOWS_REMOTE_SCRIPT, None)
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


async def async_sample(
    host: str,
    username: str,
    password: Optional[str],
    key: Optional[str],
    port: int,
    target_os: Optional[str] = "auto",
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    command_timeout: int = DEFAULT_COMMAND_TIMEOUT,
) -> Dict[str, Any]:
    data: Dict[str, Any] | None = None
    timing: Dict[str, float] = {}
    last_error: Exception | None = None
    for cmd, stdin_data in _build_collection_commands(target_os):
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
            )
            data = _parse_json_output(out)
            break
        except Exception as err:
            last_error = err
            _LOGGER.debug("Collector command failed for %s: %s", host, err)

    if data is None:
        _LOGGER.debug("Failed to collect SSH response from %s: %s", host, last_error)
        data = {
            "collection_error": str(last_error) if last_error else "No collector output",
        }
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

    cont_stats = _safe_list(data.get("container_stats"))
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

    containers_raw = data.get("containers", "")
    if isinstance(containers_raw, str) and "," in containers_raw:
        containers = ", ".join([part.strip() for part in containers_raw.split(",") if part.strip()])
    else:
        containers = containers_raw

    processed_containers: list[Dict[str, Any]] = []
    for container in cont_stats:
        if not isinstance(container, dict):
            continue
        name = str(container.get("name") or "").strip()
        if not name:
            continue
        processed_containers.append(
            {
                "name": name,
                "cpu": _safe_float(container.get("cpu")),
                "mem": _safe_float(container.get("mem")),
                "image": str(container.get("image") or ""),
                "status": str(container.get("status") or ""),
                "restart_count": _safe_int(container.get("restart_count")),
                "ports": str(container.get("ports") or ""),
                "health_state": str(container.get("health_state") or ""),
            }
        )
    if not containers and processed_containers:
        containers = ", ".join(container["name"] for container in processed_containers)

    docker_unhealthy_containers = 0
    docker_restart_count_total = 0
    for container in processed_containers:
        health_state = str(container.get("health_state") or "").lower()
        status = str(container.get("status") or "").lower()
        if health_state in {"unhealthy", "dead", "exited"} or status.startswith("exited"):
            docker_unhealthy_containers += 1
        restart_count = _safe_int(container.get("restart_count"))
        if restart_count is not None:
            docker_restart_count_total += restart_count

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

    result: Dict[str, Any] = {
        "cpu": _safe_int(data.get("cpu")),
        "mem": _safe_int(data.get("mem")),
        "disk": _safe_int(data.get("disk")),
        "disk_capacity_total": disk_total_gib,
        "disk_io_read": disk_io_read,
        "disk_io_write": disk_io_write,
        "uptime": _safe_int(data.get("uptime")),
        "temp": _safe_float(data.get("temp")),
        "cpu_temperature_status": _temperature_status(data.get("temp")),
        "net_in": net_in,
        "net_out": net_out,
        "ram": _safe_int(data.get("ram")),
        "cores": _safe_int(data.get("cores")),
        "os": data.get("os", ""),
        "pkg_count": _safe_int(data.get("pkg_count")),
        "pkg_list": data.get("pkg_list", ""),
        "security_updates": _safe_int(data.get("security_updates")),
        "last_boot": data.get("last_boot") or None,
        "kernel_version": data.get("kernel_version") or None,
        "docker": _safe_int(data.get("docker")),
        "containers": containers,
        "container_details": processed_containers,
        "docker_unhealthy_containers": docker_unhealthy_containers,
        "docker_restart_count_total": docker_restart_count_total,
        "mac_address": mac_addresses[0] if mac_addresses else None,
        "network_primary_mac": mac_addresses[0] if mac_addresses else None,
        "mac_addresses": mac_addresses,
        "primary_ip": data.get("primary_ip") or None,
        "top_processes": top_process_summary,
        "top_process_details": top_processes,
        "load_1": data.get("load_1"),
        "load_5": data.get("load_5"),
        "load_15": data.get("load_15"),
        "cpu_freq": data.get("cpu_freq"),
        "vnc": data.get("vnc", ""),
        "web": data.get("web", ""),
        "ssh": data.get("ssh", ""),
        "power_w": power_value,
        "energy_kwh_total": energy_total_kwh,
        "container_stats": processed_containers,
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

    for container in processed_containers:
        cname = _sanitize(container.get("name", ""))
        if not cname:
            continue
        cpu_value = _safe_float(container.get("cpu"))
        mem_value = _safe_float(container.get("mem"))
        result[f"container_{cname}_cpu"] = cpu_value
        result[f"container_{cname}_mem"] = mem_value

    return result
