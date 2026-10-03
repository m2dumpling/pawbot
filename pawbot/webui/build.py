"""Helpers for keeping the bundled WebUI build in sync with source checkouts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import unquote, urlsplit

BuildMode = Literal["auto", "prompt", "warn", "skip"]

_SOURCE_TOP_LEVEL_FILES = (
    "index.html",
    "package.json",
    "bun.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "vite.config.ts",
    "vite.config.js",
    "tailwind.config.ts",
    "tailwind.config.js",
    "postcss.config.ts",
    "postcss.config.js",
    "tsconfig.json",
    "tsconfig.build.json",
    "components.json",
)
_SOURCE_DIRS = ("src", "public")


class WebUIBuildError(RuntimeError):
    """Raised when the local WebUI bundle cannot be built."""


class _EntryAssets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paths: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.paths.add(str(values["src"]))
        elif tag == "link" and values.get("rel") in {
            "stylesheet", "modulepreload", "preload", "icon",
        } and values.get("href"):
            self.paths.add(str(values["href"]))


def webui_bundle_assets(dist_dir: Path) -> list[str]:
    """Validate entry and lazy-chunk references; return local HTTP asset paths.

    Fresh timestamps cannot prove that Git or a wheel contains every chunk.
    Vite's manifest covers lazy settings/channel views that index.html cannot.
    """
    index = dist_dir / "index.html"
    if not index.is_file():
        raise WebUIBuildError("bundled WebUI index.html is missing")
    parser = _EntryAssets()
    parser.feed(index.read_text(encoding="utf-8"))
    references = parser.paths
    manifest_path = dist_dir / "asset-manifest.json"
    if manifest_path.is_file():
        try:
            raw_manifest: object = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(raw_manifest, dict):
                raise ValueError("manifest must be an object")
            manifest = cast(dict[str, Any], raw_manifest)
            for entry in manifest.values():
                paths: list[Any] = [entry["file"], *entry.get("css", []), *entry.get("assets", [])]
                for path in paths:
                    if not isinstance(path, str):
                        raise ValueError("asset path must be a string")
                    references.add(path)
                for key in (*entry.get("imports", []), *entry.get("dynamicImports", [])):
                    if key not in manifest:
                        raise ValueError(f"unknown manifest import: {key}")
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise WebUIBuildError(f"invalid WebUI asset manifest: {exc}") from exc
        references.add("asset-manifest.json")
    elif any(urlsplit(path).path.endswith(".js") for path in references):
        raise WebUIBuildError("bundled WebUI asset-manifest.json is missing")

    assets: set[str] = set()
    root = dist_dir.resolve()
    missing: list[str] = []
    for reference in references:
        url = urlsplit(reference)
        if url.scheme or url.netloc:
            continue
        relative = unquote(url.path).lstrip("/")
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise WebUIBuildError(f"WebUI asset escapes the bundle: {reference}")
        if not target.is_file():
            missing.append(relative)
        assets.add(relative)
    if missing:
        raise WebUIBuildError(f"bundled WebUI assets are missing: {', '.join(sorted(missing))}")
    return sorted(assets)


@dataclass(frozen=True)
class WebUIBundleStatus:
    """Freshness status for a source checkout's bundled WebUI assets."""

    source_dir: Path
    dist_dir: Path
    index_html: Path
    source_available: bool
    dist_available: bool
    stale: bool
    reason: str
    newest_source: Path | None = None
    newest_source_mtime_ns: int | None = None
    dist_mtime_ns: int | None = None

    @property
    def needs_build(self) -> bool:
        return self.source_available and self.stale


def default_project_root() -> Path:
    """Return the repository root when running from a source checkout."""
    return Path(__file__).resolve().parents[2]


def default_webui_source_dir(project_root: Path | None = None) -> Path:
    """Return the conventional frontend source directory for a checkout."""
    root = project_root or default_project_root()
    return root / "webui"


def default_webui_dist_dir(project_root: Path | None = None) -> Path:
    """Return the bundled WebUI dist directory for the installed package."""
    try:
        import pawbot.web as web_pkg  # type: ignore[import-not-found]
    except ImportError:
        root = project_root or default_project_root()
        return root / "pawbot" / "web" / "dist"
    return Path(web_pkg.__file__).resolve().parent / "dist"


