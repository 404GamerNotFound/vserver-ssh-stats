"""Cache network statistics for rate computation."""
from __future__ import annotations

from typing import Dict, Tuple


class NetStatsCache:
    """Cache network RX/TX values to calculate transfer rates."""

    def __init__(self) -> None:
        self._last_net: Dict[str, Dict[str, int]] = {}
        self._last_ts: Dict[str, float] = {}

    def compute(self, key: str, rx: int, tx: int, now: float) -> Tuple[float, float]:
        """Update cache for *key* and return (net_in, net_out) in bytes/s."""
        last = self._last_net.get(key)
        last_ts = self._last_ts.get(key)
        net_in = net_out = 0.0
        if last and last_ts:
            dt = max(1e-6, now - last_ts)
            net_in = max(0.0, (rx - last["rx"]) / dt)
            net_out = max(0.0, (tx - last["tx"]) / dt)
        self._last_net[key] = {"rx": rx, "tx": tx}
        self._last_ts[key] = now
        return net_in, net_out
