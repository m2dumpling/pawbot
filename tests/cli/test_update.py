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


def test_update_command_reports_windows_handoff(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        update_module,
        "update_package",
        lambda: update_module.UpdateResult(
            "uv",
            "0.4.1",
            None,
            deferred=True,
            log_path=str(tmp_path / "update.log"),
        ),
    )

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    assert "后台更新" in result.stdout
    assert "pawbot --version" in result.stdout
    assert "update.log" in result.stdout


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


def test_update_package_hands_off_persistent_windows_uv_update(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(update_module, "_source_checkout", lambda: None)
    monkeypatch.setattr(update_module, "_distribution_or_none", lambda: None)
    monkeypatch.setattr(update_module, "_installed_version", lambda: "0.4.1")
    monkeypatch.setattr(update_module.sys, "platform", "win32")
    monkeypatch.setattr(update_module, "_is_persistent_uv_runtime", lambda: True)
    plan = update_module.UpdatePlan("uv", ("uv", "tool", "upgrade", "pawbot-ai"))
    monkeypatch.setattr(update_module, "build_update_plan", lambda: plan)
    monkeypatch.setattr(update_module.shutil, "which", lambda name: {
        "powershell.exe": "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "uv": "C:/Users/test/.local/bin/uv.exe",
    }.get(name))
    captured: dict[str, object] = {}

    class FakeProcess:
        pass

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(update_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(update_module.tempfile, "gettempdir", lambda: str(tmp_path))

    result = update_module.update_package()

    assert result.deferred is True
    assert result.before_version == "0.4.1"
    assert result.log_path is not None
    assert captured["argv"][0].endswith("powershell.exe")
    assert "WaitForExit" in captured["argv"][-1]
    assert "uv.exe" in captured["argv"][-1]
    assert "tool" in captured["argv"][-1]
    assert captured["kwargs"]["creationflags"] & update_module.subprocess.CREATE_NO_WINDOW


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
