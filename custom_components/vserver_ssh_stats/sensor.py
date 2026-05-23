"""Sensor platform for VServer SSH Stats."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfInformation,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .coordinator import VServerCoordinator, async_get_or_create_coordinators

ACTION_STATUS_EVENT = f"{DOMAIN}_action_status"


def _sanitize(name: str) -> str:
    """Sanitize a container name for use in entity keys."""

    return re.sub(r"[^a-zA-Z0-9_]+", "_", name).lower()


@dataclass
class VServerSensorDescription(SensorEntityDescription):
    """Class describing VServer SSH Stats sensor."""


def _diagnostic_sensor(**kwargs: Any) -> VServerSensorDescription:
    """Create a diagnostic sensor description."""

    return VServerSensorDescription(entity_category=EntityCategory.DIAGNOSTIC, **kwargs)


def _as_float(value: Any) -> float | None:
    """Return *value* as float or None."""

    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _health_level(score: int) -> str:
    """Return a health state for a numeric score."""

    if score <= 70:
        return "critical"
    if score <= 90:
        return "warning"
    return "ok"


def _build_health(data: dict[str, Any], online: bool) -> dict[str, Any]:
    """Build an aggregated health state from collected server metrics."""

    if not online:
        return {
            "status": "offline",
            "score": 0,
            "reasons": ["Host is currently unreachable"],
        }

    score = 100
    reasons: list[str] = []

    def add_reason(message: str, penalty: int) -> None:
        nonlocal score
        reasons.append(message)
        score = max(0, score - penalty)

    cpu = _as_float(data.get("cpu"))
    if cpu is not None:
        if cpu >= 95:
            add_reason(f"CPU usage is critical at {cpu:.0f}%", 30)
        elif cpu >= 85:
            add_reason(f"CPU usage is high at {cpu:.0f}%", 15)

    mem = _as_float(data.get("mem"))
    if mem is not None:
        if mem >= 95:
            add_reason(f"Memory usage is critical at {mem:.0f}%", 30)
        elif mem >= 85:
            add_reason(f"Memory usage is high at {mem:.0f}%", 15)

    swap = _as_float(data.get("swap_usage"))
    if swap is not None:
        if swap >= 80:
            add_reason(f"Swap usage is critical at {swap:.0f}%", 25)
        elif swap >= 40:
            add_reason(f"Swap usage is elevated at {swap:.0f}%", 10)

    disk = _as_float(data.get("disk"))
    if disk is not None:
        if disk >= 95:
            add_reason(f"Root disk usage is critical at {disk:.0f}%", 30)
        elif disk >= 85:
            add_reason(f"Root disk usage is high at {disk:.0f}%", 15)

    for disk_stat in data.get("disk_stats", []):
        if not isinstance(disk_stat, dict):
            continue
        if disk_stat.get("mount") == "/":
            continue
        total = _as_float(disk_stat.get("total"))
        free = _as_float(disk_stat.get("free"))
        if not total or free is None:
            continue
        used_percent = 100 - (free / total * 100)
        label = disk_stat.get("label") or disk_stat.get("mount") or disk_stat.get("name")
        if used_percent >= 95:
            add_reason(f"Disk {label} is critical at {used_percent:.0f}%", 25)
        elif used_percent >= 85:
            add_reason(f"Disk {label} is high at {used_percent:.0f}%", 10)

    cores = _as_float(data.get("cores"))
    load_5 = _as_float(data.get("load_5"))
    if cores and load_5 is not None:
        load_ratio = load_5 / cores
        if load_ratio >= 2:
            add_reason(f"5-minute load is critical at {load_5:.2f} on {cores:.0f} cores", 25)
        elif load_ratio >= 1:
            add_reason(f"5-minute load is high at {load_5:.2f} on {cores:.0f} cores", 10)

    pkg_count = _as_float(data.get("pkg_count"))
    if pkg_count is not None:
        if pkg_count >= 50:
            add_reason(f"{pkg_count:.0f} package updates are pending", 10)
        elif pkg_count >= 10:
            add_reason(f"{pkg_count:.0f} package updates are pending", 5)

    ssh_connect_time = _as_float(data.get("ssh_connect_time_ms"))
    if ssh_connect_time is not None and ssh_connect_time >= 3000:
        add_reason(f"SSH connect time is high at {ssh_connect_time:.0f} ms", 10)

    collection_time = _as_float(data.get("collection_time_ms"))
    if collection_time is not None and collection_time >= 10000:
        add_reason(f"Collection time is high at {collection_time:.0f} ms", 10)

    unhealthy_containers: list[str] = []
    for container in data.get("container_stats", []):
        if not isinstance(container, dict):
            continue
        health = str(container.get("health_state") or "").lower()
        status = str(container.get("status") or "").lower()
        name = str(container.get("name") or "").strip()
        if health in {"unhealthy", "dead", "exited"} or status.startswith("exited"):
            unhealthy_containers.append(name or "unknown")
    for name in unhealthy_containers[:5]:
        add_reason(f"Container {name} is not healthy", 15)

    return {
        "status": _health_level(score),
        "score": score,
        "reasons": reasons,
    }


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


@dataclass
class ServerDiskRegistry:
    """Track disk sensors that were created for a server."""

    coordinator: "VServerCoordinator"
    server_name: str
    known_disks: set[str] = field(default_factory=set)

    def _build_disk_sensors(self, label: str, sanitized: str) -> list["VServerSensor"]:
        """Create the sensor entities for a single disk."""

        total_description = VServerSensorDescription(
            key=f"disk_{sanitized}_total",
            name=f"{label} Total",
            native_unit_of_measurement=UnitOfInformation.GIBIBYTES,
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        free_description = VServerSensorDescription(
            key=f"disk_{sanitized}_free",
            name=f"{label} Free",
            native_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        )
        return [
            VServerSensor(self.coordinator, self.server_name, total_description),
            VServerSensor(self.coordinator, self.server_name, free_description),
        ]

    def create_entities_from_stats(
        self, stats: Iterable[Dict[str, Any]] | None
    ) -> list["VServerSensor"]:
        """Create sensor entities for new disks found in the stats."""

        if not stats:
            return []
        new_entities: list[VServerSensor] = []
        for disk in stats:
            sanitized = disk.get("key")
            if not sanitized or sanitized in self.known_disks:
                continue
            label = disk.get("label") or disk.get("name") or disk.get("mount") or sanitized
            self.known_disks.add(sanitized)
            new_entities.extend(self._build_disk_sensors(label, sanitized))
        return new_entities


SENSORS: tuple[VServerSensorDescription, ...] = (
    VServerSensorDescription(key="health_status", name="Health Status"),
    VServerSensorDescription(key="cpu", name="CPU", native_unit_of_measurement=PERCENTAGE),
    VServerSensorDescription(key="mem", name="Memory", native_unit_of_measurement=PERCENTAGE),
    VServerSensorDescription(
        key="swap_usage",
        name="Swap Usage",
        native_unit_of_measurement=PERCENTAGE,
    ),
    _diagnostic_sensor(
        key="swap_total",
        name="Swap Total",
        native_unit_of_measurement=UnitOfInformation.GIBIBYTES,
    ),
    VServerSensorDescription(key="disk", name="Disk", native_unit_of_measurement=PERCENTAGE),
    _diagnostic_sensor(
        key="disk_capacity_total",
        name="Disk Capacity Total",
        native_unit_of_measurement=UnitOfInformation.GIBIBYTES,
    ),
    VServerSensorDescription(
        key="power_w",
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    VServerSensorDescription(
        key="energy_kwh_total",
        name="Energy Total",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    VServerSensorDescription(key="net_in", name="Network In", native_unit_of_measurement="B/s"),
    VServerSensorDescription(key="net_out", name="Network Out", native_unit_of_measurement="B/s"),
    _diagnostic_sensor(
        key="ssh_connect_time_ms",
        name="SSH Connect Time",
        native_unit_of_measurement="ms",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _diagnostic_sensor(
        key="collection_time_ms",
        name="Collection Time",
        native_unit_of_measurement="ms",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _diagnostic_sensor(
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
    _diagnostic_sensor(key="ram", name="RAM", native_unit_of_measurement="MB"),
    _diagnostic_sensor(key="cores", name="Cores"),
    VServerSensorDescription(key="load_1", name="Load 1"),
    VServerSensorDescription(key="load_5", name="Load 5"),
    VServerSensorDescription(key="load_15", name="Load 15"),
    _diagnostic_sensor(
        key="cpu_freq",
        name="CPU Frequency",
        native_unit_of_measurement="MHz",
        device_class=SensorDeviceClass.FREQUENCY,
    ),
    _diagnostic_sensor(key="os", name="OS"),
    VServerSensorDescription(key="pkg_count", name="Package Count"),
    _diagnostic_sensor(key="pkg_list", name="Package List"),
    _diagnostic_sensor(key="docker", name="Docker Containers"),
    VServerSensorDescription(key="containers", name="Containers"),
    _diagnostic_sensor(key="top_processes", name="Top Processes"),
    _diagnostic_sensor(key="vnc", name="VNC Supported"),
    _diagnostic_sensor(key="web", name="Web Server"),
    _diagnostic_sensor(key="ssh", name="SSH Enabled"),
)

ACTION_STATUS_SENSORS: tuple[tuple[str, str], ...] = (
    ("update_packages", "Last Package Update Status"),
    ("reboot_host", "Last Reboot Status"),
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
        if self.entity_description.key == "health_status":
            health = _build_health(
                self.coordinator.data if isinstance(self.coordinator.data, dict) else {},
                self.coordinator.last_update_success,
            )
            return health["status"]
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self.entity_description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional context for complex sensor values."""

        if self.entity_description.key == "health_status":
            health = _build_health(
                self.coordinator.data if isinstance(self.coordinator.data, dict) else {},
                self.coordinator.last_update_success,
            )
            return {
                "score": health["score"],
                "reasons": health["reasons"],
            }
        if not self.coordinator.data:
            return None
        if self.entity_description.key == "top_processes":
            return {
                "processes": self.coordinator.data.get("top_process_details", []),
            }
        if self.entity_description.key == "containers":
            return {
                "containers": self.coordinator.data.get("container_details", []),
            }
        return None


