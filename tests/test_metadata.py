"""Repository metadata tests."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "vserver_ssh_stats"


def test_manifest_has_required_custom_integration_metadata() -> None:
    """Validate the fields HACS and Home Assistant rely on."""

    manifest = json.loads((INTEGRATION / "manifest.json").read_text())

    assert manifest["domain"] == INTEGRATION.name
    assert manifest["name"] == "VServer SSH Stats"
    assert manifest["config_flow"] is True
    assert manifest["codeowners"]
    assert manifest["documentation"].startswith("https://")
    assert manifest["issue_tracker"].startswith("https://")
    assert manifest["iot_class"] == "local_polling"
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])


def test_json_files_are_valid() -> None:
    """Ensure metadata and translation JSON files remain parseable."""

    paths = [ROOT / "hacs.json", *INTEGRATION.rglob("*.json")]
    for path in paths:
        assert json.loads(path.read_text()), f"Expected non-empty JSON object in {path}"


def test_services_yaml_is_a_mapping() -> None:
    """Ensure Home Assistant service descriptions remain valid YAML."""

    services = yaml.safe_load((INTEGRATION / "services.yaml").read_text())

    assert isinstance(services, dict)
    assert {
        "refresh",
        "start_docker_container",
        "stop_docker_container",
        "restart_docker_container",
    }.issubset(services)
