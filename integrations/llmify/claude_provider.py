"""Claude provider for funcai — Anthropic API with native tool use + prompt caching.

Fully programmatic. One process. No CLI, no subprocess, no MCP.

Authenticates via OAuth token from ~/.openclaw (same as Claude Code).
Sends same headers as Claude Code for genuine API access.
funcai Tools compile to Anthropic tool definitions.
Claude calls tools natively → we execute in-process → result back.

stream=True enables real-time output to stdout:
  thinking blocks, text, tool calls + results — all visible.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import anthropic
from kungfu import Error, Nothing, Ok, Option, Result, Some
from pydantic import BaseModel

from funcai.core.message import Message, Role, assistant
from funcai.core.provider import ABCAIProvider, AIResponse

if TYPE_CHECKING:
    from funcai.agents.tool import Tool


# ═══════════════════════════════════════════════════════════════════════════════
# Auth — read OAuth token from ~/.openclaw
# ═══════════════════════════════════════════════════════════════════════════════

_OPENCLAW_AUTH = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
_CLAUDE_CODE_VERSION = "2.1.68"
_OAUTH_BETA = "oauth-2025-04-20"


def _load_oauth_token(path: Path = _OPENCLAW_AUTH) -> str:
    """Load Anthropic OAuth token from openclaw auth profiles."""
    data = json.loads(path.read_text())
    profiles: dict[str, dict[str, str]] = data.get("profiles", {})
    for profile in profiles.values():
        if profile.get("provider") == "anthropic" and "token" in profile:
            return profile["token"]
    msg = f"No anthropic token found in {path}"
    raise ValueError(msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Error type
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ClaudeError:
    """Error from Anthropic API."""

    message: str
    code: Option[str] = field(default_factory=Nothing)

    def __str__(self) -> str:
        match self.code:
            case Some(c):
                return f"ClaudeError[{c}]: {self.message}"
            case Nothing():
                return f"ClaudeError: {self.message}"


# ═══════════════════════════════════════════════════════════════════════════════
# Tool compilation: funcai Tool → Anthropic tool definition
# ═══════════════════════════════════════════════════════════════════════════════


def compile_tool_definitions(
    tools: Sequence[Tool],
) -> list[anthropic.types.ToolParam]:
    """Compile funcai Tools → Anthropic API tool definitions.

    Pure function. Same tools → same definitions.
    Last tool gets cache_control for prompt caching.
    """
    defs: list[anthropic.types.ToolParam] = []
    tools_list = list(tools)
    for i, t in enumerate(tools_list):
        schema = t.parameters.model_json_schema()
        tool_def: anthropic.types.ToolParam = {
            "name": t.name,
            "description": t.description,
            "input_schema": schema,  # type: ignore[typeddict-item]
        }
        if i == len(tools_list) - 1:
            tool_def["cache_control"] = {"type": "ephemeral"}  # type: ignore[typeddict-unknown-key]
        defs.append(tool_def)
    return defs


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _extract_json(text: str) -> str:
    """Extract JSON from text that may contain markdown fences or other wrapping."""
    m = re.search(r"```(?:json|yaml|)\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()

    start = text.find("{")
    if start == -1:
        return text

    depth = 0
    end = start
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    return text[start:end]


def _schema_instruction(s: type[BaseModel]) -> str:
    """Build a system instruction that emulates response_format."""
    schema_json = json.dumps(s.model_json_schema(), indent=2)
    return (
        "You MUST respond with a single JSON object matching this exact JSON Schema. "
        "Use ONLY the field names defined in the schema — no aliases, no extras. "
        "Output raw JSON only, no markdown fences, no explanation.\n\n"
        f"JSON Schema:\n{schema_json}"
    )


def _messages_to_anthropic(
    messages: list[Message],
) -> tuple[str, list[anthropic.types.MessageParam]]:
    """Convert funcai messages to (system_prompt, anthropic_messages)."""
    system_parts: list[str] = []
    api_messages: list[anthropic.types.MessageParam] = []

    for msg in messages:
        text = msg.text.unwrap_or("")
        match msg.role:
            case Role.SYSTEM:
                system_parts.append(text)
            case Role.USER:
                api_messages.append({"role": "user", "content": text})
            case Role.ASSISTANT:
                api_messages.append({"role": "assistant", "content": text})
            case Role.TOOL:
                api_messages.append({"role": "user", "content": f"[Tool Result]\n{text}"})

    return "\n\n".join(system_parts), api_messages


# ═══════════════════════════════════════════════════════════════════════════════
# Client factory
# ═══════════════════════════════════════════════════════════════════════════════


def _make_client(token: str) -> anthropic.AsyncAnthropic:
    """Create Anthropic client mimicking Claude Code.

    OAuth tokens use auth_token (Bearer), not api_key (x-api-key).
    """
    return anthropic.AsyncAnthropic(
        auth_token=token,
        default_headers={
            "anthropic-beta": _OAUTH_BETA,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Provider
# ═══════════════════════════════════════════════════════════════════════════════


class ClaudeProvider(ABCAIProvider[ClaudeError]):
    """funcai provider backed by Anthropic API with native tool use.

    Authenticates with OAuth token from ~/.openclaw (same as Claude Code).
    Prompt caching via cache_control. Full agentic tool loop.

    stream=True prints all LLM output to stdout in real-time:
      thinking, text, tool calls, tool results.
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        max_tokens: int = 16384,
        max_tool_rounds: int = 20,
        thinking: bool = False,
        funcai_tools: Sequence[Tool] = (),
        token: str = "",
        stream: bool = False,
    ) -> None:
        resolved_token = token or _load_oauth_token()
        self._client = _make_client(resolved_token)
        self._model = model
        self._max_tokens = max_tokens
        self._max_tool_rounds = max_tool_rounds
        self._thinking = thinking
        self._funcai_tools = {t.name: t for t in funcai_tools}
        self._tool_defs = compile_tool_definitions(funcai_tools) if funcai_tools else []
        self._stream = stream
        self._last_api_messages: list[anthropic.types.MessageParam] = []

    async def send_messages[S: BaseModel](
        self,
        messages: list[Message],
        *,
        schema: Option[type[S]] = Nothing(),
        tools: list[Tool] | None = None,
    ) -> Result[AIResponse[S], ClaudeError]:
        # Resolve tools: explicit param > constructor
        if tools is not None:
            active_tools = {t.name: t for t in tools}
            active_tool_defs = compile_tool_definitions(tools) if tools else []
        else:
            active_tools = self._funcai_tools
            active_tool_defs = self._tool_defs

        system_prompt, api_messages = _messages_to_anthropic(messages)

        # Schema instruction appended to last user message
        s: type[S] | None = None
        match schema:
            case Some(schema_type):
                s = schema_type
                schema_instr = _schema_instruction(s)
                if api_messages and api_messages[-1]["role"] == "user":
                    content = api_messages[-1]["content"]
                    api_messages[-1] = {
                        "role": "user",
                        "content": f"{content}\n\n{schema_instr}",
                    }
                else:
                    api_messages.append({"role": "user", "content": schema_instr})

        # System prompt with cache_control
        system: str | list[anthropic.types.TextBlockParam] = system_prompt
        if system_prompt:
            system = [{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }]

        # Agentic loop: Claude calls tools → we execute → send result back
        result_text = ""
        api_kwargs: dict[str, object] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "tools": active_tool_defs if active_tool_defs else anthropic.NOT_GIVEN,
        }
        if self._thinking:
            api_kwargs["thinking"] = {"type": "adaptive"}

        for _round in range(self._max_tool_rounds):
            try:
                if self._stream:
                    response = await self._stream_round(api_messages, api_kwargs)
                else:
                    response = await self._client.messages.create(
                        messages=api_messages, **api_kwargs,  # type: ignore[arg-type]
                    )
            except anthropic.APIError as exc:
                return Error(ClaudeError(
                    message=str(exc),
                    code=Some(getattr(exc, "type", "api_error")),
                ))

            # Collect text and tool_use blocks (skip thinking blocks)
            text_parts: list[str] = []
            tool_uses: list[anthropic.types.ToolUseBlock] = []

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_uses.append(block)

            result_text = "\n".join(text_parts)

            # No tool calls → done
            if not tool_uses or response.stop_reason != "tool_use":
                break

            # Execute tools and build tool_result messages
            api_messages.append({
                "role": "assistant",
                "content": response.content,  # type: ignore[arg-type]
            })

            tool_results: list[anthropic.types.ToolResultBlockParam] = []
            for tu in tool_uses:
                funcai_tool = active_tools.get(tu.name)
                if funcai_tool is None:
                    if self._stream:
                        print(f"  ? unknown tool: {tu.name}", flush=True)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": f"Unknown tool: {tu.name}",
                        "is_error": True,
                    })
                    continue

                try:
                    kwargs = tu.input if isinstance(tu.input, dict) else {}
                    result = await funcai_tool.execute(**kwargs)
                    result_str = str(result)
                    if self._stream:
                        print(f"  <- {result_str}", flush=True)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": result_str,
                    })
                except Exception as exc:
                    if self._stream:
                        print(f"  !! {tu.name} error: {exc}", flush=True)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": f"Tool error: {exc}",
                        "is_error": True,
                    })

            api_messages.append({"role": "user", "content": tool_results})  # type: ignore[arg-type]

        if self._stream:
            print(flush=True)  # final newline

        self._last_api_messages = list(api_messages)
        response_message = assistant(text=result_text)

        if s is None:
            return Ok(AIResponse(
                message=response_message,
                parsed=Nothing(),
            ))

        extracted = _extract_json(result_text)
        try:
            parsed = s.model_validate_json(extracted)
            return Ok(AIResponse(
                message=response_message,
                parsed=Some(parsed),
            ))
        except Exception as exc:
            return Error(ClaudeError(
                message=f"JSON parse failed: {exc}\nRaw: {result_text[:500]}",
                code=Some("parse_error"),
            ))

    async def _stream_round(
        self,
        api_messages: list[anthropic.types.MessageParam],
        api_kwargs: dict[str, object],
    ) -> anthropic.types.Message:
        """One API round with streaming — prints to stdout in real-time."""
        out = sys.stdout
        in_thinking = False
        in_tool_use = False

        async with self._client.messages.stream(
            messages=api_messages, **api_kwargs,  # type: ignore[arg-type]
        ) as stream:
            async for event in stream:
                match event.type:
                    case "content_block_start":
                        block = event.content_block
                        if block.type == "thinking":  # type: ignore[union-attr]
                            in_thinking = True
                            out.write("\n  💭 ")
                            out.flush()
                        elif block.type == "text":
                            out.write("\n")
                            out.flush()
                        elif block.type == "tool_use":
                            in_tool_use = True
                            out.write(f"\n  🔧 {block.name}(")  # type: ignore[union-attr]
                            out.flush()

                    case "content_block_delta":
                        delta = event.delta
                        if delta.type == "thinking_delta":  # type: ignore[union-attr]
                            out.write(delta.thinking)  # type: ignore[union-attr]
                            out.flush()
                        elif delta.type == "text_delta":  # type: ignore[union-attr]
                            out.write(delta.text)  # type: ignore[union-attr]
                            out.flush()
                        elif delta.type == "input_json_delta":  # type: ignore[union-attr]
                            out.write(delta.partial_json)  # type: ignore[union-attr]
                            out.flush()

                    case "content_block_stop":
                        if in_thinking:
                            out.write("\n")
                            out.flush()
                            in_thinking = False
                        elif in_tool_use:
                            out.write(")")
                            out.flush()
                            in_tool_use = False
                        else:
                            out.write("\n")
                            out.flush()

            return await stream.get_final_message()


