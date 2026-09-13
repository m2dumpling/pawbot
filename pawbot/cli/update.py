"""Self-update the installed pawbot package without touching user data."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Callable, cast

from packaging.version import InvalidVersion, Version

PACKAGE_NAME = "pawbot-ai"


class UpdateError(RuntimeError):
    """Raised when the current installation cannot be updated safely."""


@dataclass(frozen=True)
class UpdatePlan:
    """Package-manager command selected for the current installation."""

    manager: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class UpdateResult:
    """Outcome of one package update attempt."""

    manager: str
    before_version: str
    after_version: str | None

    @property
    def changed(self) -> bool:
        """Return whether the installed distribution version changed."""
        if self.after_version is None:
            return True
        try:
            return Version(self.after_version) != Version(self.before_version)
        except InvalidVersion:
            return self.after_version != self.before_version


UpdateRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _path_parts(value: str) -> set[str]:
    """Return case-insensitive path components for either host path syntax."""
    return {
        part.casefold()
        for part in value.replace("\\", "/").split("/")
        if part
    }


def _runtime_path_text() -> tuple[str, ...]:
    return (sys.executable, sys.prefix)


def _path_is_under(path: str, root: str) -> bool:
    normalized_path = path.replace("\\", "/").rstrip("/").casefold()
    normalized_root = root.replace("\\", "/").rstrip("/").casefold()
    return bool(normalized_root) and (
        normalized_path == normalized_root
        or normalized_path.startswith(f"{normalized_root}/")
    )


def _runtime_is_under_configured_root(*names: str) -> bool:
    roots = tuple(
        value.strip()
        for name in names
        if (value := os.environ.get(name, "").strip())
    )
    return any(
        _path_is_under(raw, root)
        for raw in _runtime_path_text()
        for root in roots
    )


def _is_uv_runtime() -> bool:
    """Detect uv tool environments, including ``uv tool run`` cache envs."""
    if _runtime_is_under_configured_root("UV_TOOL_DIR", "UV_CACHE_DIR"):
        return True
    for raw in _runtime_path_text():
        parts = _path_parts(raw)
        if "uv" not in parts:
            continue
        if "tools" in parts or "cache" in parts or any(
            part.startswith("archive-v") for part in parts
        ):
            return True
    return False


def _is_persistent_uv_runtime() -> bool:
    """Return whether uv placed the running tool in its persistent tools dir."""
    if _runtime_is_under_configured_root("UV_TOOL_DIR"):
        return True
    return any("tools" in _path_parts(raw) for raw in _runtime_path_text())


def _is_pipx_runtime() -> bool:
    """Detect a pipx-managed environment without relying on pipx internals."""
    if _runtime_is_under_configured_root("PIPX_HOME", "PIPX_VENV_CACHEDIR"):
        return True
    for raw in _runtime_path_text():
        parts = _path_parts(raw)
        if "pipx" in parts and ("venvs" in parts or "cache" in parts):
            return True
    return False


def _is_persistent_pipx_runtime() -> bool:
    """Return whether pipx owns a persistent virtual environment for the tool."""
    pipx_home = os.environ.get("PIPX_HOME", "").strip()
    if pipx_home:
        normalized_home = pipx_home.rstrip("/\\")
        venv_root = f"{normalized_home}/venvs"
        if any(_path_is_under(raw, venv_root) for raw in _runtime_path_text()):
            return True
    return any("venvs" in _path_parts(raw) for raw in _runtime_path_text())


def _pip_available() -> bool:
    try:
        return find_spec("pip") is not None
    except (ImportError, ValueError):
        return False


def _source_checkout() -> Path | None:
    """Return the repository root when this command is running from a checkout."""
    package_root = Path(__file__).resolve().parents[1]
    project_root = package_root.parent
    if (project_root / "pyproject.toml").is_file() and (project_root / ".git").exists():
        return project_root
    return None


def _distribution_or_none() -> Distribution | None:
    try:
        return distribution(PACKAGE_NAME)
    except PackageNotFoundError:
        return None


def _is_editable_distribution(dist: Distribution | None) -> bool:
    if dist is None:
        return False
    try:
        raw = dist.read_text("direct_url.json")
    except (OSError, ValueError):
        return False
    if not raw:
        return False
    try:
        payload_raw: object = json.loads(raw)
    except (TypeError, ValueError):
        return False
    if not isinstance(payload_raw, dict):
        return False
    payload = cast(dict[str, Any], payload_raw)
    dir_info_raw: object = payload.get("dir_info")
    if not isinstance(dir_info_raw, dict):
        return False
    dir_info = cast(dict[str, object], dir_info_raw)
    return bool(dir_info.get("editable"))


def _installed_version() -> str:
    dist = _distribution_or_none()
    if dist is not None:
        return dist.version
    try:
        from pawbot import __version__

        return __version__
    except Exception:
        return "unknown"


def build_update_plan() -> UpdatePlan:
    """Choose the package manager that owns the running pawbot installation."""
    if _is_uv_runtime():
        if shutil.which("uv") is None:
            raise UpdateError(
                "检测到这是 uv 管理的 Pawbot 环境，但当前 PATH 中找不到 uv。"
                "请先安装 uv，或使用原安装方式更新。"
            )
        if _is_persistent_uv_runtime():
            return UpdatePlan("uv", ("uv", "tool", "upgrade", PACKAGE_NAME))
        # The official POSIX launcher can use `uv tool run --from`, which
        # stores a disposable environment in uv's cache. Refresh that cache
        # without replacing the user's launcher with a second installation.
        return UpdatePlan(
            "uv",
            ("uv", "tool", "run", "--from", PACKAGE_NAME, "--refresh", "pawbot", "--version"),
        )

    if _is_pipx_runtime():
        if shutil.which("pipx") is None:
            raise UpdateError(
                "检测到这是 pipx 管理的 Pawbot 环境，但当前 PATH 中找不到 pipx。"
                "请先安装 pipx，或使用原安装方式更新。"
            )
        if _is_persistent_pipx_runtime():
            return UpdatePlan("pipx", ("pipx", "upgrade", PACKAGE_NAME))
        return UpdatePlan("pipx", ("pipx", "install", "--force", PACKAGE_NAME))

    # uv-created environments intentionally do not always contain pip. Use uv
    # itself in that case instead of producing a confusing "No module named
    # pip" failure from an otherwise healthy installation.
    if not _pip_available() and shutil.which("uv") is not None:
        return UpdatePlan(
            "uv",
            ("uv", "pip", "install", "--python", sys.executable, "--upgrade", PACKAGE_NAME),
        )

    return UpdatePlan(
        "pip",
        (sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE_NAME),
    )


def _run_update(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the package manager with its output attached to the user's terminal."""
    return subprocess.run(argv, check=False, text=True)


