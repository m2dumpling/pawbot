"""Slack management contract."""

from pawbot.channels._manifest import GROUP_POLICIES, field, required_fields
from pawbot.channels.contracts import ChannelSetupSpec
from pawbot.channels.plugin import ChannelPlugin
from pawbot.channels.slack.validation import validate

SETUP_SPEC = ChannelSetupSpec(
    fields={
        "appToken": field("secret"),
        "botToken": field("secret"),
        "groupPolicy": field("enum", choices=GROUP_POLICIES, default="mention"),
    },
    required=required_fields("appToken", "botToken"),
    official_url="https://api.slack.com/apps",
    validator=validate,
)

PLUGIN = ChannelPlugin(
    name="slack",
    display_name="Slack",
    runtime=f"{__package__}.runtime:SlackChannel",
    setup=SETUP_SPEC,
    dependencies=(
        "aiohttp>=3.9.0,<4.0.0",
        "slack-sdk>=3.39.0,<4.0.0",
        "slackify-markdown>=0.2.0,<1.0.0",
    ),
    webui="webui/index.ts",
)
