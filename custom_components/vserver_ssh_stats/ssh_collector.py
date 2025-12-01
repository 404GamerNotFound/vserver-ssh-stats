"""SSH utilities for VServer SSH Stats integration."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

import paramiko

from .net_cache import EnergyStatsCache, NetStatsCache
from .remote_script import REMOTE_SCRIPT

_LOGGER = logging.getLogger(__name__)

net_cache = NetStatsCache()
energy_cache = EnergyStatsCache()


def _run_ssh(host: str, username: str, password: Optional[str], key: Optional[str], port: int, cmd: str) -> str:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        hostname=host,
        port=port,
        username=username,
        password=password,
        key_filename=key,
        timeout=10,
        banner_timeout=10,
        auth_timeout=10,
    )
    try:
        _, stdout, stderr = ssh.exec_command(cmd, timeout=15)
        out = stdout.read().decode("utf-8", "ignore")
        err = stderr.read().decode("utf-8", "ignore")
        if err and not out:
            raise RuntimeError(err.strip())
        return out
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


async def async_sample(host: str, username: str, password: Optional[str], key: Optional[str], port: int) -> Dict[str, Any]:
    out = await asyncio.to_thread(_run_ssh, host, username, password, key, port, REMOTE_SCRIPT)
    try:
        data = _parse_json_output(out)
    except json.JSONDecodeError as err:
        _LOGGER.error("Failed to decode SSH response from %s: %s", host, err)
        return {}
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

    cont_stats = _safe_list(data.get("container_stats"))
    disk_stats = _safe_list(data.get("disk_stats"))

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

    result: Dict[str, Any] = {
        "cpu": _safe_int(data.get("cpu")),
        "mem": _safe_int(data.get("mem")),
        "disk": _safe_int(data.get("disk")),
        "disk_capacity_total": disk_total_gib,
        "uptime": _safe_int(data.get("uptime")),
        "temp": _safe_float(data.get("temp")),
        "net_in": net_in,
        "net_out": net_out,
        "ram": _safe_int(data.get("ram")),
        "cores": _safe_int(data.get("cores")),
        "os": data.get("os", ""),
        "pkg_count": _safe_int(data.get("pkg_count")),
        "pkg_list": data.get("pkg_list", ""),
        "docker": _safe_int(data.get("docker")),
        "containers": data.get("containers", ""),
        "load_1": data.get("load_1"),
        "load_5": data.get("load_5"),
        "load_15": data.get("load_15"),
        "cpu_freq": data.get("cpu_freq"),
        "vnc": data.get("vnc", ""),
        "web": data.get("web", ""),
        "ssh": data.get("ssh", ""),
        "power_w": power_value,
        "energy_kwh_total": energy_total_kwh,
        "container_stats": cont_stats,
        "swap_usage": _safe_int(data.get("swap_usage")),
        "swap_total": swap_total_gib,
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

    for container in cont_stats:
        cname = _sanitize(container.get("name", ""))
        if not cname:
            continue
        cpu_value = _safe_float(container.get("cpu"))
        mem_value = _safe_float(container.get("mem"))
        result[f"container_{cname}_cpu"] = cpu_value
        result[f"container_{cname}_mem"] = mem_value

    return result