def update_package(*, runner: UpdateRunner | None = None) -> UpdateResult:
    """Update pawbot while leaving config, credentials, sessions, and workspaces alone."""
    dist = _distribution_or_none()
    source = _source_checkout()
    if source is not None or _is_editable_distribution(dist):
        location = f" ({source})" if source is not None else ""
        raise UpdateError(
            "当前 Pawbot 是源码/可编辑安装{}，无法安全地用 `pawbot update` 覆盖它。"
            "请在源码目录执行 `git pull` 后重新安装，或使用发布版安装方式。".format(location)
        )

    before_version = _installed_version()
    plan = build_update_plan()
    process = (runner or _run_update)(list(plan.argv))
    if process.returncode != 0:
        detail = (process.stderr or process.stdout or "").strip()
        suffix = f"\n{detail[-1200:]}" if detail else ""
        raise UpdateError(
            f"使用 {plan.manager} 更新 Pawbot 失败（退出码 {process.returncode}）。{suffix}"
        )

    importlib.invalidate_caches()
    # `uv tool run` and `pipx run` execute from disposable environments. The
    # nested package-manager command refreshes the environment used by the
    # next invocation, but this already-running process still sees its old
    # metadata, so do not report a false "already up to date" result.
    updates_current_runtime = not (
        (plan.manager == "uv" and not _is_persistent_uv_runtime())
        or (plan.manager == "pipx" and not _is_persistent_pipx_runtime())
    )
    after_version = _installed_version() if updates_current_runtime else None
    return UpdateResult(
        manager=plan.manager,
        before_version=before_version,
        after_version=after_version,
    )
