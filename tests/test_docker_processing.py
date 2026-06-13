"""Tests for normalized Docker coordinator data."""
from __future__ import annotations

import ast
import runpy
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "vserver_ssh_stats"


def _docker_processor():
    source = (INTEGRATION / "ssh_collector.py").read_text()
    tree = ast.parse(source)
    wanted = {
        "_sanitize",
        "_safe_int",
        "_safe_float",
        "_safe_bool",
        "_safe_list",
        "_process_docker_data",
    }
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {"Any": Any, "Dict": Dict, "Optional": Optional}
    exec(
        compile(ast.Module(body=functions, type_ignores=[]), "<docker-processor>", "exec"),
        namespace,
    )
    return namespace["_process_docker_data"]


def _docker_metric_validator():
    source = (INTEGRATION / "ssh_collector.py").read_text()
    tree = ast.parse(source)
    wanted = {"_safe_float", "_has_usable_docker_metrics"}
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {"Any": Any, "Dict": Dict, "Optional": Optional}
    exec(
        compile(ast.Module(body=functions, type_ignores=[]), "<docker-validator>", "exec"),
        namespace,
    )
    return namespace["_has_usable_docker_metrics"]


def test_docker_state_and_health_normalization() -> None:
    """Treat clean stops as inactive and failed exits as unhealthy."""

    result = _docker_processor()(
        {
            "docker": 1,
            "container_stats": [
                {
                    "id": "1",
                    "name": "running",
                    "status": "Up 1 minute",
                    "health_state": "healthy",
                },
                {
                    "id": "2",
                    "name": "stopped-ok",
                    "status": "Exited (0) 1 minute ago",
                    "health_state": "exited",
                },
                {
                    "id": "3",
                    "name": "stopped-error",
                    "status": "Exited (137) 1 minute ago",
                    "health_state": "exited",
                },
            ],
        }
    )

    assert [container["running"] for container in result["container_stats"]] == [
        True,
        False,
        False,
    ]
    assert result["docker_unhealthy_containers"] == 1
    assert result["container_lookup"]["stopped_ok"]["id"] == "2"


def test_container_lookup_helpers() -> None:
    """Ensure dynamic entities use stable sanitized lookup keys."""

    helpers = runpy.run_path(str(INTEGRATION / "docker_entities.py"))
    data = {
        "container_stats": [{"id": "2", "name": "stopped-app"}],
        "container_lookup": {"stopped_app": {"id": "2", "name": "stopped-app"}},
    }

    assert helpers["sanitize_container_name"]("Stopped-App") == "stopped_app"
    assert helpers["find_container"](data, "stopped_app")["id"] == "2"


def test_explicit_inspect_state_overrides_stale_status_text() -> None:
    """Prefer Docker's State.Running flag over the formatted ps status."""

    result = _docker_processor()(
        {
            "docker": 1,
            "container_stats": [
                {
                    "id": "1",
                    "name": "grafana",
                    "status": "Up 1 minute",
                    "running": False,
                }
            ],
        }
    )

    assert result["container_lookup"]["grafana"]["running"] is False


def test_all_zero_running_container_sample_is_rejected() -> None:
    """Do not replace valid cached data with a suspicious all-zero sample."""

    has_usable_docker_metrics = _docker_metric_validator()

    assert not has_usable_docker_metrics(
        {
            "container_stats": [
                {"name": "grafana", "running": True, "cpu": 0.0, "mem": 0.0},
                {"name": "homeassistant", "running": True, "cpu": 0.0, "mem": 0.0},
            ]
        }
    )
    assert has_usable_docker_metrics(
        {
            "container_stats": [
                {"name": "grafana", "running": True, "cpu": 0.0, "mem": 4.5}
            ]
        }
    )
