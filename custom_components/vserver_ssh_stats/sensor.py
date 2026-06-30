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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN
from .coordinator import (
    CustomCommandCoordinator,
    VServerCoordinator,
    async_get_or_create_coordinators,
    async_get_or_create_custom_sensor_coordinators,
)
from .docker_entities import find_container
from .util import build_container_device_info, build_device_info, build_storage_device_info

ACTION_STATUS_EVENT = f"{DOMAIN}_action_status"
MAX_SENSOR_STATE_LENGTH = 255


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

    if data.get("reboot_required"):
        add_reason("Reboot is required", 10)

    if data.get("root_fs_readonly"):
        add_reason("Root filesystem is mounted read-only", 40)

    security_updates = _as_float(data.get("security_updates"))
    if security_updates is not None:
        if security_updates >= 10:
            add_reason(f"{security_updates:.0f} security updates are pending", 15)
        elif security_updates >= 1:
            add_reason(f"{security_updates:.0f} security updates are pending", 8)

    failed_units = _as_float(data.get("failed_systemd_units"))
    if failed_units is not None and failed_units > 0:
        penalty = min(30, 10 + int(failed_units) * 5)
        add_reason(f"{failed_units:.0f} systemd units failed", penalty)

    journal_errors = _as_float(data.get("journal_errors"))
    if journal_errors is not None:
        if journal_errors >= 20:
            add_reason(f"{journal_errors:.0f} journal errors in the last 15 minutes", 15)
        elif journal_errors >= 1:
            add_reason(f"{journal_errors:.0f} journal errors in the last 15 minutes", 5)

    unhealthy_containers: list[str] = []
    for container in data.get("container_stats", []):
        if not isinstance(container, dict):
            continue
        health = str(container.get("health_state") or "").lower()
        status = str(container.get("status") or "").lower()
        name = str(container.get("name") or "").strip()
        memory_limit_usage = _as_float(container.get("memory_limit_usage"))
        exited_with_error = status.startswith("exited") and not status.startswith(
            "exited (0)"
        )
        if health in {"unhealthy", "dead"} or exited_with_error:
            unhealthy_containers.append(name or "unknown")
        if memory_limit_usage is not None and memory_limit_usage >= 100:
            add_reason(
                f"Container {name or 'unknown'} reached its memory limit",
                20,
            )
        elif memory_limit_usage is not None and memory_limit_usage >= 90:
            add_reason(
                f"Container {name or 'unknown'} is near its memory limit",
                10,
            )
    for name in unhealthy_containers[:5]:
        add_reason(f"Container {name} is not healthy", 15)

    zombies = _as_float(data.get("process_zombies"))
    if zombies is not None and zombies > 0:
        add_reason(f"{zombies:.0f} zombie processes detected", min(15, 5 + int(zombies)))

    if data.get("software_raid_degraded"):
        add_reason("Software RAID is degraded", 35)
    if data.get("smart_failure_detected"):
        add_reason("A storage device reports SMART failure", 40)
    elif data.get("storage_collection_error"):
        add_reason("Storage health data is incomplete", 10)

    conntrack_usage = _as_float(data.get("conntrack_usage"))
    if conntrack_usage is not None:
        if conntrack_usage >= 95:
            add_reason(f"Conntrack usage is critical at {conntrack_usage:.0f}%", 25)
        elif conntrack_usage >= 80:
            add_reason(f"Conntrack usage is high at {conntrack_usage:.0f}%", 10)

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
        device_info = build_container_device_info(
            DOMAIN,
            self.coordinator.server,
            raw_name,
            sanitized,
        )
        cpu_description = VServerSensorDescription(
            key=f"container_{sanitized}_cpu",
            name=f"{raw_name} CPU",
            native_unit_of_measurement=PERCENTAGE,
            state_class=SensorStateClass.MEASUREMENT,
        )
        mem_description = VServerSensorDescription(
            key=f"container_{sanitized}_mem",
            name=f"{raw_name} Memory",
            native_unit_of_measurement=PERCENTAGE,
            state_class=SensorStateClass.MEASUREMENT,
        )
        metrics = (
            cpu_description,
            mem_description,
            VServerSensorDescription(
                key=f"container_{sanitized}_memory_usage_bytes",
                name=f"{raw_name} Memory Usage",
                native_unit_of_measurement=UnitOfInformation.BYTES,
                device_class=SensorDeviceClass.DATA_SIZE,
                state_class=SensorStateClass.MEASUREMENT,
            ),
            VServerSensorDescription(
                key=f"container_{sanitized}_memory_limit_bytes",
                name=f"{raw_name} Memory Limit",
                native_unit_of_measurement=UnitOfInformation.BYTES,
                device_class=SensorDeviceClass.DATA_SIZE,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            VServerSensorDescription(
                key=f"container_{sanitized}_memory_limit_usage",
                name=f"{raw_name} Memory Limit Usage",
                native_unit_of_measurement=PERCENTAGE,
                state_class=SensorStateClass.MEASUREMENT,
            ),
            VServerSensorDescription(
                key=f"container_{sanitized}_pids",
                name=f"{raw_name} PIDs",
            ),
            VServerSensorDescription(
                key=f"container_{sanitized}_cpu_throttled_periods",
                name=f"{raw_name} CPU Throttled Periods",
                state_class=SensorStateClass.TOTAL_INCREASING,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            VServerSensorDescription(
                key=f"container_{sanitized}_cpu_throttled_seconds",
                name=f"{raw_name} CPU Throttled Time",
                native_unit_of_measurement=UnitOfTime.SECONDS,
                state_class=SensorStateClass.TOTAL_INCREASING,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
        )
        metric_names = (
            "cpu",
            "mem",
            "memory_usage_bytes",
            "memory_limit_bytes",
            "memory_limit_usage",
            "pids",
            "cpu_throttled_periods",
            "cpu_throttled_seconds",
        )
        return [
            VServerSensor(
                self.coordinator,
                self.server_name,
                description,
                device_info,
                container_key=sanitized,
                container_metric=metric,
            )
            for description, metric in zip(metrics, metric_names, strict=True)
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

    def create_entities_from_registry(
        self, entries: Iterable[Any]
    ) -> list["VServerSensor"]:
        """Recreate previously registered dynamic container sensors."""

        host = self.coordinator.server["host"]
        unique_id_prefix = f"{host}_container_"
        container_names: dict[str, str] = {}
        for entry in entries:
            unique_id = str(getattr(entry, "unique_id", ""))
            if not unique_id.startswith(unique_id_prefix):
                continue
            key = unique_id[len(f"{host}_") :]
            if key.endswith("_cpu"):
                sanitized = key[len("container_") : -len("_cpu")]
                metric_name = " CPU"
            elif key.endswith("_mem"):
                sanitized = key[len("container_") : -len("_mem")]
                metric_name = " Memory"
            else:
                continue
            if not sanitized:
                continue

            registered_name = str(
                getattr(entry, "original_name", None)
                or getattr(entry, "name", None)
                or ""
            )
            server_prefix = f"{self.server_name} "
            if registered_name.startswith(server_prefix):
                registered_name = registered_name[len(server_prefix) :]
            if registered_name.endswith(metric_name):
                registered_name = registered_name[: -len(metric_name)]
            container_names.setdefault(
                sanitized,
                registered_name or sanitized.replace("_", "-"),
            )

        new_entities: list[VServerSensor] = []
        for sanitized, raw_name in container_names.items():
            if sanitized in self.known_containers:
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
            state_class=SensorStateClass.MEASUREMENT,
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


@dataclass
class ServerStorageRegistry:
    """Track SMART/NVMe sensors created for physical storage devices."""

    coordinator: "VServerCoordinator"
    server_name: str
    known_devices: set[str] = field(default_factory=set)

    def create_entities_from_stats(
        self, stats: Iterable[Dict[str, Any]] | None
    ) -> list["VServerSensor"]:
        """Create child-device sensors for newly discovered physical drives."""

        if not stats:
            return []
        new_entities: list[VServerSensor] = []
        metric_descriptions = (
            ("smart_status", "SMART Status", None, None),
            (
                "temperature",
                "Temperature",
                UnitOfTemperature.CELSIUS,
                SensorDeviceClass.TEMPERATURE,
            ),
            ("wear_percent", "Wear Used", PERCENTAGE, None),
            ("media_errors", "Media Errors", None, None),
            ("reallocated_sectors", "Reallocated Sectors", None, None),
            ("pending_sectors", "Pending Sectors", None, None),
            ("uncorrectable_sectors", "Uncorrectable Sectors", None, None),
            ("power_on_hours", "Power On Hours", UnitOfTime.HOURS, None),
        )
        for device in stats:
            key = str(device.get("key") or "")
            if not key or key in self.known_devices:
                continue
            self.known_devices.add(key)
            device_info = build_storage_device_info(
                DOMAIN,
                self.coordinator.server,
                device,
            )
            name = str(device.get("name") or key)
            for metric, label, unit, device_class in metric_descriptions:
                description = VServerSensorDescription(
                    key=f"storage_{key}_{metric}",
                    name=f"{name} {label}",
                    native_unit_of_measurement=unit,
                    device_class=device_class,
                    state_class=(
                        SensorStateClass.MEASUREMENT
                        if metric in {"temperature", "wear_percent"}
                        else None
                    ),
                    entity_category=(
                        EntityCategory.DIAGNOSTIC
                        if metric not in {"temperature", "wear_percent"}
                        else None
                    ),
                )
                new_entities.append(
                    VServerSensor(
                        self.coordinator,
                        self.server_name,
                        description,
                        device_info,
                        storage_key=key,
                        storage_metric=metric,
                    )
                )
        return new_entities


SENSORS: tuple[VServerSensorDescription, ...] = (
    VServerSensorDescription(key="health_status", name="Health Status"),
    _diagnostic_sensor(
        key="health_score",
        name="Health Score",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    VServerSensorDescription(
        key="cpu",
        name="CPU",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    VServerSensorDescription(
        key="mem",
        name="Memory",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    VServerSensorDescription(
        key="swap_usage",
        name="Swap Usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _diagnostic_sensor(
        key="swap_total",
        name="Swap Total",
        native_unit_of_measurement=UnitOfInformation.GIBIBYTES,
    ),
    VServerSensorDescription(
        key="disk",
        name="Disk",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _diagnostic_sensor(
        key="disk_capacity_total",
        name="Disk Capacity Total",
        native_unit_of_measurement=UnitOfInformation.GIBIBYTES,
    ),
    VServerSensorDescription(
        key="disk_io_read",
        name="Disk I/O Read",
        native_unit_of_measurement="B/s",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    VServerSensorDescription(
        key="disk_io_write",
        name="Disk I/O Write",
        native_unit_of_measurement="B/s",
        state_class=SensorStateClass.MEASUREMENT,
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
    VServerSensorDescription(
        key="net_in",
        name="Network In",
        native_unit_of_measurement="B/s",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    VServerSensorDescription(
        key="net_out",
        name="Network Out",
        native_unit_of_measurement="B/s",
        state_class=SensorStateClass.MEASUREMENT,
    ),
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
    _diagnostic_sensor(key="collection_error", name="Collection Error"),
    _diagnostic_sensor(key="last_collection_failed", name="Last Collection Failed"),
    _diagnostic_sensor(
        key="package_collection_time_ms",
        name="Package Collection Time",
        native_unit_of_measurement="ms",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _diagnostic_sensor(key="package_collection_error", name="Package Collection Error"),
    _diagnostic_sensor(
        key="docker_collection_time_ms",
        name="Docker Collection Time",
        native_unit_of_measurement="ms",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _diagnostic_sensor(key="docker_collection_error", name="Docker Collection Error"),
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
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _diagnostic_sensor(key="cpu_temperature_status", name="CPU Temperature Status"),
    _diagnostic_sensor(key="ram", name="RAM", native_unit_of_measurement="MB"),
    _diagnostic_sensor(key="cores", name="Cores"),
    VServerSensorDescription(
        key="load_1",
        name="Load 1",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    VServerSensorDescription(
        key="load_5",
        name="Load 5",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    VServerSensorDescription(
        key="load_15",
        name="Load 15",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _diagnostic_sensor(
        key="cpu_freq",
        name="CPU Frequency",
        native_unit_of_measurement="MHz",
        device_class=SensorDeviceClass.FREQUENCY,
    ),
    _diagnostic_sensor(key="os", name="OS"),
    _diagnostic_sensor(key="last_boot", name="Last Boot"),
    _diagnostic_sensor(key="kernel_version", name="Kernel Version"),
    VServerSensorDescription(key="pkg_count", name="Package Count"),
    _diagnostic_sensor(key="pkg_list", name="Package List"),
    VServerSensorDescription(key="security_updates", name="Security Updates"),
    _diagnostic_sensor(key="docker", name="Docker Containers"),
    VServerSensorDescription(key="containers", name="Containers"),
    VServerSensorDescription(key="docker_unhealthy_containers", name="Unhealthy Containers"),
    _diagnostic_sensor(key="docker_restart_count_total", name="Docker Restart Count Total"),
    VServerSensorDescription(
        key="docker_images_size_bytes",
        name="Docker Images Disk Usage",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
    ),
    VServerSensorDescription(
        key="docker_containers_size_bytes",
        name="Docker Containers Disk Usage",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
    ),
    VServerSensorDescription(
        key="docker_volumes_size_bytes",
        name="Docker Volumes Disk Usage",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
    ),
    VServerSensorDescription(
        key="docker_build_cache_size_bytes",
        name="Docker Build Cache Disk Usage",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
    ),
    _diagnostic_sensor(key="top_processes", name="Top Processes"),
    _diagnostic_sensor(key="process_total", name="Process Count"),
    _diagnostic_sensor(key="process_running", name="Running Processes"),
    _diagnostic_sensor(key="process_zombies", name="Zombie Processes"),
    _diagnostic_sensor(key="process_peak_since_boot", name="Peak Processes Since Boot"),
    _diagnostic_sensor(key="tcp_established", name="Established TCP Connections"),
    _diagnostic_sensor(key="tcp_time_wait", name="TCP TIME-WAIT Connections"),
    _diagnostic_sensor(key="sockets_used", name="Used Sockets"),
    _diagnostic_sensor(key="tcp_sockets_in_use", name="TCP Sockets In Use"),
    _diagnostic_sensor(key="conntrack_count", name="Conntrack Entries"),
    _diagnostic_sensor(key="conntrack_max", name="Conntrack Maximum"),
    VServerSensorDescription(
        key="conntrack_usage",
        name="Conntrack Usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _diagnostic_sensor(key="software_raid_arrays", name="Software RAID Arrays"),
    VServerSensorDescription(
        key="software_raid_rebuild_progress",
        name="Software RAID Rebuild Progress",
        native_unit_of_measurement=PERCENTAGE,
    ),
    _diagnostic_sensor(
        key="software_raid_rebuild_remaining_minutes",
        name="Software RAID Rebuild Remaining",
        native_unit_of_measurement=UnitOfTime.MINUTES,
    ),
    _diagnostic_sensor(key="smart_failed_devices", name="SMART Failed Devices"),
    _diagnostic_sensor(key="storage_tools_available", name="Storage Tools Available"),
    _diagnostic_sensor(key="storage_devices_seen", name="Storage Devices Seen"),
    _diagnostic_sensor(
        key="storage_devices_collected",
        name="Storage Devices Collected",
    ),
    _diagnostic_sensor(key="storage_device_errors", name="Storage Device Errors"),
    _diagnostic_sensor(
        key="storage_collection_time_ms",
        name="Storage Collection Time",
        native_unit_of_measurement="ms",
    ),
    _diagnostic_sensor(key="storage_collection_error", name="Storage Collection Error"),
    _diagnostic_sensor(key="failed_systemd_units", name="Failed Systemd Units"),
    _diagnostic_sensor(key="failed_systemd_units_list", name="Failed Systemd Units List"),
    _diagnostic_sensor(key="journal_errors", name="Journal Errors"),
    _diagnostic_sensor(key="network_primary_mac", name="Primary MAC"),
    _diagnostic_sensor(key="primary_ip", name="Primary IP"),
    _diagnostic_sensor(key="vnc", name="VNC Supported"),
    _diagnostic_sensor(key="web", name="Web Server"),
    _diagnostic_sensor(key="ssh", name="SSH Enabled"),
)

ACTION_STATUS_SENSORS: tuple[tuple[str, str], ...] = (
    ("update_packages", "Last Package Update Status"),
    ("update_package_list", "Last Package List Update Status"),
    ("upgrade_packages", "Last Package Upgrade Status"),
    ("reboot_host", "Last Reboot Status"),
    ("refresh", "Last Manual Refresh Status"),
    ("prune_docker", "Last Docker Prune Status"),
    ("clear_package_cache", "Last Package Cache Cleanup Status"),
    ("restart_service", "Last Service Restart Status"),
    ("restart_docker_container", "Last Docker Container Restart Status"),
    ("start_docker_container", "Last Docker Container Start Status"),
    ("stop_docker_container", "Last Docker Container Stop Status"),
    ("purge_history_keep_days", "Last History Retention Purge Status"),
    ("get_server_diagnostics", "Last Diagnostics Status"),
    ("tail_logs", "Last Log Tail Status"),
)


class VServerSensor(CoordinatorEntity[VServerCoordinator], SensorEntity):
    """Representation of a VServer SSH Stats sensor."""

    _unrecorded_attributes = frozenset(
        {"processes", "containers", "units", "arrays", "mdadm_details"}
    )
    entity_description: VServerSensorDescription

    def __init__(
        self,
        coordinator: VServerCoordinator,
        server_name: str,
        description: VServerSensorDescription,
        device_info: DeviceInfo | None = None,
        *,
        container_key: str | None = None,
        container_metric: str | None = None,
        storage_key: str | None = None,
        storage_metric: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._container_key = container_key
        self._container_metric = container_metric
        self._storage_key = storage_key
        self._storage_metric = storage_metric
        host = coordinator.server["host"]
        self._attr_unique_id = f"{host}_{description.key}"
        self._attr_name = f"{server_name} {description.name}"
        self._attr_device_info = device_info or build_device_info(
            DOMAIN,
            coordinator.server,
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
        if self.entity_description.key == "health_score":
            health = _build_health(
                self.coordinator.data if isinstance(self.coordinator.data, dict) else {},
                self.coordinator.last_update_success,
            )
            return health["score"]
        if not self.coordinator.data:
            return None
        if self._container_key and self._container_metric:
            container = find_container(self.coordinator.data, self._container_key)
            if container is not None:
                return container.get(self._container_metric)
        if self._storage_key and self._storage_metric:
            lookup = self.coordinator.data.get("storage_device_lookup", {})
            device = lookup.get(self._storage_key) if isinstance(lookup, dict) else None
            if isinstance(device, dict):
                return device.get(self._storage_metric)
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
        if self.entity_description.key == "failed_systemd_units_list":
            return {
                "units": self.coordinator.data.get("failed_systemd_units_details", []),
            }
        if self.entity_description.key == "software_raid_arrays":
            return {
                "arrays": self.coordinator.data.get("raid_arrays", []),
                "mdadm_details": self.coordinator.data.get("raid_detail_arrays", []),
            }
        if self._storage_key:
            lookup = self.coordinator.data.get("storage_device_lookup", {})
            device = lookup.get(self._storage_key) if isinstance(lookup, dict) else None
            if isinstance(device, dict):
                return {
                    "path": device.get("path"),
                    "model": device.get("model"),
                    "serial": device.get("serial"),
                    "protocol": device.get("protocol"),
                }
        return None


class VServerActionStatusSensor(SensorEntity):
    """Sensor that exposes the latest remote action result for a server."""

    _unrecorded_attributes = frozenset({"output"})

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
        self._attr_device_info = build_device_info(DOMAIN, server)

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


class VServerCustomCommandSensor(
    CoordinatorEntity[CustomCommandCoordinator], SensorEntity
):
    """Sensor backed by one user-configured SSH command."""

    _unrecorded_attributes = frozenset(
        {
            "output",
            "output_truncated",
            "last_updated",
            "collection_time_ms",
            "interval_seconds",
            "timeout_seconds",
        }
    )

    def __init__(self, coordinator: CustomCommandCoordinator) -> None:
        """Initialize a custom command sensor."""

        super().__init__(coordinator)
        definition = coordinator.definition
        server = coordinator.server
        self._attr_unique_id = f"custom_{definition['id']}"
        self._attr_name = f"{server['name']} {definition['name']}"
        self._attr_icon = "mdi:console-line"
        self._attr_device_info = build_device_info(DOMAIN, server)

    @property
    def native_value(self) -> int | float | str | None:
        """Return numeric command output as a number and other output as text."""

        if not isinstance(self.coordinator.data, dict):
            return None
        output = str(self.coordinator.data.get("output") or "").strip()
        if not output:
            return None
        if re.fullmatch(r"[-+]?\d+", output):
            return int(output)
        if re.fullmatch(
            r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", output
        ):
            return float(output)
        if len(output) > MAX_SENSOR_STATE_LENGTH:
            return f"{output[: MAX_SENSOR_STATE_LENGTH - 3]}..."
        return output

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose full output and execution metadata without persisting the command."""

        if not isinstance(self.coordinator.data, dict):
            return None
        return {
            "output": self.coordinator.data.get("output", ""),
            "output_truncated": self.coordinator.data.get("output_truncated", False),
            "last_updated": self.coordinator.data.get("updated_at"),
            "collection_time_ms": self.coordinator.data.get("collection_time_ms"),
            "interval_seconds": int(self.coordinator.definition["interval"]),
            "timeout_seconds": int(self.coordinator.definition["timeout"]),
        }


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up VServer SSH Stats sensors based on a config entry."""
    entities: list[SensorEntity] = []
    registries: list[
        tuple[ServerContainerRegistry, ServerDiskRegistry, ServerStorageRegistry, str]
    ] = []
    entity_registry = er.async_get(hass)
    try:
        registry_entries = er.async_entries_for_config_entry(
            entity_registry,
            entry.entry_id,
        )
    except AttributeError:  # pragma: no cover - compatibility with older HA versions
        registry_entries = [
            registry_entry
            for registry_entry in entity_registry.entities.values()
            if registry_entry.config_entry_id == entry.entry_id
        ]
    coordinators = await async_get_or_create_coordinators(hass, entry)
    for coordinator in coordinators:
        name = coordinator.server.get("name")
        if not name:
            continue
        container_registry = ServerContainerRegistry(coordinator, name)
        disk_registry = ServerDiskRegistry(coordinator, name)
        storage_registry = ServerStorageRegistry(coordinator, name)
        registries.append((container_registry, disk_registry, storage_registry, name))
        for description in SENSORS:
            entities.append(VServerSensor(coordinator, name, description))
        for action, action_name in ACTION_STATUS_SENSORS:
            entities.append(
                VServerActionStatusSensor(hass, coordinator.server, action, action_name)
            )
    custom_coordinators = await async_get_or_create_custom_sensor_coordinators(hass, entry)
    entities.extend(
        VServerCustomCommandSensor(coordinator) for coordinator in custom_coordinators
    )
    for container_registry, disk_registry, storage_registry, _name in registries:
        coordinator = container_registry.coordinator
        stats = coordinator.data if isinstance(coordinator.data, dict) else {}
        initial_stats = stats.get("container_stats")
        disk_initial_stats = stats.get("disk_stats")
        storage_initial_stats = stats.get("storage_devices")
        entities.extend(
            container_registry.create_entities_from_registry(registry_entries)
        )
        entities.extend(container_registry.create_entities_from_stats(initial_stats))
        entities.extend(disk_registry.create_entities_from_stats(disk_initial_stats))
        entities.extend(
            storage_registry.create_entities_from_stats(storage_initial_stats)
        )

        def _make_container_listener(
            container_registry: ServerContainerRegistry,
            disk_registry: ServerDiskRegistry,
            storage_registry: ServerStorageRegistry,
        ) -> Callable[[], None]:
            def _handle_update() -> None:
                data: Dict[str, Any] | None = container_registry.coordinator.data
                stats = data.get("container_stats") if isinstance(data, dict) else None
                new_containers = container_registry.create_entities_from_stats(stats)
                if new_containers:
                    async_add_entities(new_containers)
                disk_stats = data.get("disk_stats") if isinstance(data, dict) else None
                new_disks = disk_registry.create_entities_from_stats(disk_stats)
                if new_disks:
                    async_add_entities(new_disks)
                storage_stats = (
                    data.get("storage_devices") if isinstance(data, dict) else None
                )
                new_storage = storage_registry.create_entities_from_stats(storage_stats)
                if new_storage:
                    async_add_entities(new_storage)

            return _handle_update

        remove_listener = coordinator.async_add_listener(
            _make_container_listener(
                container_registry,
                disk_registry,
                storage_registry,
            )
        )
        entry.async_on_unload(remove_listener)
    async_add_entities(entities)