def iter_webui_source_files(source_dir: Path) -> list[Path]:
    """Return WebUI source files that should make the production bundle stale."""
    files: list[Path] = []
    for name in _SOURCE_TOP_LEVEL_FILES:
        candidate = source_dir / name
        if candidate.is_file():
            files.append(candidate)
    for dirname in _SOURCE_DIRS:
        root = source_dir / dirname
        if not root.is_dir():
            continue
        files.extend(path for path in root.rglob("*") if path.is_file())
    channel_root = source_dir.parent / "pawbot" / "channels"
    if channel_root.is_dir():
        for channel_webui in channel_root.glob("*/webui"):
            files.extend(path for path in channel_webui.rglob("*") if path.is_file())
    return files


def inspect_webui_bundle(
    *,
    source_dir: Path | None = None,
    dist_dir: Path | None = None,
) -> WebUIBundleStatus:
    """Inspect whether a checkout's WebUI source is newer than the bundled dist."""
    resolved_source = source_dir or default_webui_source_dir()
    resolved_dist = dist_dir or default_webui_dist_dir()
    index_html = resolved_dist / "index.html"

    source_available = (resolved_source / "package.json").is_file()
    if index_html.is_file():
        try:
            webui_bundle_assets(resolved_dist)
        except WebUIBuildError:
            return WebUIBundleStatus(
                source_dir=resolved_source,
                dist_dir=resolved_dist,
                index_html=index_html,
                source_available=source_available,
                dist_available=False,
                stale=True,
                reason="missing_assets",
            )

    if not source_available:
        return WebUIBundleStatus(
            source_dir=resolved_source,
            dist_dir=resolved_dist,
            index_html=index_html,
            source_available=False,
            dist_available=index_html.is_file(),
            stale=False,
            reason="no_source",
        )

    if not index_html.is_file():
        return WebUIBundleStatus(
            source_dir=resolved_source,
            dist_dir=resolved_dist,
            index_html=index_html,
            source_available=True,
            dist_available=False,
            stale=True,
            reason="missing_dist",
        )

    dist_mtime_ns = index_html.stat().st_mtime_ns
    newest_source: Path | None = None
    newest_source_mtime_ns: int | None = None
    for candidate in iter_webui_source_files(resolved_source):
        try:
            mtime_ns = candidate.stat().st_mtime_ns
        except OSError:
            continue
        if newest_source_mtime_ns is None or mtime_ns > newest_source_mtime_ns:
            newest_source = candidate
            newest_source_mtime_ns = mtime_ns

    if newest_source_mtime_ns is not None and newest_source_mtime_ns > dist_mtime_ns:
        return WebUIBundleStatus(
            source_dir=resolved_source,
            dist_dir=resolved_dist,
            index_html=index_html,
            source_available=True,
            dist_available=True,
            stale=True,
            reason="source_newer",
            newest_source=newest_source,
            newest_source_mtime_ns=newest_source_mtime_ns,
            dist_mtime_ns=dist_mtime_ns,
        )

    return WebUIBundleStatus(
        source_dir=resolved_source,
        dist_dir=resolved_dist,
        index_html=index_html,
        source_available=True,
        dist_available=True,
        stale=False,
        reason="fresh",
        newest_source=newest_source,
        newest_source_mtime_ns=newest_source_mtime_ns,
        dist_mtime_ns=dist_mtime_ns,
    )


def describe_webui_bundle_status(status: WebUIBundleStatus) -> str:
    """Return a short user-facing freshness message."""
    if status.reason == "missing_dist":
        return "Bundled WebUI build is missing."
    if status.reason == "missing_assets":
        return "Bundled WebUI has missing or invalid assets. Rebuild or reinstall pawbot."
    if status.reason == "source_newer":
        changed = _display_source_path(status)
        return f"WebUI source is newer than the bundled build ({changed})."
    if status.reason == "fresh":
        return "Bundled WebUI build is up to date."
    return "WebUI source tree was not found; using the bundled build."


