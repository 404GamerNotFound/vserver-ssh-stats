#!/usr/bin/env python3
import json
import re
from pathlib import Path

MANIFEST_PATH = Path("custom_components/vserver_ssh_stats/manifest.json")
CONFIG_PATH = Path("addon/vserver_ssh_stats/config.yaml")

# Each pattern must contain exactly one capture group around the version
# number so the "current version" statement in every README stays in sync
# with manifest.json on every release.
README_VERSION_PATTERNS = {
    Path("README.md"): [
        r"(Current integration version: \*\*)\d+\.\d+\.\d+(\*\*)",
        r"(- Current manifest version: \*\*)\d+\.\d+\.\d+(\*\*)",
        r"(for example `v)\d+\.\d+\.\d+(`, so HACS can track updates reliably)",
    ],
    Path("README.de.md"): [
        r"(- Aktuelle Manifest-Version: \*\*v)\d+\.\d+\.\d+(\*\* \(siehe `manifest\.json`\))",
    ],
    Path("README.es.md"): [
        r"(- Versión estable actual: \*\*v)\d+\.\d+\.\d+(\*\* \(coincide con `manifest\.json`\))",
    ],
    Path("README.fr.md"): [
        r"(- Version stable actuelle : \*\*v)\d+\.\d+\.\d+(\*\* \(conforme à `manifest\.json`\))",
    ],
}

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

def update_readmes(new_version: str):
    for path, patterns in README_VERSION_PATTERNS.items():
        if not path.exists():
            continue
        text = path.read_text()
        for pattern in patterns:
            text = re.sub(pattern, rf"\g<1>{new_version}\g<2>", text)
        path.write_text(text)

def main():
    data = json.loads(MANIFEST_PATH.read_text())
    current_version = data.get("version", "0.0.0")
    new_version = bump_version(current_version)
    update_manifest(new_version)
    update_config(new_version)
    update_readmes(new_version)
    print(new_version)

if __name__ == "__main__":
    main()
