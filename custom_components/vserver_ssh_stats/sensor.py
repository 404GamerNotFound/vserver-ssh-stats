"""Sensor platform for VServer SSH Stats."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Dict, Iterable

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
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from . import DOMAIN
from .ssh_collector import async_sample

_LOGGER = logging.getLogger(__name__)


def _sanitize(name: str) -> str:
    """Sanitize a container name for use in entity keys."""

    return re.sub(r"[^a-zA-Z0-9_]+", "_", name).lower()


@dataclass
class VServerSensorDescription(SensorEntityDescription):
    """Class describing VServer SSH Stats sensor."""


@dataclass
class ServerContainerRegistry:
    """Track container sensors that were created for a server."""

    coordinator: "VServerCoordinator"
    server_name: str
    known_containers: set[str] = field(default_factory=set)

    def _build_container_sensors(self, raw_name: str, sanitized: str) -> list["VServerSensor"]:
        """Create the sensor entities for a single container."""
        cpu_description = VServerSensorDescription(
            key=f"container_{sanitized}_cpu",
            name=f"{raw_name} CPU",
            native_unit_of_measurement=PERCENTAGE,
        )
        mem_description = VServerSensorDescription(
            key=f"container_{sanitized}_mem",
            name=f"{raw_name} Memory",
            native_unit_of_measurement=PERCENTAGE,
        )
        return [
            VServerSensor(self.coordinator, self.server_name, cpu_description),
            VServerSensor(self.coordinator, self.server_name, mem_description),
        ]

    def create_entities_from_stats(
        self, stats: Iterable[Dict[str, Any]] | None
    ) -> list["VServerSensor"]:
        """Create sensor entities for new containers found in the stats."""
        if not stats:
            return []
        new_entities: list[VServerSensor] = []
        for container in stats:
            raw_name = container.get("name")
            if not raw_name:
                continue
            sanitized = _sanitize(raw_name)
            if not sanitized or sanitized in self.known_containers:
                continue
            self.known_containers.add(sanitized)
            new_entities.extend(self._build_container_sensors(raw_name, sanitized))
        return new_entities


SENSORS: tuple[VServerSensorDescription, ...] = (
    VServerSensorDescription(key="cpu", name="CPU", native_unit_of_measurement=PERCENTAGE),
    VServerSensorDescription(key="mem", name="Memory", native_unit_of_measurement=PERCENTAGE),
    VServerSensorDescription(key="disk", name="Disk", native_unit_of_measurement=PERCENTAGE),
    VServerSensorDescription(key="net_in", name="Network In", native_unit_of_measurement="B/s"),
    VServerSensorDescription(key="net_out", name="Network Out", native_unit_of_measurement="B/s"),
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
    VServerSensorDescription(key="os", name="OS"),
    VServerSensorDescription(key="pkg_count", name="Package Count"),
    VServerSensorDescription(key="pkg_list", name="Package List"),
    VServerSensorDescription(key="docker", name="Docker Containers"),
    VServerSensorDescription(key="containers", name="Containers"),
    VServerSensorDescription(key="vnc", name="VNC Supported"),
    VServerSensorDescription(key="web", name="Web Server"),
    VServerSensorDescription(key="ssh", name="SSH Enabled"),
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
        registry = ServerContainerRegistry(coordinator, name)
        for description in SENSORS:
            entities.append(VServerSensor(coordinator, name, description))
        initial_stats: Iterable[Dict[str, Any]] | None = None
        if coordinator.data:
            initial_stats = coordinator.data.get("container_stats")
        entities.extend(registry.create_entities_from_stats(initial_stats))

        def _make_container_listener(
            registry: ServerContainerRegistry,
        ) -> Callable[[], None]:
            def _handle_update() -> None:
                data: Dict[str, Any] | None = registry.coordinator.data
                stats = data.get("container_stats") if isinstance(data, dict) else None
                new_entities = registry.create_entities_from_stats(stats)
                if new_entities:
                    async_add_entities(new_entities, update_before_add=True)

            return _handle_update

        remove_listener = coordinator.async_add_listener(
            _make_container_listener(registry)
        )
        entry.async_on_unload(remove_listener)
    async_add_entities(entities, update_before_add=True)

