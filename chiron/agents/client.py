"""LLM client abstraction for OpenAI-compatible endpoints.

CHIRON agents drive a model-agnostic chat + tool-calling loop. This client
wraps the ``openai`` package pointed at any OpenAI-compatible base URL, which
is how the paper's DeepSeek-V3.2 model is reached. It supports:

- plain chat completions,
- tool (function) calling with a registry of callable tools,
- structured-output parsing of text into JSON shims.

Secrets are always read from the environment at call time; the API key name is
resolved from configuration, never hardcoded.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from openai import OpenAI

from ..config import LlmConfig
from ..errors import LlmError

# A tool is a callable that takes a dict of string arguments and returns a
# string (usually JSON) to be fed back to the model.
ToolFn = Callable[[dict[str, Any]], str]

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class ChatMessage:
    """One message in the agent conversation."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    tool_call_id: str | None = None


class LlmClient:
    """Thin OpenAI-compatible client with tool-calling support."""

    def __init__(self, config: LlmConfig, *, api_key: str | None = None):
        self._config = config
        key = api_key or _read_api_key(config.api_key_env)
        self._client = OpenAI(
            base_url=config.base_url,
            api_key=key,
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
        )

    @property
    def model(self) -> str:
        return self._config.model

    def complete(self, messages: list[ChatMessage], *, max_tokens: int = 2000) -> str:
        """Return the assistant text reply (no tool calls)."""
        reply = self._chat(messages, tools=None, max_tokens=max_tokens)
        text = reply.get("content")
        if not text:
            raise LlmError("LLM returned an empty completion")
        return text

    def tool_call(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]],
        *,
        max_tokens: int = 2000,
    ) -> tuple[str, list[Any]]:
        """Run a single tool-calling turn.

        Returns ``(assistant_text, raw_tool_calls)`` where each raw tool call
        is an OpenAI-format call object. If the model issued tool calls, the
        caller is responsible for dispatching them and appending results.
        """
        reply = self._chat(messages, tools=tools, max_tokens=max_tokens)
        return reply.get("content") or "", reply.get("tool_calls") or []

    def complete_json(self, messages: list[ChatMessage], *, max_tokens: int = 3000) -> dict[str, Any]:
        """Request a JSON object and parse it robustly.

        Tries strict JSON, then JSON fenced in markdown, then the first
        ``{...}`` block in the reply.
        """
        text = self.complete(messages, max_tokens=max_tokens)
        return parse_json_object(text)

    # -- internals ----------------------------------------------------------- #

    def _chat(
        self, messages: list[ChatMessage], *, tools: list[dict[str, Any]] | None, max_tokens: int
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [_as_openai_message(m) for m in messages],
            "max_tokens": max_tokens,
            "temperature": self._config.temperature,
        }
        if tools:
            payload["tools"] = tools
        try:
            response = self._client.chat.completions.create(**payload)
        except Exception as exc:  # network / auth / API errors
            raise LlmError(f"LLM request failed: {exc}") from exc
        choice = response.choices[0]
        return choice.to_dict() if hasattr(choice, "to_dict") else choice


def _as_openai_message(message: ChatMessage) -> dict[str, Any]:
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id or "",
            "content": message.content,
        }
    base: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_call_id and message.role == "assistant":
        base["tool_call_id"] = message.tool_call_id
    return base


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse the first JSON object embedded in ``text`` (robust mode)."""
    candidates = _extract_candidates(text)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise LlmError("LLM output did not contain a parseable JSON object")


def _extract_candidates(text: str) -> list[str]:
    stripped = text.strip()
    candidates: list[str] = []
    if stripped:
        candidates.append(stripped)
    for group in _JSON_FENCE_RE.findall(text):
        candidates.append(group)
    if not candidates:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])
    return candidates


def _read_api_key(env_name: str) -> str:
    key = os.environ.get(env_name, "").strip()
    if not key:
        raise LlmError(
            f"LLM API key not set: define {env_name!r} in the environment "
            "(never hardcode secrets in source)"
        )
    return key