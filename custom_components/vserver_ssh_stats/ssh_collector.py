"""SSH utilities for VServer SSH Stats integration."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, Optional

import paramiko

from .net_cache import NetStatsCache
from .remote_script import REMOTE_SCRIPT

net_cache = NetStatsCache()


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
    result: Dict[str, Any] = {
        "cpu": int(data["cpu"]),
        "mem": int(data["mem"]),
        "disk": int(data["disk"]),
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
        "container_stats": cont_stats,
    }
    for c in cont_stats:
        cname = _sanitize(c.get("name", ""))
        result[f"container_{cname}_cpu"] = c.get("cpu", 0)
        result[f"container_{cname}_mem"] = c.get("mem", 0)
    return result
