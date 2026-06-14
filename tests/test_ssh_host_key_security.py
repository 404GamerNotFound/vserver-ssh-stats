"""Tests for strict SSH host-key verification."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[1]
COMPONENT_PATH = ROOT / "custom_components" / "vserver_ssh_stats"


def _security_module() -> ModuleType:
    """Load the standalone security helper without importing Home Assistant."""

    path = COMPONENT_PATH / "ssh_security.py"
    spec = importlib.util.spec_from_file_location("vserver_ssh_stats_ssh_security", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeHostKey:
    """Minimal Paramiko host-key stand-in."""

    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def asbytes(self) -> bytes:
        """Return encoded host-key bytes."""

        return self._raw


class FakeSSHClient:
    """Minimal Paramiko SSHClient stand-in."""

    policy: object | None = None

    def set_missing_host_key_policy(self, policy: object) -> None:
        """Record the configured verification policy."""

        self.policy = policy


def test_fingerprint_parser_normalizes_and_deduplicates() -> None:
    """Accept OpenSSH SHA256 values with optional padding and separators."""

    security = _security_module()
    fingerprint = security.format_host_key_fingerprint(FakeHostKey(b"server-key"))

    parsed = security.parse_host_key_fingerprints(
        f"{fingerprint}=\n{fingerprint}; {fingerprint}"
    )

    assert parsed == [fingerprint]


def test_pinned_policy_accepts_only_matching_host_key() -> None:
    """Reject a host-key change even when the hostname and credentials match."""

    security = _security_module()
    expected_key = FakeHostKey(b"expected-server-key")
    unexpected_key = FakeHostKey(b"replacement-server-key")
    fingerprint = security.format_host_key_fingerprint(expected_key)
    client = FakeSSHClient()

    security.configure_pinned_host_keys(client, [fingerprint])
    assert client.policy is not None
    client.policy.missing_host_key(client, "example.test", expected_key)

    with pytest.raises(security.SSHHostKeyError, match="host key mismatch"):
        client.policy.missing_host_key(client, "example.test", unexpected_key)


def test_missing_or_legacy_fingerprint_is_rejected() -> None:
    """Never fall back to trusting an unconfigured or MD5 host key."""

    security = _security_module()

    with pytest.raises(security.SSHHostKeyError, match="not configured"):
        security.PinnedHostKeyPolicy([])
    with pytest.raises(ValueError, match="SHA256"):
        security.parse_host_key_fingerprints("MD5:aa:bb:cc")


def test_integration_does_not_use_paramiko_auto_add_policy() -> None:
    """Prevent accidental reintroduction of trust-on-first-use connections."""

    sources = "\n".join(
        path.read_text()
        for path in COMPONENT_PATH.glob("*.py")
    )

    assert "AutoAddPolicy" not in sources
