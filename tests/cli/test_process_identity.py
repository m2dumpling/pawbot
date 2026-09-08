from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pawbot.cli.commands import app
from pawbot.cli.process_identity import named_executable, set_cli_process_identity


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["agent"], "pawbot-agent"),
        (["gateway", "--background"], "pawbot-gateway"),
        (["webui"], "pawbot-webui"),
        (["status"], "pawbot"),
        ([], "pawbot"),
    ],
)
def test_cli_process_identity_uses_product_and_role(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    expected: str,
) -> None:
    titles: list[str] = []
    monkeypatch.setattr("pawbot.cli.process_identity.os.name", "posix")
    monkeypatch.setattr("pawbot.cli.process_identity._set_process_title", titles.append)

    set_cli_process_identity(args)

    assert titles == [expected]


def test_cli_process_identity_keeps_windows_launcher_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    titles: list[str] = []
    monkeypatch.setattr("pawbot.cli.process_identity.os.name", "nt")
    monkeypatch.setattr("pawbot.cli.process_identity._set_process_title", titles.append)

    set_cli_process_identity(["agent"])

    assert titles == []


def test_legacy_console_entrypoint_still_sets_subcommand_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr("pawbot.cli.commands.set_cli_process_identity", commands.append)

    result = CliRunner().invoke(app, ["webui", "--help"])

    assert result.exit_code == 0
    assert commands == [["webui"]]


def test_legacy_console_entrypoint_routes_bare_command_to_webui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identities: list[list[str]] = []
    launches: list[tuple[list[str], str]] = []
    monkeypatch.setattr("pawbot.cli.commands.set_cli_process_identity", identities.append)
    monkeypatch.setattr(
        "pawbot.cli.entry._run_webui",
        lambda args, *, prog_name: launches.append((args, prog_name)),
    )

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 0
    assert identities == [["webui"]]
    assert launches == [([], "pawbot")]


def test_named_executable_creates_stable_role_symlink(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX symlink naming is not used on Windows")
    executable = tmp_path / "bun"
    executable.write_text("runtime", encoding="utf-8")

    first = Path(
        named_executable(executable.as_posix(), name="pawbot-tui", directory=tmp_path / "run")
    )
    second = Path(
        named_executable(executable.as_posix(), name="pawbot-tui", directory=tmp_path / "run")
    )

    assert first == second
    assert first.name == "pawbot-tui"
    assert first.is_symlink()
    assert first.resolve() == executable


def test_named_executable_uses_original_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("pawbot.cli.process_identity.os.name", "nt")

    assert (
        named_executable("bun.exe", name="pawbot-tui", directory=tmp_path / "run")
        == "bun.exe"
    )
