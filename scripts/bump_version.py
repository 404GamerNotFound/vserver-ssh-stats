#!/usr/bin/env python3
import json
import re
from pathlib import Path

MANIFEST_PATH = Path("custom_components/vserver_ssh_stats/manifest.json")
CONFIG_PATH = Path("addon/vserver_ssh_stats/config.yaml")

def bump_version(version: str) -> str:
    major, minor, patch = map(int, version.split("."))
    patch += 1
    return f"{major}.{minor}.{patch}"

def update_manifest(new_version: str):
    data = json.loads(MANIFEST_PATH.read_text())
    data["version"] = new_version
    MANIFEST_PATH.write_text(json.dumps(data, indent=2) + "\n")

def update_config(new_version: str):
    if not CONFIG_PATH.exists():
        return
    text = CONFIG_PATH.read_text()
    text = re.sub(r'version:\s*"\d+\.\d+\.\d+"', f'version: "{new_version}"', text)
    CONFIG_PATH.write_text(text)

def main():
    data = json.loads(MANIFEST_PATH.read_text())
    current_version = data.get("version", "0.0.0")
    new_version = bump_version(current_version)
    update_manifest(new_version)
    update_config(new_version)
    print(new_version)

if __name__ == "__main__":
    main()
