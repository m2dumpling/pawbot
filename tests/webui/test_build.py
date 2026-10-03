from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

import pytest

from pawbot.webui.build import (
    WebUIBuildError,
    build_webui_bundle,
    ensure_webui_bundle,
    inspect_webui_bundle,
    pick_webui_build_runner,
    webui_bundle_assets,
)

_MTIME_BASE_NS = 1_700_000_000_000_000_000
_MTIME_STEP_NS = 5_000_000_000


def _touch(path: Path, *, mtime_ns: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(path.name, encoding="utf-8")
    if mtime_ns < 1_000_000_000_000_000:
        mtime_ns = _MTIME_BASE_NS + mtime_ns * _MTIME_STEP_NS
    os.utime(path, ns=(mtime_ns, mtime_ns))


def test_inspect_webui_bundle_ignores_packaged_install_without_source(tmp_path: Path) -> None:
    source = tmp_path / "site-packages" / "webui"
    dist = tmp_path / "site-packages" / "pawbot" / "web" / "dist"
    _touch(dist / "index.html", mtime_ns=20)

    status = inspect_webui_bundle(source_dir=source, dist_dir=dist)

    assert status.source_available is False
    assert status.stale is False
    assert status.reason == "no_source"


def test_inspect_webui_bundle_marks_missing_dist_stale(tmp_path: Path) -> None:
    source = tmp_path / "webui"
    dist = tmp_path / "pawbot" / "web" / "dist"
    _touch(source / "package.json", mtime_ns=10)

    status = inspect_webui_bundle(source_dir=source, dist_dir=dist)

    assert status.source_available is True
    assert status.dist_available is False
    assert status.stale is True
    assert status.reason == "missing_dist"


def test_inspect_webui_bundle_detects_source_newer_than_dist(tmp_path: Path) -> None:
    source = tmp_path / "webui"
    dist = tmp_path / "pawbot" / "web" / "dist"
    _touch(source / "package.json", mtime_ns=10)
    _touch(source / "src" / "App.tsx", mtime_ns=30)
    _touch(dist / "index.html", mtime_ns=20)

    status = inspect_webui_bundle(source_dir=source, dist_dir=dist)

    assert status.stale is True
    assert status.reason == "source_newer"
    assert status.newest_source == source / "src" / "App.tsx"


def test_inspect_webui_bundle_detects_channel_owned_ui_source(tmp_path: Path) -> None:
    source = tmp_path / "webui"
    dist = tmp_path / "pawbot" / "web" / "dist"
    channel_ui = tmp_path / "pawbot" / "channels" / "example" / "webui" / "index.tsx"
    _touch(source / "package.json", mtime_ns=10)
    _touch(dist / "index.html", mtime_ns=20)
    _touch(channel_ui, mtime_ns=30)

    status = inspect_webui_bundle(source_dir=source, dist_dir=dist)

    assert status.needs_build is True
    assert status.reason == "source_newer"
    assert status.newest_source == channel_ui


def test_channel_owned_ui_sources_are_included_in_distributions() -> None:
    project_root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))

    assert "pawbot/channels/*/webui/**/*" in pyproject["tool"]["hatch"]["build"]["include"]


def test_inspect_webui_bundle_accepts_fresh_dist(tmp_path: Path) -> None:
    source = tmp_path / "webui"
    dist = tmp_path / "pawbot" / "web" / "dist"
    _touch(source / "package.json", mtime_ns=10)
    _touch(source / "src" / "App.tsx", mtime_ns=20)
    _touch(dist / "index.html", mtime_ns=30)

    status = inspect_webui_bundle(source_dir=source, dist_dir=dist)

    assert status.stale is False
    assert status.reason == "fresh"


def _bundle_with_lazy_settings(dist: Path) -> None:
    dist.mkdir(parents=True)
    (dist / "index.html").write_text('<script src="/assets/app.js"></script>', encoding="utf-8")
    (dist / "assets").mkdir()
    (dist / "assets/app.js").write_text("app", encoding="utf-8")
    (dist / "asset-manifest.json").write_text(json.dumps({
        "index.html": {"file": "assets/app.js", "dynamicImports": ["settings"]},
        "settings": {"file": "assets/settings.js", "css": ["assets/settings.css"]},
    }), encoding="utf-8")


