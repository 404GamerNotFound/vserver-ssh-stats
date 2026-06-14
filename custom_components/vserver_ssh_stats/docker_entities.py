"""Shared helpers for dynamic Docker container entities."""
from __future__ import annotations

import re
from typing import Any, Iterable


def sanitize_container_name(name: str) -> str:
    """Return a stable entity-safe representation of a container name."""

    return re.sub(r"[^a-zA-Z0-9_]+", "_", name).lower()


def container_names_from_stats(
    stats: Iterable[dict[str, Any]] | None,
) -> dict[str, str]:
    """Return new container names keyed by their sanitized identifier."""

    names: dict[str, str] = {}
    for container in stats or []:
        if not isinstance(container, dict):
            continue
        raw_name = str(container.get("name") or "").strip()
        sanitized = sanitize_container_name(raw_name)
        if raw_name and sanitized:
            names.setdefault(sanitized, raw_name)
    return names


def container_names_from_registry(
    entries: Iterable[Any],
    host: str,
    server_name: str,
    unique_id_suffix: str,
    display_suffix: str,
) -> dict[str, str]:
    """Recover dynamic container names from Home Assistant's entity registry."""

    prefix = f"{host}_container_"
    names: dict[str, str] = {}
    for entry in entries:
        unique_id = str(getattr(entry, "unique_id", "") or "")
        if not unique_id.startswith(prefix) or not unique_id.endswith(unique_id_suffix):
            continue
        sanitized = unique_id[len(prefix) : -len(unique_id_suffix)]
        if not sanitized:
            continue
        display_name = str(
            getattr(entry, "original_name", None)
            or getattr(entry, "name", None)
            or sanitized
        )
        server_prefix = f"{server_name} "
        if display_name.startswith(server_prefix):
            display_name = display_name[len(server_prefix) :]
        if display_name.endswith(display_suffix):
            display_name = display_name[: -len(display_suffix)]
        names.setdefault(sanitized, display_name or sanitized)
    return names


def build_container_action_data(
    server: dict[str, Any],
    connect_timeout: int,
    container: str,
) -> dict[str, Any]:
    """Build service data for a Docker container action."""

    data: dict[str, Any] = {
        "host": server["host"],
        "username": server["username"],
        "port": server.get("port", 22),
        "connect_timeout": connect_timeout,
        "container": container,
    }
    if server.get("password"):
        data["password"] = server["password"]
    if server.get("key"):
        data["key"] = server["key"]
    if server.get("host_key_fingerprints"):
        data["host_key_fingerprints"] = "\n".join(server["host_key_fingerprints"])
    return data


def find_container(
    data: dict[str, Any] | None,
    sanitized_name: str,
) -> dict[str, Any] | None:
    """Return one container from coordinator data."""

    if not isinstance(data, dict):
        return None
    lookup = data.get("container_lookup")
    if isinstance(lookup, dict):
        container = lookup.get(sanitized_name)
        return container if isinstance(container, dict) else None
    for container in data.get("container_stats", []):
        if not isinstance(container, dict):
            continue
        if sanitize_container_name(str(container.get("name") or "")) == sanitized_name:
            return container
    return None