class VServerActionStatusSensor(SensorEntity):
    """Sensor that exposes the latest remote action result for a server."""

    def __init__(
        self,
        hass: HomeAssistant,
        server: dict[str, Any],
        action: str,
        name: str,
    ) -> None:
        """Initialize the action status sensor."""

        self.hass = hass
        self._host = server["host"]
        self._action = action
        self._status_data: dict[str, Any] = self._load_status_data()
        self._attr_unique_id = f"{self._host}_{action}_status"
        self._attr_name = f"{server['name']} {name}"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=server["name"],
        )

    def _load_status_data(self) -> dict[str, Any]:
        """Return the stored status data for this host/action."""

        domain_data = self.hass.data.get(DOMAIN, {})
        action_status = domain_data.get("action_status", {})
        host_status = action_status.get(self._host, {})
        status = host_status.get(self._action, {})
        return dict(status) if isinstance(status, dict) else {}

    @property
    def native_value(self) -> str:
        """Return the latest action status."""

        return str(self._status_data.get("status") or "never_run")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return action output and timing attributes."""

        return {
            "success": self._status_data.get("success"),
            "last_run": self._status_data.get("timestamp"),
            "output": self._status_data.get("output", ""),
        }

    async def async_added_to_hass(self) -> None:
        """Listen for action status updates."""

        self.async_on_remove(
            self.hass.bus.async_listen(ACTION_STATUS_EVENT, self._handle_action_event)
        )

    @callback
    def _handle_action_event(self, event: Event) -> None:
        """Update the entity when a matching action event is fired."""

        data = event.data
        if data.get("host") != self._host or data.get("action") != self._action:
            return
        self._status_data = dict(data)
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up VServer SSH Stats sensors based on a config entry."""
    entities: list[VServerSensor] = []
    registries: list[tuple[ServerContainerRegistry, ServerDiskRegistry, str]] = []
    coordinators = await async_get_or_create_coordinators(hass, entry)
    for coordinator in coordinators:
        name = coordinator.server.get("name")
        if not name:
            continue
        container_registry = ServerContainerRegistry(coordinator, name)
        disk_registry = ServerDiskRegistry(coordinator, name)
        registries.append((container_registry, disk_registry, name))
        for description in SENSORS:
            entities.append(VServerSensor(coordinator, name, description))
        for action, action_name in ACTION_STATUS_SENSORS:
            entities.append(
                VServerActionStatusSensor(hass, coordinator.server, action, action_name)
            )
    for container_registry, disk_registry, _name in registries:
        coordinator = container_registry.coordinator
        stats = coordinator.data if isinstance(coordinator.data, dict) else {}
        initial_stats = stats.get("container_stats")
        disk_initial_stats = stats.get("disk_stats")
        entities.extend(container_registry.create_entities_from_stats(initial_stats))
        entities.extend(disk_registry.create_entities_from_stats(disk_initial_stats))

        def _make_container_listener(
            container_registry: ServerContainerRegistry,
            disk_registry: ServerDiskRegistry,
        ) -> Callable[[], None]:
            def _handle_update() -> None:
                data: Dict[str, Any] | None = container_registry.coordinator.data
                stats = data.get("container_stats") if isinstance(data, dict) else None
                new_containers = container_registry.create_entities_from_stats(stats)
                if new_containers:
                    async_add_entities(new_containers, update_before_add=True)
                disk_stats = data.get("disk_stats") if isinstance(data, dict) else None
                new_disks = disk_registry.create_entities_from_stats(disk_stats)
                if new_disks:
                    async_add_entities(new_disks, update_before_add=True)

            return _handle_update

        remove_listener = coordinator.async_add_listener(
            _make_container_listener(container_registry, disk_registry)
        )
        entry.async_on_unload(remove_listener)
    async_add_entities(entities, update_before_add=True)
