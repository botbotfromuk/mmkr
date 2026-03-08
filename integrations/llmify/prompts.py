"""Dialogue builders — turn emergent's frozen IR into funcai Dialogues.

Each function reads emergent's schema data (dicts, field info, capabilities)
and renders it into a structured prompt for the LLM. The LLM responds with
Pydantic-parseable JSON via funcai's structured output.

The LLM sees:
- Capability reprs directly (Min(value=0) is self-explanatory)
- Fold messages — Contract text, EntityContract text, custom cap hints —
  all contributed by capabilities via compile_llmify / compile_llmify_entity
- Source code of custom capabilities via inspect.getsource()
- Derivation info (operations, effects, triggers) from derivelib
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Sequence

from funcai.core.dialogue import Dialogue
from funcai.core.message import system, user

if TYPE_CHECKING:
    from funcai.core.message import Message

    from llmify.programs import DerivationInfo
    from llmify.results import ConsistencyIssue, MethodLogicIssue


# ═══════════════════════════════════════════════════════════════════════════════
# Static intro — capabilities are self-describing via repr
# ═══════════════════════════════════════════════════════════════════════════════


_EMERGENT_INTRO = """\
Entities use `Annotated[type, Cap1, Cap2, ...]` to attach capabilities. \
Capabilities are frozen dataclasses — their repr shows what they enforce. \
Min/Max REJECT out-of-range values. ReadOnly = server-set, not by client. \
Immutable = settable on create, immutable after.

When suggesting fixes, provide a `suggested_capability` with a one-liner \
`stub` showing a frozen dataclass definition."""


# ═══════════════════════════════════════════════════════════════════════════════
# Formatters
# ═══════════════════════════════════════════════════════════════════════════════


def _format_field_block(
    name: str,
    type_name: str,
    caps: Sequence[str],
) -> str:
    lines = [f"  {name} ({type_name}):"]
    if caps:
        lines.append(f"    capabilities: {', '.join(caps)}")
    return "\n".join(lines)


def _format_schema(
    entity_name: str,
    schema: Mapping[str, object],
    caps_repr: Mapping[str, Sequence[str]],
) -> str:
    """Format schema — field types + capabilities only.

    Contract text and entity contract come from fold messages,
    merged into the system prompt separately.
    """
    lines = [f"Entity: {entity_name}", ""]

    fields = schema.get("fields", [])
    assert isinstance(fields, list)
    for field_dict in fields:
        assert isinstance(field_dict, dict)
        name = str(field_dict.get("name", ""))
        type_name = str(field_dict.get("type", ""))
        caps = caps_repr.get(name, ())
        lines.append(_format_field_block(name, type_name, caps))

    return "\n".join(lines)


def _format_custom_caps(sources: Mapping[str, str] | None) -> str:
    """Format custom capability source code for the LLM."""
    if not sources:
        return ""
    parts = ["Custom capabilities (source code):\n"]
    for _name, source in sources.items():
        parts.append(source.rstrip())
        parts.append("")
    return "\n".join(parts)


def _format_fold_messages(fold_messages: Sequence[Message]) -> str:
    """Format fold messages — contracts, hints, entity contracts.

    All contributed by capabilities via compile_llmify / compile_llmify_entity.
    """
    if not fold_messages:
        return ""
    parts: list[str] = []
    for msg in fold_messages:
        text = msg.text.unwrap_or("")
        if text:
            parts.append(text)
    return "\n".join(parts)


def _format_derivation(derivation: DerivationInfo | None) -> str:
    """Format derivation info — operations, effects, triggers, source code."""
    if derivation is None:
        return ""

    p_word = "pattern" if derivation.pattern_count == 1 else "patterns"
    parts = [f"Derivation: {derivation.pattern_count} {p_word}\n"]

    for i, pattern in enumerate(derivation.patterns, 1):
        parts.append(f"Pattern #{i}: {pattern.pattern_type}")
        parts.append("  Operations:")
        for op in pattern.operations:
            effects_str = ", ".join(op.effects) if op.effects else ""
            effects_part = f"\n      effects: {effects_str}" if effects_str else ""
            input_str = ""
            if op.input_fields:
                input_str = f"\n      input: {', '.join(op.input_fields)}"
            response_str = ""
            if op.response_type:
                response_str = f"\n      response: {op.response_type}"
            parts.append(f"    {op.name}: {op.trigger}{effects_part}{input_str}{response_str}")
        parts.append("")

    # Custom DerivationEffect source code
    if derivation.effect_sources:
        parts.append("Derivation effects (source code):\n")
        for _name, source in derivation.effect_sources.items():
            parts.append(source.rstrip())
            parts.append("")

    # Method source code (methods pattern)
    if derivation.method_sources:
        parts.append("Method implementations (source code):\n")
        for _name, source in derivation.method_sources.items():
            parts.append(source.rstrip())
            parts.append("")

    return "\n".join(parts)


def _format_existing_issues(
    consistency_issues: Sequence[ConsistencyIssue] = (),
    method_issues: Sequence[MethodLogicIssue] = (),
) -> str:
    """Format previously-found issues so the LLM avoids repeating them."""
    if not consistency_issues and not method_issues:
        return ""
    lines = [
        "Previously identified issues (do NOT repeat these — find NEW issues only):\n",
    ]
    for issue in consistency_issues:
        lines.append(f"- [{issue.severity.upper()}] {issue.field}: {issue.message}")
    for issue in method_issues:
        lines.append(f"- [{issue.severity.upper()}] {issue.method}: {issue.message}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# System prompt builders
# ═══════════════════════════════════════════════════════════════════════════════


def _build_system(
    role_description: str,
    check_instructions: str,
    fold_messages: Sequence[Message] = (),
    custom_cap_sources: Mapping[str, str] | None = None,
    entity_source: str | None = None,
) -> str:
    """Build a system prompt from parts.

    Fold messages (contracts, hints, entity contracts) are merged
    into the system prompt between the intro and instructions.
    """
    parts = [role_description, "", _EMERGENT_INTRO]

    # Entity source code — the raw @dataclass definition
    if entity_source:
        parts.append("")
        parts.append("Entity definition (source code):\n")
        parts.append(entity_source.rstrip())

    # Fold messages — contracts, entity contracts, custom cap hints
    fold_text = _format_fold_messages(fold_messages)
    if fold_text:
        parts.append("")
        parts.append(fold_text)

    # Custom cap source code
    custom = _format_custom_caps(custom_cap_sources)
    if custom:
        parts.append("")
        parts.append(custom)

    parts.append("")
    parts.append(check_instructions)

    return "\n".join(parts)


_CONSISTENCY_ROLE = """\
You are a domain verification assistant for the emergent framework. \
You check whether natural language contracts on fields are consistent \
with the actual capabilities (constraints) attached to those fields."""

_CONSISTENCY_INSTRUCTIONS = """\
For each field that has a contract, check:
1. Does the contract text match what the capabilities enforce?
2. Are there contradictions between what the contract says and what the \
capabilities do?
3. Are there gaps — things the contract promises but capabilities don't enforce?

