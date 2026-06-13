"""Regression tests for Docker action and collector synchronization."""
from __future__ import annotations

import ast
import asyncio
import logging
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
COORDINATOR_PATH = (
    ROOT / "custom_components" / "vserver_ssh_stats" / "coordinator.py"
)


def _coordinator_methods() -> dict[str, Any]:
    tree = ast.parse(COORDINATOR_PATH.read_text())
    coordinator = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "VServerCoordinator"
    )
    wanted = {
        "_clear_docker_data",
        "_sanitize_container_name",
        "apply_docker_action_state",
        "_async_update_slow_data",
    }
    methods = [
        node
        for node in coordinator.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in wanted
    ]
    namespace: dict[str, Any] = {
        "Any": Any,
        "_LOGGER": logging.getLogger(__name__),
        "async_sample_packages": None,
        "re": re,
    }
    exec(
        compile(ast.Module(body=methods, type_ignores=[]), "<coordinator-test>", "exec"),
        namespace,
    )
    return namespace


def test_stop_action_discards_docker_sample_started_before_action() -> None:
    """A stale running snapshot must not switch a stopped container back on."""

    methods = _coordinator_methods()
    collector_started = asyncio.Event()
    release_collector = asyncio.Event()

    async def sample_docker(*args: Any, **kwargs: Any) -> dict[str, Any]:
        collector_started.set()
        await release_collector.wait()
        running = {"name": "grafana", "running": True, "status": "Up 1 minute"}
        return {
            "docker": 1,
            "container_stats": [running],
            "container_details": [running],
            "container_lookup": {"grafana": running},
        }

    methods["_async_update_slow_data"].__globals__["async_sample_docker"] = sample_docker

    class FakeCoordinator:
        server = {
            "host": "pi5docker",
            "username": "homeassistant",
            "port": 22,
        }
        connect_timeout = 10
        slow_command_timeout = 180
        _docker_state_revision = 0
        _slow_refresh_task = None
        data = {
            "docker": 1,
            "container_stats": [
                {"name": "grafana", "running": True, "status": "Up 1 minute"}
            ],
        }
        _clear_docker_data = methods["_clear_docker_data"]
        _sanitize_container_name = staticmethod(methods["_sanitize_container_name"])

        def async_set_updated_data(self, data: dict[str, Any]) -> None:
            self.data = data

    async def run_scenario() -> None:
        coordinator = FakeCoordinator()
        collection = asyncio.create_task(
            methods["_async_update_slow_data"](coordinator, ["docker"])
        )
        await collector_started.wait()

        methods["apply_docker_action_state"](coordinator, "grafana", "stop")
        assert coordinator.data["container_lookup"]["grafana"]["running"] is False

        release_collector.set()
        await collection
        assert coordinator.data["container_lookup"]["grafana"]["running"] is False

    asyncio.run(run_scenario())
