"""Concrete task agent bound to a role, a toolkit, and a structured output.

A :class:`TaskAgent` is the reusable unit CHIRON's multi-view diagnosis and
repair loops are built on. It bundles:

* the shared :class:`LlmClient`,
* a system prompt fixing the role,
* a read-only toolkit over the kernel tree,
* an optional JSON output contract (enforced by the caller).

Calling ``.run(prompt, tools_override=None)`` executes the tool loop and
returns the final assistant text.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .base import AgentResult, run_agent_loop
from .client import ChatMessage, LlmClient
from .tools import Tool

__all__ = ["TaskAgent"]


class TaskAgent:
    """A single-role agent that runs a bounded tool loop and returns text."""

    def __init__(
        self,
        *,
        client: LlmClient,
        role: str,
        system_prompt: str,
        tools: Optional[Sequence[Tool]] = None,
        max_tokens: int = 2000,
    ):
        self._client = client
        self.role = role
        self._system_prompt = system_prompt
        self._tools = tuple(tools or ())
        self._max_tokens = max_tokens

    def run(
        self,
        prompt: str,
        *,
        tools: Optional[Sequence[Tool]] = None,
        max_tokens: Optional[int] = None,
    ) -> AgentResult:
        """Run one agent turn with the given user prompt (and optional tools)."""
        active_tools = tuple(tools or self._tools)
        return run_agent_loop(
            client=self._client,
            system_prompt=self._system_prompt,
            tools=active_tools,
            messages=[ChatMessage(role="user", content=prompt)],
            max_tokens=max_tokens or self._max_tokens,
        )

    @property
    def tools(self) -> tuple[Tool, ...]:
        return self._tools