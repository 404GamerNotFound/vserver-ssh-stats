"""Cache disk I/O statistics for rate computation."""
from __future__ import annotations

from typing import Dict, Tuple


class DiskStatsCache:
    """Cache disk read/write values to calculate throughput."""

    def __init__(self) -> None:
        self._last_disk: Dict[str, Dict[str, int]] = {}
        self._last_ts: Dict[str, float] = {}

    def compute(self, key: str, read: int, write: int, now: float) -> Tuple[float, float]:
        """Update cache for *key* and return (read_rate, write_rate) in bytes/s."""
        last = self._last_disk.get(key)
        last_ts = self._last_ts.get(key)
        read_rate = write_rate = 0.0
        if last and last_ts:
            dt = max(1e-6, now - last_ts)
            read_rate = max(0.0, (read - last["read"]) / dt)
            write_rate = max(0.0, (write - last["write"]) / dt)
        self._last_disk[key] = {"read": read, "write": write}
        self._last_ts[key] = now
        return read_rate, write_rate
