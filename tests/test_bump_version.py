"""Tests for scripts/bump_version.py (regression for issue #210)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bump_version", ROOT / "scripts" / "bump_version.py"
)
bump_version = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bump_version)


def test_next_version_follows_newest_release_tag() -> None:
    tags = ["v1.4.9-beta4", "v1.5.11", "v1.5.11-beta3", "v1.5.12"]
    assert bump_version.next_version("1.4.81", tags) == "1.5.13"


def test_next_version_keeps_manifest_when_ahead_of_tags() -> None:
    assert bump_version.next_version("1.5.20", ["v1.5.12"]) == "1.5.21"


def test_next_version_ignores_prerelease_and_foreign_tags() -> None:
    tags = ["v2.0.0-beta1", "logo", "release-9.9.9"]
    assert bump_version.next_version("1.5.13", tags) == "1.5.14"


def test_next_version_without_tags() -> None:
    assert bump_version.next_version("1.4.81", []) == "1.4.82"


def test_set_explicit_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "custom_components" / "vserver_ssh_stats" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"domain": "vserver_ssh_stats", "version": "1.4.81"}))
    readme = tmp_path / "README.md"
    readme.write_text("Current integration version: **1.4.81**.\n")
    monkeypatch.chdir(tmp_path)

    bump_version.main(["--set", "v1.5.12"])

    assert json.loads(manifest.read_text())["version"] == "1.5.12"
    assert "**1.5.12**" in readme.read_text()


def test_set_rejects_garbage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        bump_version.main(["--set", "latest"])
