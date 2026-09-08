from pawbot.agent.tools.context import ToolContext
from pawbot.agent.tools.loader import ToolLoader
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.config.schema import Config


def test_tool_loader_scope_memory_only_returns_memory_tools():
    loader = ToolLoader()
    registry = ToolRegistry()
    ctx = ToolContext(config=Config().tools, workspace="/tmp")
    loader.load(ctx, registry, scope="memory")

    names = set(registry.tool_names)
    assert "read_file" in names
    assert "edit_file" in names
    assert "write_file" in names
    assert "list_dir" not in names
    assert "exec" not in names
    assert "message" not in names