def test_fresh_index_cannot_hide_missing_lazy_settings_assets(tmp_path: Path) -> None:
    source, dist = tmp_path / "webui", tmp_path / "dist"
    _touch(source / "package.json", mtime_ns=10)
    _bundle_with_lazy_settings(dist)
    os.utime(dist / "index.html", ns=(_MTIME_BASE_NS * 2, _MTIME_BASE_NS * 2))

    status = inspect_webui_bundle(source_dir=source, dist_dir=dist)
    assert status.needs_build
    assert status.reason == "missing_assets"
    with pytest.raises(WebUIBuildError, match="settings.css.*settings.js"):
        webui_bundle_assets(dist)

    (dist / "assets/settings.js").write_text("settings", encoding="utf-8")
    (dist / "assets/settings.css").write_text("css", encoding="utf-8")
    assert inspect_webui_bundle(source_dir=source, dist_dir=dist).reason == "fresh"
    assert "assets/settings.js" in webui_bundle_assets(dist)


def test_packaged_bundle_is_checked_without_a_source_tree(tmp_path: Path) -> None:
    _bundle_with_lazy_settings(tmp_path / "dist")
    status = inspect_webui_bundle(source_dir=tmp_path / "absent", dist_dir=tmp_path / "dist")
    assert not status.source_available
    assert not status.dist_available
    assert status.reason == "missing_assets"


@pytest.mark.parametrize("reference", ["../outside.js", "%2e%2e/outside.js"])
def test_asset_manifest_cannot_reference_files_outside_bundle(tmp_path: Path, reference: str) -> None:
    _bundle_with_lazy_settings(tmp_path / "dist")
    (tmp_path / "dist/asset-manifest.json").write_text(
        json.dumps({"settings": {"file": reference}}), encoding="utf-8",
    )
    with pytest.raises(WebUIBuildError, match="escapes the bundle"):
        webui_bundle_assets(tmp_path / "dist")


def test_build_success_without_lazy_assets_is_rejected(tmp_path: Path) -> None:
    _bundle_with_lazy_settings(tmp_path / "dist")
    with pytest.raises(WebUIBuildError, match="assets are missing"):
        build_webui_bundle(
            source_dir=tmp_path, dist_dir=tmp_path / "dist", runner="bun",
            subprocess_run=lambda *_args, **_kwargs: None,
        )


def test_ensure_webui_bundle_auto_builds_stale_dist(tmp_path: Path) -> None:
    source = tmp_path / "webui"
    dist = tmp_path / "pawbot" / "web" / "dist"
    _touch(source / "package.json", mtime_ns=10)
    _touch(source / "src" / "App.tsx", mtime_ns=30)
    _touch(dist / "index.html", mtime_ns=20)
    commands: list[tuple[str, ...]] = []

    def fake_run(command, *, cwd: Path, check: bool) -> None:
        commands.append(tuple(command))
        assert cwd == source
        assert check is True
        if command == ["bun", "run", "build"]:
            _touch(dist / "index.html", mtime_ns=40)

    status = ensure_webui_bundle(
        mode="auto",
        source_dir=source,
        dist_dir=dist,
        runner="bun",
        subprocess_run=fake_run,
    )

    assert status.stale is False
    assert commands == [("bun", "install"), ("bun", "run", "build")]


def test_pick_webui_build_runner_returns_resolved_executable(monkeypatch) -> None:
    bun_shim = r"C:\tools\npm\bun.CMD"

    monkeypatch.setattr(
        "pawbot.webui.build.shutil.which",
        lambda candidate: bun_shim if candidate == "bun" else None,
    )

    assert pick_webui_build_runner() == bun_shim


def test_ensure_webui_bundle_warns_without_building(tmp_path: Path) -> None:
    source = tmp_path / "webui"
    dist = tmp_path / "pawbot" / "web" / "dist"
    _touch(source / "package.json", mtime_ns=10)
    _touch(source / "src" / "App.tsx", mtime_ns=30)
    _touch(dist / "index.html", mtime_ns=20)
    messages: list[str] = []

    status = ensure_webui_bundle(
        mode="warn",
        source_dir=source,
        dist_dir=dist,
        output=messages.append,
    )

    assert status.stale is True
    assert messages
    assert "Run `cd" in messages[0]
