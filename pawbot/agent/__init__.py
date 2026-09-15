"""Agent core module."""

from pawbot.agent.approval import (
    DEFAULT_APPROVAL_CAPABILITIES,
    ToolApprovalManager,
    ToolApprovalRequest,
    ToolApprovalResult,
)
from pawbot.agent.budget import TurnBudget
from pawbot.agent.context import ContextBuilder
from pawbot.agent.evaluation import TaskContract, TaskEvaluation, evaluate_task
from pawbot.agent.hook import (
    AgentHook,
    AgentHookContext,
    AgentRunHookContext,
    AgentTurnHookContext,
    AgentTurnHookFactory,
    CompositeHook,
)
from pawbot.agent.loop import AgentLoop
from pawbot.agent.memory import MemoryStore
from pawbot.agent.skills import SkillsLoader
from pawbot.agent.subagent import SubagentManager

__all__ = [
    "DEFAULT_APPROVAL_CAPABILITIES",
    "AgentHook",
    "AgentHookContext",
    "AgentRunHookContext",
    "AgentTurnHookContext",
    "AgentTurnHookFactory",
    "AgentLoop",
    "CompositeHook",
    "ContextBuilder",
    "TurnBudget",
    "MemoryStore",
    "SkillsLoader",
    "SubagentManager",
    "TaskContract",
    "TaskEvaluation",
    "ToolApprovalManager",
    "ToolApprovalRequest",
    "ToolApprovalResult",
    "evaluate_task",
]
