#!/usr/bin/env python3
"""Keep the integration version in sync across manifest, add-on and READMEs.

Usage:
    python scripts/bump_version.py               # next patch version
    python scripts/bump_version.py --set 1.5.13  # set an explicit version

The automatic bump never goes below the newest stable release tag
(``vX.Y.Z``). Without that rule the per-commit bump on ``main`` and the
manually created release tags drift apart (e.g. tag ``v1.5.12`` while
``manifest.json`` still says ``1.4.81``), and Home Assistant then reports an
update that can never be installed (issue #210).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

MANIFEST_PATH = Path("custom_components/vserver_ssh_stats/manifest.json")
CONFIG_PATH = Path("addon/vserver_ssh_stats/config.yaml")

STABLE_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
# Release tags may carry a pre-release suffix such as ``v1.5.11-beta3``.
RELEASE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-.]?[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$")

# Each pattern must contain exactly two capture groups around the version
# number so the "current version" statement in every README stays in sync
# with manifest.json on every bump.
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


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse ``X.Y.Z`` (optionally prefixed with ``v``) into a tuple."""
    match = STABLE_TAG_RE.match(version.strip())
    if not match:
        raise ValueError(f"Not a stable X.Y.Z version: {version!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def latest_stable_tag(tags: list[str]) -> tuple[int, int, int] | None:
    """Return the highest stable ``vX.Y.Z`` tag, ignoring pre-releases."""
    versions = [parse_version(tag) for tag in tags if STABLE_TAG_RE.match(tag.strip())]
    return max(versions) if versions else None


def next_version(current: str, tags: list[str]) -> str:
    """Return the next patch version, never lower than the newest release tag."""
    base = parse_version(current)
    latest_tag = latest_stable_tag(tags)
    if latest_tag is not None and latest_tag > base:
        base = latest_tag
    major, minor, patch = base
    return f"{major}.{minor}.{patch + 1}"


def bump_version(version: str) -> str:
    """Backwards-compatible helper: plain patch increment."""
    major, minor, patch = parse_version(version)
    return f"{major}.{minor}.{patch + 1}"


def git_tags() -> list[str]:
    """Return all git tags of the repository (empty list if git is unavailable)."""
    try:
        result = subprocess.run(
            ["git", "tag", "--list"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def update_manifest(new_version: str) -> None:
    data = json.loads(MANIFEST_PATH.read_text())
    data["version"] = new_version
    MANIFEST_PATH.write_text(json.dumps(data, indent=2) + "\n")


def update_config(new_version: str) -> None:
    if not CONFIG_PATH.exists():
        return
    text = CONFIG_PATH.read_text()
    text = re.sub(r'version:\s*"\d+\.\d+\.\d+"', f'version: "{new_version}"', text)
    CONFIG_PATH.write_text(text)


def update_readmes(new_version: str) -> None:
    for path, patterns in README_VERSION_PATTERNS.items():
        if not path.exists():
            continue
        text = path.read_text()
        for pattern in patterns:
            text = re.sub(pattern, rf"\g<1>{new_version}\g<2>", text)
        path.write_text(text)


def apply_version(new_version: str) -> None:
    update_manifest(new_version)
    update_config(new_version)
    # README statements only track stable versions.
    if STABLE_TAG_RE.match(new_version):
        update_readmes(new_version)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--set",
        dest="explicit",
        metavar="VERSION",
        help="set this exact version (a leading 'v' is stripped)",
    )
    args = parser.parse_args(argv)

    if args.explicit:
        new_version = args.explicit.strip().removeprefix("v")
        if not RELEASE_VERSION_RE.match(new_version):
            raise SystemExit(f"Invalid version: {args.explicit!r}")
    else:
        data = json.loads(MANIFEST_PATH.read_text())
        new_version = next_version(data.get("version", "0.0.0"), git_tags())

    apply_version(new_version)
    print(new_version)


if __name__ == "__main__":
    main()
