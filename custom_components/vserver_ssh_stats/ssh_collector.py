"""SSH utilities for VServer SSH Stats integration."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, Optional

import paramiko

from .net_cache import NetStatsCache
from .disk_cache import DiskStatsCache
from .remote_script import REMOTE_SCRIPT

net_cache = NetStatsCache()
disk_cache = DiskStatsCache()


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
        # Some commands like package manager checks can take a bit longer on
        # slower systems. Give the remote script more time to finish so the
        # integration still receives data instead of timing out early.
        _, stdout, stderr = ssh.exec_command(cmd, timeout=60)
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


def _flatten_sensors(data: Any) -> Dict[str, float]:
    """Flatten lm-sensors JSON output to key/value pairs."""

    result: Dict[str, float] = {}

    def _recurse(prefix: str, obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                ksan = _sanitize(str(k))
                new_prefix = f"{prefix}_{ksan}" if prefix else ksan
                _recurse(new_prefix, v)
        else:
            try:
                result[f"sensor_{prefix}"] = float(obj)
            except (TypeError, ValueError):
                pass

    _recurse("", data)
    return result


async def async_sample(host: str, username: str, password: Optional[str], key: Optional[str], port: int) -> Dict[str, Any]:
    out = await asyncio.to_thread(_run_ssh, host, username, password, key, port, REMOTE_SCRIPT)
    data = json.loads(out.strip())
    now = time.time()
    net_in, net_out = net_cache.compute(host, data["rx"], data["tx"], now)
    d_read, d_write = disk_cache.compute(host, data["dread"], data["dwrite"], now)
    cont_stats = data.get("container_stats", [])
    sensors = _flatten_sensors(data.get("sensors", {}))
    result: Dict[str, Any] = {
        "cpu": int(data["cpu"]),
        "mem": int(data["mem"]),
        "swap": int(data.get("swap", 0)),
        "disk": int(data["disk"]),
        "uptime": int(data["uptime"]),
        "temp": (None if data["temp"] is None else float(data["temp"])),
        "net_in": round(net_in, 2),
        "net_out": round(net_out, 2),
        "disk_read": round(d_read, 2),
        "disk_write": round(d_write, 2),
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
        "local_ip": data.get("local_ip", ""),
        "container_stats": cont_stats,
    }
    for c in cont_stats:
        cname = _sanitize(c.get("name", ""))
        result[f"container_{cname}_cpu"] = c.get("cpu", 0)
        result[f"container_{cname}_mem"] = c.get("mem", 0)
    result.update(sensors)
    return result
