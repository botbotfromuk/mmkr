"""Contract annotations for LLM-powered domain verification.

Contract is a SchemaAxisCapability — it lives alongside Min, Max, Unique
in Annotated[...] on fields. EntityContract is a SchemaCapability — it
attaches to the entity via @schema_meta.

Both participate in the llmify fold: Contract implements compile_llmify
(contributes a system Message per field), EntityContract implements
compile_llmify_entity (contributes a system Message for the entity).

Both are invisible to production compilation (no compile_pydantic, etc).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from funcai.core.message import system

from emergent.wire.axis.schema._universal import (
    SchemaAxisCapability,
    SchemaCapability,
)

from llmify.context import LlmifyContext, LlmifyEntityContext


@dataclass(frozen=True, slots=True)
class Contract(SchemaAxisCapability):
    """Natural language specification for a field.

    Participates in the llmify fold via compile_llmify — contributes a
    system Message with the contract text for the LLM verifier.

    Usage::

        @dataclass
        class Sensor:
            temp: Annotated[float, Min(-40), Max(125),
                            Contract("Celsius temperature from industrial sensor. "
                                     "Values outside range indicate malfunction.")]
    """

    text: str

    def compile_llmify(self, ctx: LlmifyContext) -> LlmifyContext:
        return replace(ctx, messages=(*ctx.messages,
            system(text=f'Field "{ctx.field_name}" contract: "{self.text}"')
        ))


@dataclass(frozen=True, slots=True)
class EntityContract(SchemaCapability):
    """Natural language specification for an entity.

    Attached via @schema_meta, participates in the llmify entity fold
    via compile_llmify_entity — contributes a system Message.

    Usage::

        @schema_meta(EntityContract(
            "Industrial sensor monitoring. Readings outside operating range "
            "indicate hardware malfunction and MUST be recorded, not rejected."
        ))
        @dataclass
        class Sensor: ...
    """

    text: str

    def compile_llmify_entity(self, ctx: LlmifyEntityContext) -> LlmifyEntityContext:
        return replace(ctx, messages=(*ctx.messages,
            system(text=f'Entity contract: "{self.text}"')
        ))
