"""Sensor platform for VServer SSH Stats."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from homeassistant.components import mqtt
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

_LOGGER = logging.getLogger(__name__)


@dataclass
class VServerSensorDescription(SensorEntityDescription):
    """Class describing VServer SSH Stats sensor."""


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
)


class VServerCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Coordinator that subscribes to MQTT for a single server."""

    def __init__(self, hass: HomeAssistant, server_name: str) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, _LOGGER, name=server_name)
        self.server_name = server_name
        self._unsub: Optional[callable] = None

    async def async_setup(self) -> None:
        """Subscribe to the MQTT topic for this server."""

        async def message_received(msg: mqtt.MqttMessage) -> None:
            try:
                payload = json.loads(msg.payload)
            except ValueError:  # pragma: no cover - depends on MQTT input
                _LOGGER.warning("Invalid payload for %s: %s", self.server_name, msg.payload)
                return
            self.async_set_updated_data(payload)

        topic = f"vserver_ssh/{self.server_name}/state"
        _LOGGER.debug("Subscribing to %s", topic)
        self._unsub = await mqtt.async_subscribe(self.hass, topic, message_received)

    async def async_unsubscribe(self) -> None:
        """Unsubscribe from the MQTT topic."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None


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
        self._attr_unique_id = f"{server_name}_{description.key}"
        self._attr_name = f"{server_name} {description.name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, server_name)},
            name=server_name,
        )

    @property
    def native_value(self) -> Any:
        """Return the value reported by the collector."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self.entity_description.key)

    async def async_will_remove_from_hass(self) -> None:
        """Handle removal by unsubscribing the coordinator."""
        await super().async_will_remove_from_hass()
        await self.coordinator.async_unsubscribe()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up VServer SSH Stats sensors based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    servers = data.get("servers", [])
    entities: list[VServerSensor] = []
    for srv in servers:
        name = srv.get("name")
        if not name:
            continue
        coordinator = VServerCoordinator(hass, name)
        await coordinator.async_setup()
        for description in SENSORS:
            entities.append(VServerSensor(coordinator, name, description))
    async_add_entities(entities)

