"""Reject version mismatches and Python publication before native TUI assets exist."""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path
from typing import Any

TARGETS = ("linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64", "win32-x64")


def validate_release(tag: str, version: str, release: dict[str, Any] | None = None) -> None:
    if tag != f"v{version}":
        raise ValueError(f"release tag {tag!r} does not match package version {version!r}")
    if release is None:
        return
    if release.get("tagName") != tag or release.get("isDraft") is not False:
        raise ValueError("the matching GitHub release must be published")
    assets = {asset["name"]: asset for asset in release.get("assets", [])}
    required = set()
    for target in TARGETS:
        suffix = ".exe" if target.startswith("win32-") else ""
        archive = f"pawbot-tui-{target}{suffix}.zip"
        required.update((archive, f"{archive}.sha256"))
    missing = sorted(name for name in required
                     if name not in assets or assets[name].get("size", 0) <= 0)
    if missing:
        raise ValueError(f"native release assets are missing or empty: {', '.join(missing)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("release_json", type=Path, nargs="?")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    release = json.loads(args.release_json.read_text(encoding="utf-8")) if args.release_json else None
    try:
        validate_release(args.tag, version, release)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Release preflight passed: {args.tag}")


if __name__ == "__main__":
    main()
