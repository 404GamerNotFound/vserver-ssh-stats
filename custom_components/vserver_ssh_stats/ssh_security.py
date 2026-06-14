"""SSH host-key verification helpers."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
from typing import Any

SHA256_FINGERPRINT_RE = re.compile(r"^SHA256:([A-Za-z0-9+/]+={0,2})$")
FINGERPRINT_SPLIT_RE = re.compile(r"[\s,;]+")


class SSHHostKeyError(RuntimeError):
    """Raised when SSH host-key verification cannot be completed safely."""


def normalize_host_key_fingerprint(value: object) -> str:
    """Return one canonical OpenSSH SHA-256 host-key fingerprint."""

    if not isinstance(value, str):
        raise ValueError("SSH host-key fingerprint must be a string")

    candidate = value.strip()
    if candidate.lower().startswith("sha256:"):
        candidate = f"SHA256:{candidate.split(':', 1)[1]}"
    match = SHA256_FINGERPRINT_RE.fullmatch(candidate)
    if not match:
        raise ValueError("Expected an OpenSSH SHA256 host-key fingerprint")

    encoded = match.group(1).rstrip("=")
    try:
        digest = base64.b64decode(encoded + ("=" * (-len(encoded) % 4)), validate=True)
    except (ValueError, TypeError) as err:
        raise ValueError("Invalid SHA256 host-key fingerprint") from err
    if len(digest) != hashlib.sha256().digest_size:
        raise ValueError("Invalid SHA256 host-key fingerprint length")

    canonical = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{canonical}"


def parse_host_key_fingerprints(value: object) -> list[str]:
    """Return a de-duplicated list of canonical SSH host-key fingerprints."""

    if isinstance(value, str):
        raw_values: list[object] = [
            part for part in FINGERPRINT_SPLIT_RE.split(value.strip()) if part
        ]
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = []

    fingerprints: list[str] = []
    for raw_value in raw_values:
        fingerprint = normalize_host_key_fingerprint(raw_value)
        if fingerprint not in fingerprints:
            fingerprints.append(fingerprint)
    if not fingerprints:
        raise ValueError("At least one SSH host-key fingerprint is required")
    return fingerprints


def format_host_key_fingerprint(key: Any) -> str:
    """Return the OpenSSH SHA-256 fingerprint for a Paramiko host key."""

    digest = hashlib.sha256(key.asbytes()).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


class PinnedHostKeyPolicy:
    """Paramiko missing-host-key policy that accepts only configured pins."""

    def __init__(self, fingerprints: object) -> None:
        """Initialize the policy with canonical accepted fingerprints."""

        try:
            self._fingerprints = parse_host_key_fingerprints(fingerprints)
        except ValueError as err:
            raise SSHHostKeyError(
                "SSH host-key verification is not configured. Add the server's "
                "SHA256 fingerprint in the integration options."
            ) from err

    def missing_host_key(self, client: Any, hostname: str, key: Any) -> None:
        """Accept *key* only when its SHA-256 fingerprint is pinned."""

        del client
        actual = format_host_key_fingerprint(key)
        if any(hmac.compare_digest(actual, expected) for expected in self._fingerprints):
            return
        expected = ", ".join(self._fingerprints)
        raise SSHHostKeyError(
            f"SSH host key mismatch for {hostname}: received {actual}; expected {expected}"
        )


def configure_pinned_host_keys(client: Any, fingerprints: object) -> None:
    """Configure a Paramiko client to reject every unpinned host key."""

    client.set_missing_host_key_policy(PinnedHostKeyPolicy(fingerprints))
