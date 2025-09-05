"""Sensor platform for VServer SSH Stats."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from . import DOMAIN
from .ssh_collector import async_sample

_LOGGER = logging.getLogger(__name__)


def _sanitize(name: str) -> str:
    """Sanitize a container name for use in entity keys."""
    import re

    return re.sub(r"[^a-zA-Z0-9_]+", "_", name).lower()


@dataclass
class VServerSensorDescription(SensorEntityDescription):
    """Class describing VServer SSH Stats sensor."""


SENSORS: tuple[VServerSensorDescription, ...] = (
    VServerSensorDescription(key="cpu", name="CPU", native_unit_of_measurement=PERCENTAGE),
    VServerSensorDescription(key="mem", name="Memory", native_unit_of_measurement=PERCENTAGE),
    VServerSensorDescription(key="swap", name="Swap", native_unit_of_measurement=PERCENTAGE),
    VServerSensorDescription(key="disk", name="Disk", native_unit_of_measurement=PERCENTAGE),
    VServerSensorDescription(key="net_in", name="Network In", native_unit_of_measurement="B/s"),
    VServerSensorDescription(key="net_out", name="Network Out", native_unit_of_measurement="B/s"),
    VServerSensorDescription(key="disk_read", name="Disk Read", native_unit_of_measurement="B/s"),
    VServerSensorDescription(key="disk_write", name="Disk Write", native_unit_of_measurement="B/s"),
    VServerSensorDescription(
        key="uptime",
        name="Uptime",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
    ),
    VServerSensorDescription(
        key="temp",
        name="Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
    ),
    VServerSensorDescription(key="ram", name="RAM", native_unit_of_measurement="MB"),
    VServerSensorDescription(key="cores", name="Cores"),
    VServerSensorDescription(key="load_1", name="Load 1"),
    VServerSensorDescription(key="load_5", name="Load 5"),
    VServerSensorDescription(key="load_15", name="Load 15"),
    VServerSensorDescription(
        key="cpu_freq",
        name="CPU Frequency",
        native_unit_of_measurement="MHz",
        device_class=SensorDeviceClass.FREQUENCY,
    ),
    VServerSensorDescription(
        key="os",
        name="OS",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=True,
    ),
    VServerSensorDescription(
        key="pkg_count",
        name="Package Count",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=True,
    ),
    VServerSensorDescription(
        key="pkg_list",
        name="Package List",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=True,
    ),
    VServerSensorDescription(key="docker", name="Docker Containers", entity_category=EntityCategory.DIAGNOSTIC),
    VServerSensorDescription(key="containers", name="Containers", entity_category=EntityCategory.DIAGNOSTIC),
    VServerSensorDescription(
        key="vnc",
        name="VNC Supported",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=True,
    ),
    VServerSensorDescription(
        key="web",
        name="Web Server",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=True,
    ),
    VServerSensorDescription(
        key="ssh",
        name="SSH Enabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=True,
    ),
    VServerSensorDescription(
        key="local_ip",
        name="Local IP",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=True,
    ),
)


class VServerCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Coordinator that polls a server via SSH."""

    def __init__(self, hass: HomeAssistant, server: Dict[str, Any], interval: int) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=server["name"],
            update_interval=timedelta(seconds=interval),
        )
        self.server = server

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from the server."""
        return await async_sample(
            self.server["host"],
            self.server["username"],
            self.server.get("password"),
            self.server.get("key"),
            self.server.get("port", 22),
        )


class VServerSensor(CoordinatorEntity[VServerCoordinator], SensorEntity):
    """Representation of a VServer SSH Stats sensor."""

    entity_description: VServerSensorDescription

    def __init__(
        self,
        coordinator: VServerCoordinator,
        server_name: str,
        description: VServerSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        host = coordinator.server["host"]
        self._attr_unique_id = f"{host}_{description.key}"
        self._attr_name = f"{server_name} {description.name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, host)},
            name=server_name,
        )

    @property
    def native_value(self) -> Any:
        """Return the value reported by the collector."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self.entity_description.key)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up VServer SSH Stats sensors based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    servers = data.get("servers", [])
    interval = data.get("interval", 30)
    entities: list[VServerSensor] = []
    for srv in servers:
        name = srv.get("name")
        if not name:
            continue
        coordinator = VServerCoordinator(hass, srv, interval)
        await coordinator.async_config_entry_first_refresh()
        for description in SENSORS:
            entities.append(VServerSensor(coordinator, name, description))
        for cont in coordinator.data.get("container_stats", []):
            cname = cont.get("name")
            if not cname:
                continue
            sanitized = _sanitize(cname)
            entities.append(
                VServerSensor(
                    coordinator,
                    name,
                    VServerSensorDescription(
                        key=f"container_{sanitized}_cpu",
                        name=f"{cname} CPU",
                        native_unit_of_measurement=PERCENTAGE,
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                )
            )
            entities.append(
                VServerSensor(
                    coordinator,
                    name,
                    VServerSensorDescription(
                        key=f"container_{sanitized}_mem",
                        name=f"{cname} Memory",
                        native_unit_of_measurement=PERCENTAGE,
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                )
            )
        for key in coordinator.data.keys():
            if not key.startswith("sensor_"):
                continue
            pretty = key[7:].replace("_", " ").title()
            unit = None
            device_class = None
            lower = key.lower()
            if "temp" in lower:
                unit = UnitOfTemperature.CELSIUS
                device_class = SensorDeviceClass.TEMPERATURE
            elif "fan" in lower:
                unit = "RPM"
            elif "power" in lower:
                unit = "W"
            elif lower.startswith("sensor_in") or "volt" in lower:
                unit = "V"
            entities.append(
                VServerSensor(
                    coordinator,
                    name,
                    VServerSensorDescription(
                        key=key,
                        name=pretty,
                        native_unit_of_measurement=unit,
                        device_class=device_class,
                        entity_category=EntityCategory.DIAGNOSTIC,
                    ),
                )
            )
    async_add_entities(entities)

