"""Small, privacy-conscious provenance records for local experiments."""

from __future__ import annotations

import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


def _git_value(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else "unknown"


def collect_provenance(
    *,
    root: Path | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return reproducibility metadata without recording secrets or local paths."""
    from pawbot import __version__

    repository = (root or Path.cwd()).resolve()
    status = _git_value(repository, "status", "--porcelain", "--untracked-files=no")
    payload: dict[str, Any] = {
        "experiment_id": uuid4().hex,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "pawbot_version": __version__,
        "git_revision": _git_value(repository, "rev-parse", "HEAD"),
        "git_dirty": status not in {"", "unknown"},
        "python_version": sys.version.split()[0],
        "platform": platform.system(),
        "platform_release": platform.release(),
        "architecture": platform.machine(),
    }
    if extra:
        payload.update(dict(extra))
    return payload


__all__ = ["collect_provenance"]