Report issues with severity "error" (contradiction), "warning" (gap/ambiguity), \
or "info" (minor observation). Include the exact contract excerpt and capability \
that conflict.

When a fix requires a new or modified capability, include `suggested_capability` \
with a one-liner `stub` (frozen dataclass definition) and `rationale`.

Provide a brief summary at the end."""

_ADEQUACY_ROLE = """\
You are a domain expert reviewer for the emergent framework. For the given \
field in the given domain, check whether the constraint values are adequate."""

_ADEQUACY_INSTRUCTIONS = """\
Consider:
1. Are numeric bounds reasonable for this domain?
2. Are string length limits appropriate?
3. Are the allowed values (OneOf) complete?
4. Could the constraints be too tight (rejecting valid data) or too loose \
(accepting invalid data)?

Be specific. If a constraint seems wrong, suggest what it should be. \
When a new capability would help, include it in `suggested_capabilities` \
with a one-liner `stub` (frozen dataclass definition) and `rationale`."""

_INVARIANTS_ROLE = """\
You are a test engineer for the emergent framework. Given an entity with \
contracts and capabilities, generate property-based test invariants."""

_INVARIANTS_INSTRUCTIONS = """\
Each invariant should:
1. Have a clear, testable name (snake_case)
2. Describe a property that should ALWAYS hold
3. Reference specific fields
4. Be expressible as a Hypothesis-style property test

