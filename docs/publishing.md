# Publishing pawbot

This guide covers the public release path for the `pawbot-ai` package. GitHub
source releases and PyPI package releases are separate steps.

## GitHub Release

1. Wait for the `CI` workflow on `main` to finish successfully.
2. Open **Releases → Draft a new release** in the repository.
3. Create or select tag `v0.3.0` targeting the latest green `main` commit.
4. Use `docs/release-notes/0.3.0.md` as the release description.

Publishing the GitHub Release triggers `.github/workflows/publish.yml`.

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

Then test the public installer in a clean environment:

```bash
uv tool install --force --upgrade pawbot-ai
pawbot
```

On Windows PowerShell:

```powershell
uv tool install --force --upgrade pawbot-ai
pawbot
```

The installer opens the WebUI on a local desktop. The first user still needs
to configure a Provider, API key, and model in **Settings → Models**.

## Release safety

- Do not add `PYPI_TOKEN` or provider credentials to the repository.
- The publish workflow only runs for a published GitHub Release.
- Keep the version in `pyproject.toml`, the release tag, and the release notes
  aligned.
- If the workflow fails, fix the publisher or build issue before retrying; do
  not create a second tag for the same version.
