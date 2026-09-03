"""CHIRON agents package: LLM clients, toolkits, and the agent runtime."""

from ..config import LlmConfig
from .base import AgentResult, run_agent_loop
from .client import ChatMessage, LlmClient
from .task_agent import TaskAgent
from .tools import Tool, ToolContext, build_toolkit, format_tools_schema

__all__ = [
    "AgentResult",
    "ChatMessage",
    "LlmClient",
    "LlmConfig",
    "TaskAgent",
    "Tool",
    "ToolContext",
    "build_toolkit",
    "format_tools_schema",
    "run_agent_loop",
]