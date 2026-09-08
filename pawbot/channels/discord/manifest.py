"""Discord management contract."""

from pawbot.channels._manifest import DIRECT_GROUP_POLICIES, field, required
from pawbot.channels.contracts import ChannelSetupSpec
from pawbot.channels.discord.validation import validate
from pawbot.channels.plugin import ChannelPlugin

SETUP_SPEC = ChannelSetupSpec(
    fields={
        "token": field("secret"),
        "allowFrom": field("list", snapshot=False),
        "allowChannels": field("list"),
        "groupPolicy": field("enum", choices=DIRECT_GROUP_POLICIES, default="mention"),
    },
    required=(required("token"),),
    official_url="https://discord.com/developers/applications",
    validator=validate,
)

PLUGIN = ChannelPlugin(
    name="discord",
    display_name="Discord",
    runtime=f"{__package__}.runtime:DiscordChannel",
    setup=SETUP_SPEC,
    dependencies=("discord.py>=2.5.2,<3.0.0",),
    webui="webui/index.ts",
)
