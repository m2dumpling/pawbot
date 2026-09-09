from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_posix_installer_stops_with_actionable_venv_error(tmp_path: Path) -> None:
    """A missing Debian-style ensurepip module must stop before package setup."""

    if os.name == "nt":
        pytest.skip("the POSIX installer requires a POSIX shell")

    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX shell is not available")

    fake_python = tmp_path / "python"
    fake_python.write_text(
        """#!/bin/sh
payload=$(cat)
case "$payload" in
  *"print(f"*) printf '3.13\\n'; exit 0 ;;
  *"sys.prefix"*) exit 1 ;;
  *"import venv"*) exit 1 ;;
  *) exit 0 ;;
esac
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "PYTHON": str(fake_python),
            "PAWBOT_VENV": str(tmp_path / "venv"),
            "PAWBOT_SKIP_WIZARD": "1",
        }
    )
    result = subprocess.run(
        [shell, str(ROOT / "scripts" / "install.sh")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 1
    assert "Pawbot installation failed." in output
    assert "python3.13-venv" in output
    assert "After fixing the reason above, rerun the Pawbot installer." in output
    assert "Then open the WebUI with:" not in output
    assert "Run: pawbot" not in output
    assert "Installation successful." not in output


def test_install_failure_hints_do_not_advertise_startup() -> None:
    """Failure helpers must not look like a successful installation."""

    shell_script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    powershell_script = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")

    shell_hint = shell_script[shell_script.index("install_failure_hint()"): shell_script.index("usage()")]
    powershell_hint = powershell_script[
        powershell_script.index("function Show-InstallFailureHint"):
        powershell_script.index("function Show-Usage")
    ]

    for hint in (shell_hint, powershell_hint):
        assert "Then open the WebUI with:" not in hint
        assert "Run: pawbot" not in hint
        assert "Installation successful." not in hint
