"""SSH utilities for VServer SSH Stats integration."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, Optional

import paramiko

from .net_cache import EnergyStatsCache, NetStatsCache
from .remote_script import REMOTE_SCRIPT

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


async def async_sample(host: str, username: str, password: Optional[str], key: Optional[str], port: int) -> Dict[str, Any]:
    out = await asyncio.to_thread(_run_ssh, host, username, password, key, port, REMOTE_SCRIPT)
    data = json.loads(out.strip())
    now = time.time()
    net_in, net_out = net_cache.compute(host, data["rx"], data["tx"], now)
    cont_stats = data.get("container_stats", [])
    disk_stats = data.get("disk_stats", [])
    disk_total_bytes = int(data.get("disk_capacity_total", 0))
    power_w_raw = data.get("power_w")
    energy_uj_raw = data.get("energy_uj")
    energy_range_raw = data.get("energy_range_uj")
    power_value: Optional[float]
    if power_w_raw is None:
        power_value = None
    else:
        power_value = round(float(power_w_raw), 2)
    energy_uj: Optional[int]
    if energy_uj_raw is None:
        energy_uj = None
    else:
        try:
            energy_uj = int(energy_uj_raw)
        except (TypeError, ValueError):
            energy_uj = None
    energy_range: Optional[int]
    if energy_range_raw is None:
        energy_range = None
    else:
        try:
            energy_range = int(energy_range_raw)
        except (TypeError, ValueError):
            energy_range = None
    energy_total_kwh = energy_cache.compute(host, energy_uj, energy_range)
    result: Dict[str, Any] = {
        "cpu": int(data["cpu"]),
        "mem": int(data["mem"]),
        "disk": int(data["disk"]),
        "disk_capacity_total": round(disk_total_bytes / (1024 ** 3), 2)
        if disk_total_bytes
        else None,
        "uptime": int(data["uptime"]),
        "temp": (None if data["temp"] is None else float(data["temp"])),
        "net_in": round(net_in, 2),
        "net_out": round(net_out, 2),
        "ram": int(data.get("ram", 0)),
        "cores": int(data.get("cores", 0)),
        "os": data.get("os", ""),
        "pkg_count": int(data.get("pkg_count", 0)),
        "pkg_list": data.get("pkg_list", ""),
        "docker": int(data.get("docker", 0)),
        "containers": data.get("containers", ""),
        "load_1": data.get("load_1"),
        "load_5": data.get("load_5"),
        "load_15": data.get("load_15"),
        "cpu_freq": data.get("cpu_freq"),
        "vnc": data.get("vnc", ""),
        "web": data.get("web", ""),
        "ssh": data.get("ssh", ""),
        "power_w": power_value,
        "energy_kwh_total": (None if energy_total_kwh is None else round(energy_total_kwh, 5)),
        "container_stats": cont_stats,
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
        total_bytes = int(disk.get("total", 0) or 0)
        free_bytes = int(disk.get("free", 0) or 0)
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
    for c in cont_stats:
        cname = _sanitize(c.get("name", ""))
        result[f"container_{cname}_cpu"] = c.get("cpu", 0)
        result[f"container_{cname}_mem"] = c.get("mem", 0)
    return result
