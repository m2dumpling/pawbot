from __future__ import annotations

from pathlib import Path

from pawbot.apps.cli import service


def test_windows_catalog_scan_verifies_only_plausible_commands(
    tmp_path: Path, monkeypatch,
) -> None:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    (binary_dir / "foo.CMD").write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", str(binary_dir))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.CMD")
    checked: list[str] = []

    def fake_which(name: str) -> str | None:
        checked.append(name)
        return str(binary_dir / "foo.CMD") if name == "foo" else None

    monkeypatch.setattr(service.shutil, "which", fake_which)
    result = service._available_entry_points(
        ["foo", "missing", str(binary_dir / "direct.exe")], windows=True,
    )

    assert result == {"foo": True, "missing": False, str(binary_dir / "direct.exe"): False}
    assert "missing" not in checked
    assert set(checked) == {"foo", str(binary_dir / "direct.exe")}


def test_manifest_package_name_does_not_probe_package_manager(
    tmp_path: Path, monkeypatch,
) -> None:
    manager = service.CliAppManager(workspace=tmp_path, data_dir=tmp_path / "data")

    def unexpected_probe(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("package manager was probed while rendering the catalog")

    monkeypatch.setattr(manager, "_pip_uninstall_argv", unexpected_probe)
    app = {
        "name": "example",
        "entry_point": "cli-anything-example",
        "package_manager": "pip",
        "pip_package": "cli-anything-example",
    }
    assert manager._package_ref(app) == {
        "manager": "pip",
        "name": "cli-anything-example",
    }
