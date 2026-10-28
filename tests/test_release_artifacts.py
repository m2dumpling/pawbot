from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.release_artifacts import (
    digest,
    prepare_manifest,
    validate_distribution,
    validate_prepare_run,
    validate_prepared,
    validate_tui,
)

VERSION = "0.8.2"
TAG = f"v{VERSION}"
COMMIT = "a" * 40
RUN = "12345"
REPO = "example/pawbot"


def _distributions(directory: Path) -> None:
    bundle = {
        "pawbot/web/dist/index.html": b"<html></html>",
        "pawbot/web/dist/asset-manifest.json": json.dumps({"src/main.tsx": {"file": "assets/main.js"}}).encode(),
        "pawbot/web/dist/assets/main.js": b"console.log('ok')",
    }
    metadata = f"Metadata-Version: 2.1\nName: pawbot-ai\nVersion: {VERSION}\n\nPackage description.\n".encode()
    with zipfile.ZipFile(directory / f"pawbot_ai-{VERSION}-py3-none-any.whl", "w") as wheel:
        for name, value in bundle.items():
            wheel.writestr(name, value)
        wheel.writestr(f"pawbot_ai-{VERSION}.dist-info/METADATA", metadata)
    with tarfile.open(directory / f"pawbot_ai-{VERSION}.tar.gz", "w:gz") as sdist:
        for name, value in {**bundle, "PKG-INFO": metadata}.items():
            info = tarfile.TarInfo(f"pawbot_ai-{VERSION}/{name}")
            info.size = len(value)
            sdist.addfile(info, io.BytesIO(value))


def _tui(directory: Path) -> Path:
    executable = "pawbot-tui-linux-x64"
    files = {name: b"test" for name in (
        executable, "THIRD_PARTY_NOTICES.txt", "RELINKING.md", "LICENSE",
        "BUN-1.3.13-LICENSE.md", "LGPL-2.0.txt", "LGPL-2.1.txt", "pawbot-tui-source.tar.gz",
    )}
    manifest = "".join(f"{digest(value)}  {name}\n" for name, value in files.items()).encode()
    archive_path = directory / f"{executable}.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
        archive.writestr("MANIFEST.sha256", manifest)
    archive_path.with_name(archive_path.name + ".sha256").write_text(
        f"{digest(archive_path.read_bytes())}  {archive_path.name}\n", encoding="utf-8"
    )
    return archive_path


def test_tui_checks_outer_and_inner_hashes(tmp_path: Path) -> None:
    archive = _tui(tmp_path)
    validate_tui(tmp_path, "linux-x64")
    checksum = archive.with_name(archive.name + ".sha256")
    checksum.write_text(f"{'0' * 64}  {archive.name}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="archive checksum"):
        validate_tui(tmp_path, "linux-x64")
    with zipfile.ZipFile(archive, "a") as content:
        content.writestr("unexpected", "content")
    checksum.write_text(f"{digest(archive.read_bytes())}  {archive.name}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="archive entries"):
        validate_tui(tmp_path, "linux-x64")


def test_tui_rejects_tampered_content_even_with_a_fresh_outer_checksum(tmp_path: Path) -> None:
    archive_path = _tui(tmp_path)
    with zipfile.ZipFile(archive_path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries["pawbot-tui-linux-x64"] = b"tampered executable"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    archive_path.with_name(archive_path.name + ".sha256").write_text(
        f"{digest(archive_path.read_bytes())}  {archive_path.name}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="internal checksum"):
        validate_tui(tmp_path, "linux-x64")


def test_prepared_distributions_bind_version_commit_run_and_hash(tmp_path: Path) -> None:
    _distributions(tmp_path)
    (tmp_path / ".gitignore").write_text("*\n", encoding="utf-8")
    prepare_manifest(tmp_path, TAG, VERSION, COMMIT, RUN, REPO)
    (tmp_path / ".gitignore").unlink()  # Hidden files are excluded from the uploaded artifact.
    validate_prepared(tmp_path, TAG, VERSION, COMMIT, RUN, REPO)
    for commit, run in (("b" * 40, RUN), (COMMIT, "12346")):
        with pytest.raises(ValueError, match="identity"):
            validate_prepared(tmp_path, TAG, VERSION, commit, run, REPO)
    with pytest.raises(ValueError, match="does not match"):
        validate_prepared(tmp_path, "v0.8.1", VERSION, COMMIT, RUN, REPO)
    wheel = tmp_path / f"pawbot_ai-{VERSION}-py3-none-any.whl"
    wheel.write_bytes(wheel.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="checksum"):
        validate_prepared(tmp_path, TAG, VERSION, COMMIT, RUN, REPO)


def test_prepare_rejects_missing_distribution(tmp_path: Path) -> None:
    _distributions(tmp_path)
    (tmp_path / f"pawbot_ai-{VERSION}.tar.gz").unlink()
    with pytest.raises(ValueError, match="exactly one"):
        prepare_manifest(tmp_path, TAG, VERSION, COMMIT, RUN, REPO)


def test_distribution_rejects_missing_asset_and_wrong_version(tmp_path: Path) -> None:
    _distributions(tmp_path)
    wheel = tmp_path / f"pawbot_ai-{VERSION}-py3-none-any.whl"
    with pytest.raises(ValueError, match="version mismatch"):
        validate_distribution(wheel, "0.8.1")
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"pawbot_ai-{VERSION}.dist-info/METADATA", f"Version: {VERSION}\n\nDescription.\n")
    with pytest.raises(ValueError, match="WebUI index or manifest"):
        validate_distribution(wheel, VERSION)

    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"pawbot_ai-{VERSION}.dist-info/METADATA", f"Version: {VERSION}\n\n")
    with pytest.raises(ValueError, match="description is missing"):
        validate_distribution(wheel, VERSION)


def test_prepare_run_must_be_successful_dispatch_for_same_workflow_and_commit() -> None:
    run = {
        "id": int(RUN), "head_sha": COMMIT, "repository": {"full_name": REPO},
        "path": ".github/workflows/publish.yml", "event": "workflow_dispatch",
        "status": "completed", "conclusion": "success",
    }
    validate_prepare_run(run, RUN, COMMIT, REPO)
    for key, value in (("head_sha", "b" * 40), ("conclusion", "failure"),
                       ("event", "pull_request"), ("path", "other.yml"), ("id", 9)):
        with pytest.raises(ValueError, match="prepare run"):
            validate_prepare_run({**run, key: value}, RUN, COMMIT, REPO)


def test_pypi_readme_uses_version_independent_installation_links() -> None:
    import re

    readme = Path(__file__).resolve().parents[1] / "PYPI_README.md"
    text = readme.read_text(encoding="utf-8")
    assert "https://github.com/m2dumpling/pawbot/releases/latest" in text
    assert "uv tool install --force --upgrade pawbot-ai" in text
    assert not re.search(r"/releases/tag/v\d|raw\.githubusercontent\.com/[^\s]+/v\d", text)
