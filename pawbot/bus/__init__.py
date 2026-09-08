"""Message bus module for decoupled channel-agent communication."""

from pawbot.bus.events import InboundMessage, OutboundMessage
from pawbot.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
