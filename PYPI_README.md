# Pawbot

Pawbot is a self-hosted Python agent with a WebUI, a native terminal UI,
command-line access, and an OpenAI-compatible API. It supports multi-step
tool use, MCP integration, session recovery, and replayable agent runs.

## Install and start

Install or upgrade with [uv](https://docs.astral.sh/uv/):

```sh
uv tool install --force --upgrade pawbot-ai
pawbot --version
pawbot webui
```

Alternatively, install `pawbot-ai` in a Python 3.11+ virtual environment with
`python -m pip install --upgrade pawbot-ai`. Configure a model provider in the
WebUI before starting a conversation. Run `pawbot agent` for the terminal UI.

## Documentation and evaluation

- [Project and complete installation guide](https://github.com/m2dumpling/pawbot)
- [Latest GitHub release](https://github.com/m2dumpling/pawbot/releases/latest)
- [Usage guide](https://github.com/m2dumpling/pawbot/blob/main/docs/usage.md)
- [Promptfoo evaluation guide](https://github.com/m2dumpling/pawbot/blob/main/evals/promptfoo/README.md)

The Promptfoo suites are developer tools in the source repository; they are
not included in the Python wheel. Real-model evaluations require configured
provider credentials and consume model usage. Temporary evaluation workspaces
are not operating-system security sandboxes.
