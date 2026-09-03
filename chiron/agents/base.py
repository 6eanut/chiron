"""Agent runtime: drives the LLM through a read-only tool loop.

An agent is composed of three pieces:

* a :class:`LlmClient` that talks to any OpenAI-compatible backend,
* a list of :class:`Tool` objects (normally from :func:`build_toolkit`),
* a system prompt that fixes the agent's role and output contract.

:func:`run_agent_loop` runs the cooperative tool loop: each turn the model may
issue zero or more tool calls; the harness executes them and feeds results
back, until the model returns a final assistant message without a tool call.
That final text is the agent's answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..errors import ToolError
from ..logging_utils import get_logger
from .client import ChatMessage, LlmClient
from .tools import Tool

log = get_logger(__name__)

MAX_TOOL_ITERATIONS = 12


@dataclass
class AgentResult:
    """The outcome of a completed agent run."""

    text: str
    tool_calls: int = 0
    iterations: int = 1


def _tool_call_id(call: object) -> str:
    """Extract the OpenAI ``id`` of a raw tool call object, tolerantly."""
    call_id = getattr(call, "id", None) or ""
    if not call_id:
        call_id = (call.get("id") if isinstance(call, dict) else "") or ""
    return call_id


def _tool_name(call: object) -> str:
    fn = getattr(call, "function", None)
    if fn is not None:
        return getattr(fn, "name", "") or ""
    if isinstance(call, dict):
        fn = call.get("function")
        if isinstance(fn, dict):
            return fn.get("name", "") or ""
    return ""


def _tool_arguments(call: object) -> dict:
    fn = getattr(call, "function", None)
    if fn is not None:
        raw = getattr(fn, "arguments", "{}") or "{}"
    elif isinstance(call, dict):
        fn = call.get("function")
        raw = fn.get("arguments", "{}") if isinstance(fn, dict) else "{}"
    else:
        raw = "{}"
    import json

    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _default_execute(tool_by_name: dict[str, Tool]):
    """Return an executor that dispatches to the tool registry, catching errors."""

    def execute(name: str, args: dict) -> str:
        tool = tool_by_name.get(name)
        if tool is None:
            raise ToolError(f"Unknown tool requested by model: {name!r}")
        try:
            return str(tool.fn(args))
        except Exception as exc:  # surface a readable error to the model
            log.warning("Tool %s failed: %s", name, exc)
            return f"Error running tool {name!r}: {exc}"

    return execute


def _final_answer(messages: list[ChatMessage]) -> ChatMessage:
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg
    return ChatMessage(role="assistant", content="")


def run_agent_loop(
    *,
    client: LlmClient,
    system_prompt: str,
    tools: Sequence[Tool],
    messages: list[ChatMessage] | None = None,
    max_tokens: int = 2000,
    max_tool_iterations: int = MAX_TOOL_ITERATIONS,
) -> AgentResult:
    """Run the tool-calling loop for a single agent until it stops.

    The model's final assistant text is returned. The loop is bounded by
    ``max_tool_iterations`` to guard against the model calling tools forever.
    """

    if messages is None:
        messages = []
    history: list[ChatMessage] = [ChatMessage(role="system", content=system_prompt), *messages]

    tool_by_name = {t.name: t for t in tools}
    execute = _default_execute(tool_by_name)
    tool_calls = 0
    iterations = 0

    for iterations in range(1, max_tool_iterations + 1):
        text, raw_calls = client.tool_call(history, [t.to_openai_schema() for t in tools], max_tokens=max_tokens)

        # Persist the assistant turn. The client returns the text separately
        # from the tool calls; record the text as the assistant message content.
        history.append(ChatMessage(role="assistant", content=text))

        if not raw_calls:
            break

        last_arguments = None
        for call in raw_calls:
            call_id = _tool_call_id(call)
            name = _tool_name(call)
            arguments = _tool_arguments(call)
            if not name:
                continue
            tool_calls += 1
            last_arguments = arguments
            output = execute(name, arguments)
            history.append(
                ChatMessage(role="tool", content=output, tool_call_id=call_id)
            )
        _ = last_arguments

        if iterations == max_tool_iterations:
            log.warning("Tool loop reached %d iterations without a final answer", max_tool_iterations)

    final = _final_answer(history)
    return AgentResult(text=final.content or "", tool_calls=tool_calls, iterations=iterations)


__all__ = ["AgentResult", "run_agent_loop"]