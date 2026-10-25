"""Regression coverage for intentionally read-only LibreELEC roots (issue #197)."""
from __future__ import annotations

import ast
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

INTEGRATION = Path(__file__).parents[1] / "custom_components" / "vserver_ssh_stats"


def _repair_methods(registry):
    tree = ast.parse((INTEGRATION / 'coordinator.py').read_text())
    coordinator = next(node for node in tree.body if getattr(node, 'name', '') == 'VServerCoordinator')
    methods = [node for node in coordinator.body
               if getattr(node, 'name', '') in {'_sync_issue', '_sync_base_issues'}]
    namespace = {
        'ir': registry, 'DOMAIN': 'vserver_ssh_stats', 'FAIL2BAN_ELEVATED_THRESHOLD': 20,
    }
    module = ast.Module(body=[
        ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
        *methods,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), '<repairs-test>', 'exec'), namespace)
    return type('Coordinator', (), {method.name: namespace[method.name] for method in methods})


@pytest.mark.parametrize('expected', [False, None, True])
def test_root_repair_is_cleared_only_for_expected_readonly_root(expected):
    registry = SimpleNamespace(
        IssueSeverity=SimpleNamespace(ERROR='error', WARNING='warning'),
        async_create_issue=Mock(), async_delete_issue=Mock(),
    )
    coordinator = _repair_methods(registry)()
    coordinator.server = {'host': 'cinema'}
    coordinator.hass = object()
    data = {'root_fs_readonly': True, 'software_raid_degraded': True, 'fail2ban_banned_count': 20}
    if expected is not None:  # Old payloads without the new field retain their warning.
        data['root_fs_readonly_expected'] = expected
    coordinator._sync_base_issues(data)
    created = {call.args[2] for call in registry.async_create_issue.call_args_list}
    deleted = {call.args[2] for call in registry.async_delete_issue.call_args_list}
    assert ('cinema_root_fs_readonly' in created) is (not expected)
    assert ('cinema_root_fs_readonly' in deleted) is bool(expected)
    assert {'cinema_raid_degraded', 'cinema_fail2ban_elevated'} <= created

    # Poll another server immediately afterwards: the exception must not leak.
    coordinator.server = {'host': 'debian'}
    coordinator._sync_base_issues({'root_fs_readonly': True})
    assert registry.async_create_issue.call_args.args[2] == 'debian_root_fs_readonly'

    # Both servers can subsequently report writable roots and clear the repair.
    for host in ('cinema', 'debian'):
        coordinator.server = {'host': host}
        registry.async_create_issue.reset_mock()
        coordinator._sync_base_issues({'root_fs_readonly': False})
        registry.async_create_issue.assert_not_called()
        registry.async_delete_issue.assert_any_call(
            coordinator.hass, 'vserver_ssh_stats', f'{host}_root_fs_readonly',
        )


@pytest.mark.parametrize('expected', [1, 0, '0', None])
def test_sampling_preserves_readonly_state_and_normalizes_exception(expected):
    """The new field must reach Repairs as a boolean, including old payloads."""
    tree = ast.parse((INTEGRATION / 'ssh_collector.py').read_text())
    names = {
        'async_sample', '_safe_int', '_safe_float', '_safe_list',
        '_drop_slow_result_keys', '_temperature_status',
    }
    nodes = [node for node in tree.body if getattr(node, 'name', '') in names]
    slow_keys = next(node for node in tree.body if isinstance(node, ast.Assign)
                     and any(getattr(target, 'id', '') == 'SLOW_RESULT_KEYS' for target in node.targets))
    data = {'root_fs_readonly': 1}
    if expected is not None:
        data['root_fs_readonly_expected'] = expected
    namespace = {
        'asyncio': asyncio, 'time': time, '_LOGGER': Mock(),
        'DEFAULT_CONNECT_TIMEOUT': 10, 'DEFAULT_COMMAND_TIMEOUT': 45,
        '_async_collect_raw': AsyncMock(return_value=(data, {}, None)),
        '_async_check_monitored_ports': AsyncMock(return_value=[]),
        '_process_docker_data': lambda data: {},
        'normalize_mac_addresses': lambda value: [],
        '_add_port_check_results': Mock(),
        **{name: SimpleNamespace(compute=Mock(return_value=None)) for name in (
            'cpu_rolling_average_cache', 'mem_rolling_average_cache', 'energy_cache',
        )},
    }
    module = ast.Module(body=[
        ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
        slow_keys, *nodes,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), '<sampling-test>', 'exec'), namespace)
    result = asyncio.run(namespace['async_sample']('cinema', 'root', None, None, 22))
    assert result['root_fs_readonly'] is True
    assert result['root_fs_readonly_expected'] is (expected == 1)
