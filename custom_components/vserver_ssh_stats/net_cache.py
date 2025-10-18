"""Cache network statistics for rate computation."""
from __future__ import annotations

from typing import Dict, Optional, Tuple


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


class EnergyStatsCache:
    """Cache energy counters to provide cumulative kWh readings."""

    _MICROJOULE_PER_KWH = 3_600_000_000_000.0

    def __init__(self) -> None:
        self._last_energy: Dict[str, int] = {}
        self._offset_kwh: Dict[str, float] = {}
        self._last_range: Dict[str, int] = {}

    def compute(
        self,
        key: str,
        energy_uj: Optional[int],
        energy_range_uj: Optional[int],
    ) -> Optional[float]:
        """Return cumulative energy consumption in kWh for *key*."""

        if energy_uj is None:
            self._last_energy.pop(key, None)
            self._offset_kwh.pop(key, None)
            self._last_range.pop(key, None)
            return None

        if energy_range_uj is not None:
            self._last_range[key] = energy_range_uj
        range_value = self._last_range.get(key)

        prev_energy = self._last_energy.get(key)
        offset = self._offset_kwh.get(key, 0.0)

        if prev_energy is not None:
            if energy_uj < prev_energy and range_value:
                offset += range_value / self._MICROJOULE_PER_KWH
            elif energy_uj < prev_energy:
                offset = 0.0

        total = offset + energy_uj / self._MICROJOULE_PER_KWH

        self._last_energy[key] = energy_uj
        self._offset_kwh[key] = offset

        return total