Focus on invariants that arise from the INTERACTION between contracts and \
capabilities — things that neither alone would catch."""

_TESTS_ROLE = """\
You are a test engineer for the emergent framework. Given an entity with \
contracts and capabilities, generate concrete test cases."""

_TESTS_INSTRUCTIONS = """\
Each test should:
1. Have a clear name (test_snake_case)
2. Describe the setup (what to create/configure)
3. Describe the assertion (what to check)
4. Target boundary conditions and contract edge cases

Focus on tests that verify the CONTRACT is correctly implemented by the \
capabilities."""

_METHOD_LOGIC_ROLE = """\
You are a code logic auditor for the emergent framework. \
You trace through method implementations line-by-line and verify \
that the computed results match the entity's contract and domain rules."""

_METHOD_LOGIC_INSTRUCTIONS = """\
For each method that has non-trivial logic (not just `return Ok(1)`):
1. TRACE the computation step-by-step with concrete values.
2. COMPARE the computed result to what the contract says should happen.
3. FLAG any discrepancy: wrong formula, missing step, double-counting, \
off-by-one, wrong variable, incorrect operator.

Focus on:
- Arithmetic errors (double-charging, wrong base amount, missing discount)
- Logic errors (wrong conditional, missing edge case, inverted comparison)
- Domain violations (computation contradicts the contract's stated rules)
- Side-effect gaps (method modifies/returns something contract doesn't expect)

Use concrete numeric examples in your trace. For example: \
"If upgrading from starter ($9.99) to pro ($29.99) on day 15 of a 30-day month: \
expected charge = (2999 - 999) * 15/30 = 1000 cents, but code computes 1000 + 2999 = 3999."

Report issues with severity "error" (incorrect result), "warning" (edge case not handled), \
or "info" (suspicious but not provably wrong).

Skip methods that are stubs (just `return Ok(1)` or similar). \
Only audit methods with real computation logic."""

_FULL_CHECK_ROLE = """\
You are a domain verification assistant for the emergent framework. \
Given an entity with natural language contracts and typed capabilities, \
perform a full verification."""

_FULL_CHECK_INSTRUCTIONS = """\
1. CONSISTENCY: Check each field's contract against its capabilities. \
Find contradictions, gaps, ambiguities. When a fix requires a new capability, \
suggest it with a one-liner stub.
2. ADEQUACY: Check if constraint values are reasonable for the domain. \
Suggest new capabilities where needed.
3. INVARIANTS: Suggest property-based test invariants (Hypothesis-style).
4. TESTS: Suggest concrete test cases for boundary conditions.

Be specific. Reference exact fields, capabilities, and contract excerpts."""


# ═══════════════════════════════════════════════════════════════════════════════
# Dialogue builders
# ═══════════════════════════════════════════════════════════════════════════════


def build_consistency_dialogue(
    entity_name: str,
    schema: Mapping[str, object],
    caps_repr: Mapping[str, Sequence[str]],
    fold_messages: Sequence[Message] = (),
    fold_tools: Sequence[object] = (),
    custom_cap_sources: Mapping[str, str] | None = None,
    derivation: DerivationInfo | None = None,
    entity_source: str | None = None,
) -> Dialogue:
    schema_text = _format_schema(entity_name, schema, caps_repr)
    derivation_text = _format_derivation(derivation)
    if derivation_text:
        schema_text = f"{schema_text}\n\n{derivation_text}"

    return Dialogue([
        system(text=_build_system(
            _CONSISTENCY_ROLE, _CONSISTENCY_INSTRUCTIONS,
            fold_messages, custom_cap_sources, entity_source,
        )),
        user(text=schema_text),
    ])


def build_field_adequacy_dialogue(
    field_name: str,
    type_name: str,
    caps: Sequence[str],
    fold_messages: Sequence[Message] = (),
    domain: str = "",
    custom_cap_sources: Mapping[str, str] | None = None,
    entity_source: str | None = None,
) -> Dialogue:
    field_text = (
        f"Domain: {domain}\n\n"
        f"Field: {field_name} ({type_name})\n"
        f"Capabilities: {', '.join(caps)}"
    )
    return Dialogue([
        system(text=_build_system(
            _ADEQUACY_ROLE, _ADEQUACY_INSTRUCTIONS,
            fold_messages, custom_cap_sources, entity_source,
        )),
        user(text=field_text),
    ])


