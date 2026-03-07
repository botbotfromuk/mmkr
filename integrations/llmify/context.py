"""llmify compilation phase — funcai-native compilation target.

llmify is a proper compiler. Its output is funcai primitives: Messages and
Tools. Capabilities contribute to the LLM verification context through
the fold — same mechanism as Pydantic, OpenAPI, SQL, CLI.

Field-level: capabilities implement compile_llmify → add Messages/Tools.
Entity-level: schema_meta caps implement compile_llmify_entity → same.

Usage (Contract — field-level, adds message)::

    @dataclass(frozen=True, slots=True)
    class Contract(SchemaAxisCapability):
        text: str
        def compile_llmify(self, ctx: LlmifyContext) -> LlmifyContext:
            return replace(ctx, messages=(*ctx.messages,
                system(text=f'Field "{ctx.field_name}" contract: "{self.text}"')
            ))

Usage (future — capability contributing a tool)::

    @dataclass(frozen=True, slots=True)
    class ValidateAgainstAPI(SchemaAxisCapability):
        base_url: str
        def compile_llmify(self, ctx: LlmifyContext) -> LlmifyContext:
            return replace(ctx,
                messages=(*ctx.messages, system(text=f"API at {self.base_url}")),
                tools=(*ctx.tools, make_probe_tool(self.base_url)),
            )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from emergent.wire.compile._phase import CompilationPhase, EntityFold

if TYPE_CHECKING:
    from funcai.agents.tool import Tool
    from funcai.core.message import Message


# ═══════════════════════════════════════════════════════════════════════════════
# Field-level context + protocol
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class LlmifyContext:
    """Per-field context — accumulates funcai Messages and Tools via fold."""

    field_name: str
    field_type: type
    messages: tuple[Message, ...] = ()
    tools: tuple[Tool, ...] = ()


@runtime_checkable
class LlmifyCompilable(Protocol):
    """Capability that contributes to the LLM verification context.

    Implement on any SchemaAxisCapability. The fold discovers it
    automatically — no registration needed.

    Add Messages (context the LLM needs) or Tools (actions the LLM can take).
    """

    def compile_llmify(self, ctx: LlmifyContext) -> LlmifyContext: ...


# ═══════════════════════════════════════════════════════════════════════════════
# Entity-level context + protocol
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class LlmifyEntityContext:
    """Entity-level context — accumulates funcai Messages and Tools.

    Folded from @schema_meta capabilities (EntityContract, etc.).
    """

    messages: tuple[Message, ...] = ()
    tools: tuple[Tool, ...] = ()


@runtime_checkable
class LlmifyEntityCompilable(Protocol):
    """Entity-level capability that contributes to the LLM context.

    Implement on any SchemaCapability used with @schema_meta.
    """

    def compile_llmify_entity(self, ctx: LlmifyEntityContext) -> LlmifyEntityContext: ...


# ═══════════════════════════════════════════════════════════════════════════════
# Phase — field-level fold + entity-level fold
# ═══════════════════════════════════════════════════════════════════════════════


LLMIFY_ENTITY_FOLD: EntityFold[LlmifyEntityContext] = EntityFold(
    LlmifyEntityContext,
    LlmifyEntityCompilable,
    lambda _name: LlmifyEntityContext(),
)

LLMIFY_PHASE: CompilationPhase[LlmifyContext] = CompilationPhase(
    LlmifyContext,
    LlmifyCompilable,
    lambda n, t: LlmifyContext(field_name=n, field_type=t),
    entity=LLMIFY_ENTITY_FOLD,
)
