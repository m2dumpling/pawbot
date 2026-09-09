# Publishing pawbot

This guide covers the public release path for the `pawbot-ai` package. GitHub
source releases and PyPI package releases are separate steps.

## GitHub Release

1. Wait for the `CI` workflow on `main` to finish successfully.
2. Open **Releases → Draft a new release** in the repository.
3. Create or select the new version tag (for example, `v0.3.4`) targeting the latest green `main` commit.
4. Use the matching file under `docs/release-notes/` as the release description.

Publishing the GitHub Release triggers both `.github/workflows/publish.yml` for
the Python package and `.github/workflows/publish-tui.yml` for the native TUI
archives. The latter attaches matching archives for Linux x64/ARM64, macOS
Intel/Apple Silicon, and Windows x64 to the same release.

## One-time PyPI setup

Before publishing the first GitHub Release, configure a Trusted Publisher in
PyPI. This avoids placing a PyPI token in the repository or GitHub secrets.

In PyPI, open **Account settings → Publishing → Add a new pending publisher**
and enter:

```text
PyPI project name: pawbot-ai
Owner: m2dumpling
Repository: pawbot
Workflow name: publish.yml
Environment name: leave empty
```

Save the publisher. The project may be configured as a pending publisher before
the first upload; the first successful workflow run creates the package.

## Verify the package

After the publish workflow succeeds, verify the package page:

```text
https://pypi.org/project/pawbot-ai/
```

Then test the public package in a clean environment:

```bash
uv tool install --force --upgrade pawbot-ai
pawbot --version
```

On Windows PowerShell:

```powershell
uv tool install --force --upgrade pawbot-ai
pawbot --version
```

For the GitHub installer, test the platform-specific commands as well:

```bash
curl -fsSL https://raw.githubusercontent.com/m2dumpling/pawbot/vX.Y.Z/scripts/install.sh | sh
```

```powershell
iex (irm https://raw.githubusercontent.com/m2dumpling/pawbot/vX.Y.Z/scripts/install.ps1)
```

The installer must verify `pawbot --version` before printing a success message.
On a minimal Debian/Ubuntu image without `venv/ensurepip`, it must stop before
creating the managed environment and print the matching `python3.x-venv`
command. It must not print a WebUI or `pawbot` startup command in that failure
path. On Windows, the generated launcher is placed in the user's Pawbot bin
directory and added to the user `PATH`; on POSIX systems, the installer prints
the verified launcher path and an `export PATH=...` command when the current
shell does not already contain that directory.

The installer opens the WebUI on a local desktop. The first user needs to
configure a Provider in **Settings → Models**; Quick Start and compatible
provider settings can discover the account's models from `/models` and enrich
known IDs with context-window and reasoning metadata. Providers without a
model catalogue still support manual model IDs.

For a Linux server, keep the default localhost binding unless a remote browser
is required. To expose the WebUI deliberately, use
`pawbot webui --host 0.0.0.0 --yes --no-open`, open the server IP on the WebUI
port, and enter the configured `channels.websocket.tokenIssueSecret`. Keep the
gateway health port private; an SSH tunnel is the safer alternative.

## Release safety

- Do not add `PYPI_TOKEN` or provider credentials to the repository.
- The publish workflow only runs for a published GitHub Release.
- Keep the version in `pyproject.toml`, the release tag, and the release notes
  aligned.
- Wait for the CI installer smoke jobs on Ubuntu, macOS, and Windows before
  publishing a release.
- Wait for the native TUI matrix and the TUI asset-upload job after publishing;
  a release is ready for terminal users only when its matching archives are
  attached.
- If the workflow fails, fix the publisher or build issue before retrying; do
  not create a second tag for the same version.
