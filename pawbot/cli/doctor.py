"""Read-only local diagnostics for a Pawbot installation and Gateway."""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pawbot import __version__
from pawbot.config.loader import get_config_path, load_config
from pawbot.gateway import GatewayInstance, GatewayRuntime
from pawbot.gateway.protocol import GatewayEventJournal, GatewayOperationLedger


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    id: str
    status: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "status": self.status, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class DoctorReport:
    status: str
    checks: tuple[DoctorCheck, ...]
    version: str = __version__

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "version": self.version,
            "checks": [check.to_dict() for check in self.checks],
        }


def _check(id: str, status: str, detail: str) -> DoctorCheck:
    return DoctorCheck(id=id, status=status, detail=detail)


def collect_doctor_report(
    *,
    config_path: Path | None = None,
    workspace: Path | None = None,
    check_provider: bool = False,
) -> DoctorReport:
    """Collect diagnostics without changing configuration or contacting providers."""

    checks: list[DoctorCheck] = [
        _check("python", "passed" if sys.version_info >= (3, 11) else "failed", platform.python_version()),
    ]
    path = (config_path or get_config_path()).expanduser().resolve(strict=False)
    loaded = None
    if not path.exists():
        checks.append(_check("config", "warning", f"not found: {path}"))
    else:
        try:
            loaded = load_config(path)
        except Exception as exc:
            checks.append(_check("config", "failed", f"{type(exc).__name__}: {exc}"))
        else:
            checks.append(_check("config", "passed", str(path)))

    data_dir = path.parent
    checks.append(
        _check(
            "data_dir",
            "passed" if data_dir.exists() and os.access(data_dir, os.R_OK) else "failed",
            str(data_dir),
        )
    )

    selected_workspace = workspace or (loaded.workspace_path if loaded is not None else None)
    if selected_workspace is None:
        checks.append(_check("workspace", "warning", "workspace could not be resolved"))
    else:
        selected_workspace = selected_workspace.expanduser().resolve(strict=False)
        checks.append(
            _check(
                "workspace",
                "passed" if selected_workspace.exists() and selected_workspace.is_dir() else "failed",
                str(selected_workspace),
            )
        )

    instance = GatewayInstance.resolve(
        config_path=path,
        workspace=str(selected_workspace) if selected_workspace is not None else None,
    )
    runtime = GatewayRuntime(paths=instance.paths)
    try:
        gateway_status = runtime.status()
    except Exception as exc:
        checks.append(_check("gateway", "failed", f"{type(exc).__name__}: {exc}"))
    else:
        if not gateway_status.running:
            checks.append(_check("gateway", "warning", "not running"))
        elif gateway_status.ready is False:
            checks.append(_check("gateway", "failed", gateway_status.reason or "not ready"))
        else:
            checks.append(_check("gateway", "passed", "running and ready"))

    run_dir = data_dir / "run"
    event_journal = GatewayEventJournal(run_dir / "gateway.events.jsonl")
    operation_ledger = GatewayOperationLedger(run_dir / "gateway.operations.json")
    event_issues = event_journal.validate()
    operation_issues = operation_ledger.validate()
    checks.append(
        _check(
            "event_sequence",
            "failed" if event_issues else "passed",
            "; ".join(event_issues[:3]) if event_issues else "event journal is consistent",
        )
    )
    checks.append(
        _check(
            "operation_ledger",
            "failed" if operation_issues else "passed",
            "; ".join(operation_issues[:3]) if operation_issues else "idempotency ledger is consistent",
        )
    )

    web_dist = Path(__file__).resolve().parents[1] / "web" / "dist" / "index.html"
    checks.append(
        _check(
            "webui_bundle",
            "passed" if web_dist.is_file() else "warning",
            str(web_dist),
        )
    )

    preset = None
    if loaded is not None:
        preset = loaded.resolve_preset()
        checks.append(
            _check(
                "model_config",
                "passed" if preset.model.strip() else "warning",
                f"provider={preset.provider or 'default'} model={preset.model or 'unset'}",
            )
        )
    if check_provider:
        if loaded is None or preset is None:
            checks.append(_check("provider_readiness", "not_evaluable", "configuration is unavailable"))
        else:
            provider_name = loaded.get_provider_name(preset=preset) or "unknown"
            api_key = loaded.get_api_key(preset=preset)
            checks.append(
                _check(
                    "provider_readiness",
                    "passed" if api_key else "warning",
                    f"provider={provider_name}; credential presence checked locally only",
                )
            )

    status = "failed" if any(check.status == "failed" for check in checks) else "passed"
    return DoctorReport(status=status, checks=tuple(checks))


def render_doctor_report(report: DoctorReport, *, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    lines = [f"Pawbot doctor: {report.status} (v{report.version})"]
    for check in report.checks:
        marker = {
            "passed": "✓",
            "warning": "⚠",
            "failed": "✗",
            "not_evaluable": "·",
        }.get(check.status, "?")
        lines.append(f"{marker} {check.id}: {check.detail}")
    return "\n".join(lines)


__all__ = [
    "DoctorCheck",
    "DoctorReport",
    "collect_doctor_report",
    "render_doctor_report",
]
