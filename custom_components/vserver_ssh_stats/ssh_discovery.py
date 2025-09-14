"""Discover SSH servers on the local network."""
from __future__ import annotations

import asyncio
from ipaddress import ip_network
import socket
from typing import List

DEFAULT_TIMEOUT = 1.0


async def _probe_host(host: str, port: int = 22, timeout: float = DEFAULT_TIMEOUT) -> bool:
    """Return True if *host* has the given *port* open."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (asyncio.TimeoutError, OSError):
        return False
    writer.close()
    await writer.wait_closed()
    return True


async def discover_ssh_hosts(network: str) -> List[str]:
    """Discover hosts with an open SSH port in *network* (CIDR notation)."""
    net = ip_network(network, strict=False)
    hosts = [str(ip) for ip in net.hosts()]
    tasks = [_probe_host(h) for h in hosts]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [h for h, ok in zip(hosts, results) if ok]


def guess_local_network() -> str:
    """Return a best-effort guess for the local /24 network."""
    ip = socket.gethostbyname(socket.gethostname())
    return f"{ip}/24"
