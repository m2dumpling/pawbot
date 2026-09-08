"""Slash command routing and built-in handlers."""

from pawbot.command.builtin import register_builtin_commands
from pawbot.command.router import CommandContext, CommandRouter

__all__ = ["CommandContext", "CommandRouter", "register_builtin_commands"]