def build_invariants_dialogue(
    entity_name: str,
    schema: Mapping[str, object],
    caps_repr: Mapping[str, Sequence[str]],
    fold_messages: Sequence[Message] = (),
    fold_tools: Sequence[object] = (),
    count: int = 5,
    custom_cap_sources: Mapping[str, str] | None = None,
    derivation: DerivationInfo | None = None,
    entity_source: str | None = None,
) -> Dialogue:
    schema_text = _format_schema(entity_name, schema, caps_repr)
    derivation_text = _format_derivation(derivation)
    if derivation_text:
        schema_text = f"{schema_text}\n\n{derivation_text}"

    return Dialogue([
        system(text=_build_system(
            _INVARIANTS_ROLE, _INVARIANTS_INSTRUCTIONS,
            fold_messages, custom_cap_sources, entity_source,
        )),
        user(text=f"{schema_text}\n\nGenerate {count} invariants."),
    ])


def build_tests_dialogue(
    entity_name: str,
    schema: Mapping[str, object],
    caps_repr: Mapping[str, Sequence[str]],
    fold_messages: Sequence[Message] = (),
    fold_tools: Sequence[object] = (),
    count: int = 5,
    custom_cap_sources: Mapping[str, str] | None = None,
    derivation: DerivationInfo | None = None,
    entity_source: str | None = None,
) -> Dialogue:
    schema_text = _format_schema(entity_name, schema, caps_repr)
    derivation_text = _format_derivation(derivation)
    if derivation_text:
        schema_text = f"{schema_text}\n\n{derivation_text}"

    return Dialogue([
        system(text=_build_system(
            _TESTS_ROLE, _TESTS_INSTRUCTIONS,
            fold_messages, custom_cap_sources, entity_source,
        )),
        user(text=f"{schema_text}\n\nGenerate {count} test cases."),
    ])


def build_method_logic_audit_dialogue(
    entity_name: str,
    schema: Mapping[str, object],
    caps_repr: Mapping[str, Sequence[str]],
    fold_messages: Sequence[Message] = (),
    fold_tools: Sequence[object] = (),
    custom_cap_sources: Mapping[str, str] | None = None,
    derivation: DerivationInfo | None = None,
    entity_source: str | None = None,
    existing_method_issues: Sequence[MethodLogicIssue] = (),
) -> Dialogue:
    schema_text = _format_schema(entity_name, schema, caps_repr)
    derivation_text = _format_derivation(derivation)
    if derivation_text:
        schema_text = f"{schema_text}\n\n{derivation_text}"

    existing = _format_existing_issues(method_issues=existing_method_issues)
    task = "Audit all method implementations with non-trivial logic."
    if existing:
        task = f"{task}\n\n{existing}"

    return Dialogue([
        system(text=_build_system(
            _METHOD_LOGIC_ROLE, _METHOD_LOGIC_INSTRUCTIONS,
            fold_messages, custom_cap_sources, entity_source,
        )),
        user(text=f"{schema_text}\n\n{task}"),
    ])


def build_full_check_dialogue(
    entity_name: str,
    schema: Mapping[str, object],
    caps_repr: Mapping[str, Sequence[str]],
    fold_messages: Sequence[Message] = (),
    fold_tools: Sequence[object] = (),
    domain: str | None = None,
    invariant_count: int = 3,
    test_count: int = 5,
    custom_cap_sources: Mapping[str, str] | None = None,
    derivation: DerivationInfo | None = None,
    entity_source: str | None = None,
    existing_consistency_issues: Sequence[ConsistencyIssue] = (),
) -> Dialogue:
    schema_text = _format_schema(entity_name, schema, caps_repr)
    if domain:
        schema_text = f"Domain: {domain}\n\n{schema_text}"
    derivation_text = _format_derivation(derivation)
    if derivation_text:
        schema_text = f"{schema_text}\n\n{derivation_text}"
    instructions = (
        f"\n\nGenerate up to {invariant_count} invariants "
        f"and {test_count} test cases."
    )
    existing = _format_existing_issues(consistency_issues=existing_consistency_issues)
    if existing:
        instructions = f"{instructions}\n\n{existing}"
    return Dialogue([
        system(text=_build_system(
            _FULL_CHECK_ROLE, _FULL_CHECK_INSTRUCTIONS,
            fold_messages, custom_cap_sources, entity_source,
        )),
        user(text=schema_text + instructions),
    ])