def build_webui_bundle(
    *,
    source_dir: Path | None = None,
    dist_dir: Path | None = None,
    runner: str | None = None,
    subprocess_run: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    output: Callable[[str], None] | None = None,
) -> WebUIBundleStatus:
    """Install frontend dependencies and build the WebUI bundle."""
    resolved_source = source_dir or default_webui_source_dir()
    command_runner = runner or pick_webui_build_runner()
    if command_runner is None:
        raise WebUIBuildError(
            "neither `bun` nor `npm` is available on PATH; install one or run "
            "`cd webui && bun run build` manually"
        )

    _emit(output, f"Building bundled WebUI with `{command_runner}`...")
    install = [command_runner, "install"]
    if Path(command_runner).stem.lower() == "bun" and (resolved_source / "bun.lock").is_file():
        install.append("--frozen-lockfile")
    elif Path(command_runner).stem.lower() == "npm" and (resolved_source / "package-lock.json").is_file():
        install = [command_runner, "ci"]
    _run_frontend_command(
        install,
        cwd=resolved_source,
        subprocess_run=subprocess_run,
    )
    _run_frontend_command(
        [command_runner, "run", "build"],
        cwd=resolved_source,
        subprocess_run=subprocess_run,
    )
    webui_bundle_assets(dist_dir or default_webui_dist_dir())
    return inspect_webui_bundle(source_dir=resolved_source, dist_dir=dist_dir)


def ensure_webui_bundle(
    *,
    mode: BuildMode,
    source_dir: Path | None = None,
    dist_dir: Path | None = None,
    confirm: Callable[[str], bool] | None = None,
    output: Callable[[str], None] | None = None,
    runner: str | None = None,
    environ: Mapping[str, str] | None = None,
    subprocess_run: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> WebUIBundleStatus:
    """Ensure or warn about a stale WebUI bundle according to the selected mode."""
    env = environ or os.environ
    status = inspect_webui_bundle(source_dir=source_dir, dist_dir=dist_dir)
    if not status.needs_build:
        return status

    detail = describe_webui_bundle_status(status)
    if env.get("PAWBOT_SKIP_WEBUI_BUILD") == "1" or mode == "skip":
        _emit(output, f"Warning: {detail} Skipping WebUI build.")
        return status

    if mode == "warn":
        _emit(
            output,
            f"Warning: {detail} Run `cd {status.source_dir} && bun run build` "
            "to refresh it.",
        )
        return status

    if mode == "prompt":
        if confirm is None:
            _emit(output, f"Warning: {detail} No interactive confirmation is available.")
            return status
        message = "Build WebUI now? This runs `cd webui && bun run build`."
        if not confirm(message):
            _emit(output, "Continuing with the existing bundled WebUI build.")
            return status

    try:
        return build_webui_bundle(
            source_dir=status.source_dir,
            dist_dir=status.dist_dir,
            runner=runner,
            subprocess_run=subprocess_run,
            output=output,
        )
    except WebUIBuildError as exc:
        raise WebUIBuildError(f"{detail} {exc}") from exc


def pick_webui_build_runner() -> str | None:
    """Pick the frontend package manager used to build the WebUI."""
    for candidate in ("bun", "npm"):
        if executable := shutil.which(candidate):
            return executable
    return None


def _run_frontend_command(
    command: list[str],
    *,
    cwd: Path,
    subprocess_run: Callable[..., subprocess.CompletedProcess[Any]],
) -> None:
    try:
        subprocess_run(command, cwd=cwd, check=True)
    except subprocess.CalledProcessError as exc:
        raise WebUIBuildError(
            f"command failed ({exc.returncode}): {' '.join(command)}"
        ) from exc
    except OSError as exc:
        raise WebUIBuildError(f"command failed: {' '.join(command)} ({exc})") from exc


def _display_source_path(status: WebUIBundleStatus) -> str:
    if status.newest_source is None:
        return "source files changed"
    with suppress(ValueError):
        return str(status.newest_source.relative_to(status.source_dir))
    return str(status.newest_source)


def _emit(output: Callable[[str], None] | None, message: str) -> None:
    if output is not None:
        output(message)
