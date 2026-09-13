from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

from typer.testing import CliRunner

from pawbot.cli import update as update_module
from pawbot.cli.commands import app

runner = CliRunner()


def test_update_plan_uses_pip_for_a_managed_virtual_environment(monkeypatch) -> None:
    monkeypatch.setattr(update_module.sys, "executable", "/tmp/pawbot-venv/bin/python")
    monkeypatch.setattr(update_module.sys, "prefix", "/tmp/pawbot-venv")
    monkeypatch.setattr(update_module.sys, "base_prefix", "/usr")
    monkeypatch.setattr(update_module.shutil, "which", lambda _name: None)

    plan = update_module.build_update_plan()

    assert plan.manager == "pip"
    assert plan.argv == (
        "/tmp/pawbot-venv/bin/python",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "pawbot-ai",
    )


def test_update_plan_uses_uv_when_the_environment_has_no_pip(monkeypatch) -> None:
    monkeypatch.setattr(update_module.sys, "executable", "/tmp/pawbot-venv/bin/python")
    monkeypatch.setattr(update_module.sys, "prefix", "/tmp/pawbot-venv")
    monkeypatch.setattr(update_module.sys, "base_prefix", "/usr")
    monkeypatch.setattr(update_module, "_pip_available", lambda: False)
    monkeypatch.setattr(update_module.shutil, "which", lambda name: "/bin/uv" if name == "uv" else None)

    plan = update_module.build_update_plan()

    assert plan.manager == "uv"
    assert plan.argv == (
        "uv",
        "pip",
        "install",
        "--python",
        "/tmp/pawbot-venv/bin/python",
        "--upgrade",
        "pawbot-ai",
    )


def test_update_plan_uses_uv_for_a_uv_tool_environment(monkeypatch) -> None:
    monkeypatch.setattr(
        update_module.sys,
        "executable",
        "/home/user/.cache/uv/archive-v0/abc/bin/python",
    )
    monkeypatch.setattr(update_module.sys, "prefix", "/home/user/.cache/uv/archive-v0/abc")
    monkeypatch.setattr(update_module.sys, "base_prefix", "/usr")
    monkeypatch.setattr(update_module.shutil, "which", lambda name: "/bin/uv" if name == "uv" else None)

    plan = update_module.build_update_plan()

    assert plan.manager == "uv"
    assert plan.argv == (
        "uv",
        "tool",
        "run",
        "--from",
        "pawbot-ai",
        "--refresh",
        "pawbot",
        "--version",
    )


def test_update_plan_uses_uv_tool_upgrade_for_a_persistent_uv_environment(monkeypatch) -> None:
    monkeypatch.setattr(
        update_module.sys,
        "executable",
        "/home/user/.local/share/uv/tools/pawbot-ai/bin/python",
    )
    monkeypatch.setattr(
        update_module.sys,
        "prefix",
        "/home/user/.local/share/uv/tools/pawbot-ai",
    )
    monkeypatch.setattr(update_module.sys, "base_prefix", "/usr")
    monkeypatch.setattr(update_module.shutil, "which", lambda name: "/bin/uv" if name == "uv" else None)

    plan = update_module.build_update_plan()

    assert plan.manager == "uv"
    assert plan.argv == ("uv", "tool", "upgrade", "pawbot-ai")


def test_update_plan_uses_pipx_upgrade_for_a_persistent_pipx_environment(monkeypatch) -> None:
    monkeypatch.setattr(
        update_module.sys,
        "executable",
        "/home/user/.local/pipx/venvs/pawbot-ai/bin/python",
    )
    monkeypatch.setattr(
        update_module.sys,
        "prefix",
        "/home/user/.local/pipx/venvs/pawbot-ai",
    )
    monkeypatch.setattr(update_module.shutil, "which", lambda name: "/bin/pipx" if name == "pipx" else None)

    plan = update_module.build_update_plan()

    assert plan.manager == "pipx"
    assert plan.argv == ("pipx", "upgrade", "pawbot-ai")


def test_update_command_does_not_run_onboarding(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"providers":{"openai":{"apiKey":"keep-me"}},"agents":{"defaults":{}}}',
        encoding="utf-8",
    )
    before = config_path.read_bytes()
    called: list[str] = []

    def fake_update():
        called.append("update")
        return update_module.UpdateResult("pip", "0.3.8", "0.3.9")

    monkeypatch.setattr(update_module, "update_package", fake_update)
    monkeypatch.setattr(
        "pawbot.cli.onboard.run_onboard",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("onboarding must not run")),
    )

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    assert called == ["update"]
    assert config_path.read_bytes() == before
    assert "0.3.8" in result.stdout
    assert "0.3.9" in result.stdout
    assert "not reinitialized" in result.stdout


def test_update_package_propagates_package_manager_failure(monkeypatch) -> None:
    monkeypatch.setattr(update_module, "_source_checkout", lambda: None)
    monkeypatch.setattr(update_module, "_distribution_or_none", lambda: None)
    monkeypatch.setattr(update_module, "_installed_version", lambda: "0.3.8")
    monkeypatch.setattr(
        update_module,
        "build_update_plan",
        lambda: update_module.UpdatePlan("pip", ("python", "-m", "pip")),
    )

    def fail(_argv: list[str]) -> CompletedProcess[str]:
        return CompletedProcess(_argv, 2, stdout="", stderr="network unavailable")

    try:
        update_module.update_package(runner=fail)
    except update_module.UpdateError as exc:
        assert "network unavailable" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("update_package should fail")


def test_update_package_rejects_a_source_checkout(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(update_module, "_source_checkout", lambda: tmp_path)
    monkeypatch.setattr(update_module, "_distribution_or_none", lambda: None)

    try:
        update_module.update_package(runner=lambda _argv: CompletedProcess(_argv, 0))
    except update_module.UpdateError as exc:
        assert "源码/可编辑安装" in str(exc)
        assert "git pull" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("source checkouts must not be overwritten")
