"""Tests for the release-version parsing used by the update entity."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "vserver_ssh_stats"


def _parse_latest_version():
    """Load the pure tag-parsing helper without importing Home Assistant."""

    tree = ast.parse((INTEGRATION / "update.py").read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_parse_latest_version"
    )
    namespace: dict[str, Any] = {"Any": Any}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "<update-helper>", "exec"),
        namespace,
    )
    return namespace["_parse_latest_version"]


def test_strips_leading_v_from_tag_name() -> None:
    """GitHub tags are typically prefixed with v; the stored version is bare."""

    parse = _parse_latest_version()

    assert parse("v1.4.51") == "1.4.51"
    assert parse("1.4.51") == "1.4.51"


def test_blank_or_missing_tag_returns_none() -> None:
    """A missing release payload should not report a false version."""

    parse = _parse_latest_version()

    assert parse("") is None
    assert parse(None) is None
    assert parse("   ") is None
