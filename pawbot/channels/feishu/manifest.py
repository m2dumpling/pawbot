"""Dependency-free Feishu/Lark management contract."""

from pawbot.channels._manifest import DIRECT_GROUP_POLICIES, field, required_fields
from pawbot.channels.contracts import ChannelSetupSpec
from pawbot.channels.feishu.instances import FEISHU_MANAGEMENT
from pawbot.channels.feishu.validation import validate
from pawbot.channels.plugin import ChannelPlugin

SETUP_SPEC = ChannelSetupSpec(
    fields={
        "appId": field(snapshot=False),
        "appSecret": field("secret", snapshot=False),
        "domain": field(
            "enum",
            choices={"feishu", "lark"},
            default="feishu",
            snapshot=False,
        ),
        "groupPolicy": field(
            "enum",
            choices=DIRECT_GROUP_POLICIES,
            default="mention",
            snapshot=False,
        ),
        "allowFrom": field("list", snapshot=False),
        "topicIsolation": field("bool", default=True, snapshot=False),
    },
    required=required_fields("appId", "appSecret"),
    official_url="https://open.feishu.cn/app",
    validator=validate,
)

PLUGIN = ChannelPlugin(
    name="feishu",
    display_name="Feishu",
    runtime=f"{__package__}.runtime:FeishuChannel",
    connector=f"{__package__}.connect:FeishuConnectStore",
    setup=SETUP_SPEC,
    management=FEISHU_MANAGEMENT,
    dependencies=("lark-oapi>=1.5.0,<2.0.0",),
    webui="webui/index.tsx",
)
