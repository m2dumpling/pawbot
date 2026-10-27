"""Validate release files and bind prepared Python distributions to a workflow run."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from scripts.check_release import TARGETS, validate_release


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_tui(directory: Path, target: str) -> None:
    if target not in TARGETS:
        raise ValueError(f"unsupported TUI target: {target}")
    extension = ".exe" if target.startswith("win32-") else ""
    executable = f"pawbot-tui-{target}{extension}"
    name = f"{executable}.zip"
    archive_path = directory / name
    checksum = (directory / f"{name}.sha256").read_text(encoding="utf-8").strip()
    match = re.fullmatch(r"([0-9a-f]{64})  (.+)", checksum)
    if match is None or match.group(2) != name or match.group(1) != digest(archive_path.read_bytes()):
        raise ValueError(f"invalid archive checksum: {name}")
    required = {
        executable, "THIRD_PARTY_NOTICES.txt", "RELINKING.md", "LICENSE",
        "BUN-1.3.13-LICENSE.md", "LGPL-2.0.txt", "LGPL-2.1.txt",
        "pawbot-tui-source.tar.gz",
    }
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.namelist()
        if len(entries) != len(set(entries)) or set(entries) != required | {"MANIFEST.sha256"}:
            raise ValueError(f"unexpected or missing archive entries: {name}")
        if archive.testzip() is not None:
            raise ValueError(f"invalid archive CRC: {name}")
        lines = archive.read("MANIFEST.sha256").decode("utf-8").splitlines()
        manifest: dict[str, str] = {}
        for line in lines:
            item = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
            if item is None or item.group(2) in manifest:
                raise ValueError(f"invalid internal manifest: {name}")
            manifest[item.group(2)] = item.group(1)
        if set(manifest) != required:
            raise ValueError(f"incomplete internal manifest: {name}")
        for entry, expected in manifest.items():
            data = archive.read(entry)
            if not data or digest(data) != expected:
                raise ValueError(f"invalid internal checksum: {name}/{entry}")


def _validate_bundle(files: dict[str, bytes], prefix: str) -> None:
    root = f"{prefix}pawbot/web/dist/"
    if root + "index.html" not in files or root + "asset-manifest.json" not in files:
        raise ValueError("WebUI index or manifest missing from distribution")
    manifest = json.loads(files[root + "asset-manifest.json"])
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("invalid WebUI manifest")
    for entry in manifest.values():
        for relative in [entry["file"], *entry.get("css", []), *entry.get("assets", [])]:
            if not isinstance(relative, str) or root + relative.lstrip("/") not in files:
                raise ValueError(f"WebUI asset missing from distribution: {relative}")
        for key in [*entry.get("imports", []), *entry.get("dynamicImports", [])]:
            if key not in manifest:
                raise ValueError(f"WebUI manifest import missing: {key}")


def validate_distribution(path: Path, version: str) -> None:
    if path.name.endswith(".whl"):
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError("wheel has a corrupt entry")
            files = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
        metadata = [data for name, data in files.items() if name.endswith(".dist-info/METADATA")]
        prefix = ""
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            files = {
                item.name: stream.read()
                for item in archive.getmembers()
                if item.isfile() and (stream := archive.extractfile(item)) is not None
            }
        roots = {name.split("/", 1)[0] for name in files if "/" in name}
        if len(roots) != 1:
            raise ValueError("sdist must contain exactly one root directory")
        prefix = next(iter(roots)) + "/"
        metadata = [files[prefix + "PKG-INFO"]] if prefix + "PKG-INFO" in files else []
    else:
        raise ValueError(f"unsupported distribution: {path.name}")
    if len(metadata) != 1 or BytesParser().parsebytes(metadata[0]).get("Version") != version:
        raise ValueError(f"distribution version mismatch: {path.name}")
    _validate_bundle(files, prefix)


def distribution_names(version: str) -> set[str]:
    return {f"pawbot_ai-{version}-py3-none-any.whl", f"pawbot_ai-{version}.tar.gz"}


def prepare_manifest(directory: Path, tag: str, version: str, commit: str, run_id: str, repo: str) -> None:
    validate_release(tag, version)
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise ValueError("invalid commit or workflow run ID")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid repository")
    names = distribution_names(version)
    # uv creates an empty .gitignore in a newly created output directory; the
    # Actions artifact uploader excludes hidden files by default.
    actual = {p.name for p in directory.iterdir() if p.is_file()} - {".gitignore"}
    if actual != names:
        raise ValueError("expected exactly one matching wheel and sdist")
    for name in sorted(names):
        validate_distribution(directory / name, version)
    manifest = {
        "tag": tag, "version": version, "commit": commit, "run_id": run_id,
        "repository": repo, "files": {name: digest((directory / name).read_bytes()) for name in sorted(names)},
    }
    (directory / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def validate_prepared(directory: Path, tag: str, version: str, commit: str, run_id: str, repo: str) -> None:
    validate_release(tag, version)
    manifest = json.loads((directory / "release-manifest.json").read_text(encoding="utf-8"))
    expected_identity = {"tag": tag, "version": version, "commit": commit, "run_id": run_id, "repository": repo}
    if any(manifest.get(key) != value for key, value in expected_identity.items()):
        raise ValueError("prepared artifact identity does not match release")
    names = distribution_names(version)
    if set(manifest.get("files", {})) != names:
        raise ValueError("prepared artifact manifest has missing or unexpected files")
    if {p.name for p in directory.iterdir() if p.is_file()} != names | {"release-manifest.json"}:
        raise ValueError("prepared artifact directory has missing or unexpected files")
    for name in sorted(names):
        path = directory / name
        if digest(path.read_bytes()) != manifest["files"][name]:
            raise ValueError(f"prepared artifact checksum mismatch: {name}")
        validate_distribution(path, version)


def validate_prepare_run(run: dict[str, Any], run_id: str, commit: str, repo: str) -> None:
    if (
        str(run.get("id")) != run_id or run.get("head_sha") != commit
        or run.get("repository", {}).get("full_name") != repo
        or run.get("path") != ".github/workflows/publish.yml"
        or run.get("event") != "workflow_dispatch" or run.get("status") != "completed"
        or run.get("conclusion") != "success"
    ):
        raise ValueError("prepare run does not match the successful release workflow and commit")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    tui = sub.add_parser("tui")
    tui.add_argument("directory", type=Path)
    tui.add_argument("target", choices=TARGETS)
    dist = sub.add_parser("distribution")
    dist.add_argument("path", type=Path)
    dist.add_argument("version")
    for command in ("prepare", "verify"):
        part = sub.add_parser(command)
        part.add_argument("directory", type=Path)
        part.add_argument("tag")
        part.add_argument("version")
        part.add_argument("commit")
        part.add_argument("run_id")
        part.add_argument("repository")
    run = sub.add_parser("run")
    run.add_argument("json", type=Path)
    run.add_argument("run_id")
    run.add_argument("commit")
    run.add_argument("repository")
    args = parser.parse_args()
    if args.command == "tui":
        validate_tui(args.directory, args.target)
    elif args.command == "distribution":
        validate_distribution(args.path, args.version)
    elif args.command == "run":
        validate_prepare_run(json.loads(args.json.read_text(encoding="utf-8")), args.run_id, args.commit, args.repository)
    else:
        fn = prepare_manifest if args.command == "prepare" else validate_prepared
        fn(args.directory, args.tag, args.version, args.commit, args.run_id, args.repository)
    print(f"Release artifact validation passed: {args.command}")


if __name__ == "__main__":
    main()