def make_claude_provider(
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 16384,
    max_tool_rounds: int = 20,
    thinking: bool = False,
    funcai_tools: Sequence[Tool] = (),
    token: str = "",
    stream: bool = False,
) -> ClaudeProvider:
    """Create a ClaudeProvider via Anthropic API.

    Authenticates with OAuth token from ~/.openclaw (same as Claude Code).
    Fully programmatic. Native tool use. Prompt caching.

    Args:
        model: Full model ID (e.g. "claude-opus-4-6", "claude-haiku-4-5-20251001").
        max_tokens: Max output tokens per API call.
        max_tool_rounds: Max tool call rounds before stopping.
        thinking: Enable adaptive thinking (Opus 4.6, Sonnet 4.6).
        funcai_tools: funcai Tools to expose. Compiled to Anthropic format.
        token: OAuth token override. Empty = auto-load from ~/.openclaw.
        stream: Stream LLM output (thinking, text, tool calls) to stdout.
    """
    return ClaudeProvider(
        model=model, max_tokens=max_tokens,
        max_tool_rounds=max_tool_rounds, thinking=thinking,
        funcai_tools=funcai_tools, token=token,
        stream=stream,
    )


__all__ = ("ClaudeError", "ClaudeProvider", "make_claude_provider", "compile_tool_definitions")
