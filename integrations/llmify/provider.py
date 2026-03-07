"""Clean JSON provider for OpenAI-compatible APIs that don't support structured output.

Adapted from openemergent/agent/_provider.py. Wraps any OpenAIProvider,
injects JSON Schema into prompts, extracts and validates JSON from plain-text
responses. Handles markdown fences, brace-matching for incomplete JSON, etc.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from kungfu import Error, Nothing, Ok, Option, Result, Some
from pydantic import BaseModel

from funcai.core.message import Message, system
from funcai.core.provider import ABCAIProvider, AIResponse
from funcai.std.providers.openai import OpenAIError, OpenAIProvider

if TYPE_CHECKING:
    from funcai.agents.tool import Tool


def _extract_json(text: str) -> str:
    """Extract JSON from text that may contain markdown fences or other wrapping."""
    m = re.search(r"```(?:json|yaml|)\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()

    start = text.find("{")
    if start == -1:
        return text

    # Brace-match, skipping braces inside strings
    depth = 0
    end = start
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    return text[start:end]


def _repair_json(text: str) -> str:
    """Repair common LLM JSON issues.

    - Unescaped newlines/tabs inside string values
    - Trailing commas before } or ]
    """
    # Fix unescaped newlines/tabs inside JSON strings
    result: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            result.append(ch)
            escape = False
            continue
        if ch == "\\":
            result.append(ch)
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string:
            if ch == "\n":
                result.append("\\n")
                continue
            if ch == "\t":
                result.append("\\t")
                continue
            if ch == "\r":
                continue
        result.append(ch)
    text = "".join(result)

    # Remove trailing commas: ,] or ,}
    text = re.sub(r",\s*([}\]])", r"\1", text)

    return text


def _schema_instruction(s: type[BaseModel]) -> str:
    """Build a system instruction that emulates response_format."""
    schema_json = json.dumps(s.model_json_schema(), indent=2)
    return (
        "You MUST respond with a single JSON object matching this exact JSON Schema. "
        "Use ONLY the field names defined in the schema — no aliases, no extras. "
        "Output raw JSON only, no markdown fences, no explanation.\n\n"
        f"JSON Schema:\n{schema_json}"
    )


class CleanJsonProvider(ABCAIProvider[OpenAIError]):
    """Wraps OpenAIProvider, emulating structured output for non-compliant models.

    When a schema is requested:
    1. Injects the JSON Schema into messages as a system instruction
    2. Calls the inner provider without response_format
    3. Extracts JSON from the plain-text response
    4. Validates via Pydantic

    Retries on connection/transient errors (configurable).
    For regular calls: passes through to the inner provider.
    """

    def __init__(self, inner: OpenAIProvider, *, max_retries: int = 3) -> None:
        self._inner = inner
        self._max_retries = max_retries

    async def _call_with_retry(
        self,
        messages: list[Message],
        tools: list[Tool] | None,
    ) -> Result[AIResponse[BaseModel], OpenAIError]:
        last_error: OpenAIError | None = None
        for attempt in range(self._max_retries):
            result = await self._inner.send_messages(
                messages, schema=Nothing(), tools=tools,
            )
            match result:
                case Ok():
                    return result
                case Error(e):
                    last_error = e
                    msg = e.message if hasattr(e, "message") else str(e)
                    is_transient = any(k in msg.lower() for k in (
                        "connection", "timeout", "reset", "refused",
                        "temporarily", "503", "502", "429",
                    ))
                    if not is_transient:
                        return result
                    if attempt < self._max_retries - 1:
                        import asyncio
                        wait = 2 ** attempt
                        print(f"[retry {attempt + 1}/{self._max_retries}] {msg[:80]}... waiting {wait}s")
                        await asyncio.sleep(wait)
        return Error(last_error or OpenAIError(message="max retries exhausted", code=Some("retry_exhausted")))

    async def send_messages[S: BaseModel](
        self,
        messages: list[Message],
        *,
        schema: Option[type[S]] = Nothing(),
        tools: list[Tool] | None = None,
    ) -> Result[AIResponse[S], OpenAIError]:
        match schema:
            case Some(s):
                augmented = [
                    *messages,
                    system(text=_schema_instruction(s)),
                ]
                result = await self._call_with_retry(augmented, tools)
                match result:
                    case Ok(response):
                        raw = response.message.text.unwrap_or("")
                        extracted = _extract_json(raw)
                        repaired = _repair_json(extracted)
                        try:
                            parsed = s.model_validate_json(repaired)
                            return Ok(AIResponse(
                                message=response.message,
                                tool_calls=response.tool_calls,
                                parsed=Some(parsed),
                                meta=response.meta,
                            ))
                        except Exception as exc:
                            return Error(OpenAIError(
                                message=f"JSON parse failed: {exc}\nRaw: {raw[:500]}",
                                code=Some("parse_error"),
                            ))
                    case Error(e):
                        return Error(e)
            case _:
                return await self._call_with_retry(messages, tools)


def make_zai_provider(
    model: str = "GLM-4.7",
    api_key: str | None = None,
    base_url: str = "https://api.z.ai/api/coding/paas/v4",
    timeout: float = 120.0,
) -> CleanJsonProvider:
    """Create a CleanJsonProvider for z.ai (OpenAI-compatible)."""
    from openai import AsyncOpenAI

    inner = OpenAIProvider(
        model=model,
        api_key=Some(api_key),
        base_url=Some(base_url),
    )
    # Override client with explicit timeout — z.ai is slow
    inner.client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
    )
    return CleanJsonProvider(inner)
