"""Regression tests for OS-specific remote action commands."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "vserver_ssh_stats"
INIT_PATH = INTEGRATION / "__init__.py"
BUTTON_PATH = INTEGRATION / "button.py"


def _has_assignment_to(node: ast.AST, names: set[str]) -> bool:
    """Return whether an AST assignment writes one of *names*."""

    if not isinstance(node, ast.Assign):
        return False
    return any(isinstance(target, ast.Name) and target.id in names for target in node.targets)


def _action_command_namespace() -> dict[str, Any]:
    """Compile action command helpers without importing Home Assistant."""

    tree = ast.parse(INIT_PATH.read_text())
    assignments = {"SUPPORTED_TARGET_OS", "LINUX_TARGET_OS"}
    functions = {
        "_build_os_command_sequence",
        "_build_update_commands",
        "_build_package_list_update_commands",
        "_build_clear_package_cache_commands",
    }
    body = [
        node
        for node in tree.body
        if _has_assignment_to(node, assignments)
        or (isinstance(node, ast.FunctionDef) and node.name in functions)
    ]
    namespace: dict[str, Any] = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(INIT_PATH), "exec"), namespace)
    return namespace


def _button_target_namespace() -> dict[str, Any]:
    """Compile button target OS helpers without importing Home Assistant."""

    tree = ast.parse(BUTTON_PATH.read_text())
    assignments = {"SUPPORTED_ACTION_TARGET_OS", "LINUX_OS_HINTS"}
    functions = {"_normalize_action_target_os", "_target_os_for_action"}
    body = [
        node
        for node in tree.body
        if _has_assignment_to(node, assignments)
        or (isinstance(node, ast.FunctionDef) and node.name in functions)
    ]
    namespace: dict[str, Any] = {
        "Any": Any,
        "Dict": Dict,
        "VServerCoordinator": object,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(BUTTON_PATH), "exec"), namespace)
    return namespace


def test_explicit_linux_package_actions_do_not_try_powershell() -> None:
    """Linux profiles must keep the original Linux error instead of masking it."""

    namespace = _action_command_namespace()

    for builder_name in (
        "_build_update_commands",
        "_build_package_list_update_commands",
        "_build_clear_package_cache_commands",
    ):
        for target_os in ("debian", "raspbian"):
            commands = namespace[builder_name](target_os)

            assert len(commands) == 1
            assert "powershell" not in commands[0].lower()


def test_explicit_windows_package_actions_do_not_try_linux_commands() -> None:
    """Windows profiles should not fall through to Linux shell commands."""

    namespace = _action_command_namespace()

    for builder_name in (
        "_build_update_commands",
        "_build_package_list_update_commands",
        "_build_clear_package_cache_commands",
    ):
        commands = namespace[builder_name]("windows")

        assert len(commands) == 1
        assert "apt-get" not in commands[0]
        assert "dnf" not in commands[0]
        assert "yum" not in commands[0]


def test_auto_package_actions_keep_ordered_os_fallback() -> None:
    """Auto mode still tries Linux first and then Windows for unknown hosts."""

    namespace = _action_command_namespace()
    commands = namespace["_build_update_commands"]("auto")

    assert len(commands) == 2
    assert "apt-get" in commands[0]
    assert "powershell" in commands[1].lower()


def test_action_buttons_infer_detected_linux_os_for_auto_targets() -> None:
    """Buttons convert detected Linux hosts from auto to the Linux action profile."""

    namespace = _button_target_namespace()
    target_os_for_action = namespace["_target_os_for_action"]

    target_os = target_os_for_action(
        {"target_os": "auto"},
        SimpleNamespace(data={"os": "Debian GNU/Linux 13 (trixie)"}),
    )

    assert target_os == "debian"


def test_action_buttons_keep_explicit_target_os_over_detected_os() -> None:
    """Manual OS configuration has priority over collector hints."""

    namespace = _button_target_namespace()
    target_os_for_action = namespace["_target_os_for_action"]

    target_os = target_os_for_action(
        {"target_os": "windows"},
        SimpleNamespace(data={"os": "Debian GNU/Linux 13 (trixie)"}),
    )

    assert target_os == "windows"


def test_action_buttons_infer_detected_windows_os_for_auto_targets() -> None:
    """Auto action buttons also preserve the Windows path after detection."""

    namespace = _button_target_namespace()
    target_os_for_action = namespace["_target_os_for_action"]

    target_os = target_os_for_action(
        {"target_os": "auto"},
        SimpleNamespace(data={"os": "Microsoft Windows Server 2025"}),
    )

    assert target_os == "windows"
