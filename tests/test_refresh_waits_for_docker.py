"""Regression tests for manual refresh completion."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT_PATH = ROOT / "custom_components" / "vserver_ssh_stats"


def test_refresh_service_waits_for_slow_collectors() -> None:
    """The refresh service waits for the Docker task it schedules."""

    tree = ast.parse((COMPONENT_PATH / "__init__.py").read_text())
    async_setup = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_setup"
    )
    handle_refresh = next(
        node
        for node in async_setup.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle_refresh"
    )

    assert any(
        isinstance(node, ast.Attribute)
        and node.attr == "async_wait_for_slow_refresh"
        for node in ast.walk(handle_refresh)
    )
