"""AI[T] program builders — the core of llmify.

Each function reads emergent's frozen IR and builds a funcai AI[T] program.
Programs are frozen ASTs — not executed until .compile(provider) is called.
They can be analyzed, composed, timed out, and retried.

The fold collects funcai Messages and Tools from all capabilities that
implement compile_llmify (field-level) or compile_llmify_entity (entity-level).
Contract text, custom cap hints, and future tools all flow through the fold.

Usage::

    from llmify import contract_check
    from funcai.std.dsl import analyze

    program = contract_check(Sensor, domain="industrial monitoring")
    print(analyze(program.op).pretty())  # inspect before execution

    result = await program.compile(provider)  # execute with LLM
    for issue in result.consistency.issues:
        print(f"[{issue.severity}] {issue.field}: {issue.message}")
"""

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from funcai.agents.tool import Tool
from funcai.core.message import Message
from funcai.std.dsl import AI

from emergent.wire.axis.schema import (
    SchemaAxisCapability,
    inspect_type,
    schema_dict,
)

from emergent.wire.compile import Axes
from emergent.wire.compile._phase import SchemaCompiler

from llmify.context import LLMIFY_ENTITY_FOLD, LLMIFY_PHASE
from llmify.prompts import (
    build_consistency_dialogue,
    build_field_adequacy_dialogue,
    build_full_check_dialogue,
    build_invariants_dialogue,
    build_method_logic_audit_dialogue,
    build_tests_dialogue,
)

# Re-export for use in accumulate() — avoids circular import issues
# since prompts.py TYPE_CHECKs programs.py
from llmify.results import (
    AccumulatedResult,
    AdequacyResult,
    ConsistencyIssue,
    ConsistencyResult,
    ContractCheckResult,
    InvariantSpec,
    MethodLogicAuditResult,
    MethodLogicIssue,
    TestSpec,
)

if TYPE_CHECKING:
    from funcai.agents.tool import Tool
    from funcai.core.message import Message


# ═══════════════════════════════════════════════════════════════════════════════
# Derivation data classes
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class OperationInfo:
    """One operation's info for LLM prompts."""

    name: str
    trigger: str  # "GET /articles" or "POST /articles/{id}/submit"
    effects: tuple[str, ...]  # ("HoldsFunds(hold_duration_hours=168)",)
    input_fields: tuple[str, ...]  # ("amount: int", "currency: str")
    response_type: str = ""  # "EntityResponse", "ListResponse", "OkResponse"


@dataclass(frozen=True, slots=True)
class PatternInfo:
    """One pattern's info for LLM prompts."""

    pattern_type: str
    operations: tuple[OperationInfo, ...]


@dataclass(frozen=True, slots=True)
class DerivationInfo:
    """Extracted derivation context for LLM prompts."""

    pattern_count: int
    patterns: tuple[PatternInfo, ...]
    effect_sources: dict[str, str] = field(default_factory=dict)
    method_sources: dict[str, str] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
# Fold output — collected funcai primitives
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class FoldOutput:
    """Collected funcai primitives from the llmify fold.

    Messages come from Contract (field-level), EntityContract (entity-level),
    and any custom capability implementing compile_llmify.
    Tools come from capabilities that contribute funcai Tools (future).
    """

    messages: tuple[Message, ...]
    tools: tuple[Tool, ...]


def _collect_fold_output(entity: type) -> FoldOutput:
    """Fold entity through LLMIFY_PHASE, collect all Messages + Tools.

    Entity-level capabilities (EntityContract via @schema_meta) contribute
    first, then field-level capabilities (Contract, custom caps with
    compile_llmify). Messages are deduplicated by text content.
    """
    axes = Axes.default()
    compiler = SchemaCompiler(phases=(LLMIFY_PHASE,))
    ec = compiler.compile(entity, axes)

    messages: list[Message] = []
    tools: list[Tool] = []

    # Entity-level (EntityContract, etc.)
    entity_ctx = ec.get(LLMIFY_ENTITY_FOLD)
    if entity_ctx is not None:
        messages.extend(entity_ctx.messages)
        tools.extend(entity_ctx.tools)

    # Field-level (Contract, custom caps with compile_llmify)
    seen_messages: set[str] = set()
    for fc in ec:
        ctx = fc[LLMIFY_PHASE]
        for msg in ctx.messages:
            text = msg.text.unwrap_or("")
            if text and text not in seen_messages:
                seen_messages.add(text)
                messages.append(msg)
        tools.extend(ctx.tools)

    return FoldOutput(tuple(messages), tuple(tools))


# ═══════════════════════════════════════════════════════════════════════════════
# Extraction helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _extract_entity_source(entity: type) -> str | None:
    """Extract entity class source code via inspect.getsource()."""
    try:
        return inspect.getsource(entity)
    except (OSError, TypeError):
        return None


def _extract_caps_repr(entity: type) -> dict[str, list[str]]:
    """Extract human-readable capability reprs per field."""
    fields = inspect_type(entity)
    result: dict[str, list[str]] = {}
    for name, info in fields.items():
        result[name] = [repr(c) for c in info.capabilities]
    return result


def _is_builtin_cap(cap: SchemaAxisCapability) -> bool:
    """Check if capability is from emergent's built-in modules."""
    module = type(cap).__module__
    return module.startswith("emergent.")


def _collect_custom_cap_sources(entity: type) -> dict[str, str]:
    """Collect source code for custom (non-builtin) capability types.

    Uses inspect.getsource() on capability classes. Deduplicates by type.
    Skips Contract/EntityContract (our own annotations).
    Falls back to repr() if source is unavailable.
    """
    fields = inspect_type(entity)
    seen: set[type] = set()
    sources: dict[str, str] = {}

    for info in fields.values():
        for cap in info.capabilities:
            cap_type = type(cap)
            if cap_type in seen:
                continue
            seen.add(cap_type)

            # Skip builtins and our own annotations
            if _is_builtin_cap(cap):
                continue
            if cap_type.__module__.startswith("llmify."):
                continue

            try:
                source = inspect.getsource(cap_type)
                sources[cap_type.__name__] = source
            except (OSError, TypeError):
                sources[cap_type.__name__] = repr(cap)

    return sources


def _collect_effect_sources(
    effects: Sequence[object],
    seen: set[type],
) -> dict[str, str]:
    """Collect source code for custom DerivationEffect subclasses.

    Skips builtins (module starts with 'derivelib.') and already-seen types.
    Falls back to repr() if source is unavailable.
    """
    sources: dict[str, str] = {}
    for effect in effects:
        eff_type = type(effect)
        if eff_type in seen:
            continue
        seen.add(eff_type)

        # Skip builtins
        if eff_type.__module__.startswith("derivelib."):
            continue

        try:
            sources[eff_type.__name__] = inspect.getsource(eff_type)
        except (OSError, TypeError):
            sources[eff_type.__name__] = repr(effect)

    return sources


def _collect_method_source(service: type, method_name: str) -> str | None:
    """Collect source code for a method on a service class.

    Returns None if source is unavailable.
    """
    try:
        method_fn = getattr(service, method_name)
        return inspect.getsource(method_fn)
    except (OSError, TypeError, AttributeError):
        return None


def _collect_entity_methods(entity: type) -> dict[str, str]:
    """Scan entity class for all non-dunder methods and collect source code.

    Picks up classmethods, staticmethods, and regular methods.
    Skips inherited methods (only methods defined on this class).
    """
    sources: dict[str, str] = {}
    for name in list(vars(entity)):
        if name.startswith("_"):
            continue
        attr = inspect.getattr_static(entity, name)
        if isinstance(attr, (classmethod, staticmethod)):
            fn = attr.__func__
        elif callable(attr):
            fn = attr
        else:
            continue
        try:
            sources[name] = inspect.getsource(fn)
        except (OSError, TypeError):
            pass
    return sources


def _extract_derivation(entity: type) -> DerivationInfo | None:
    """Extract derivation info if entity has @derive and derivelib is available.

    Returns None if derivelib is not installed or entity has no patterns.

    Handles two derivation paths:
    - Dialect (DeriveOp) → specs on SurfaceCtx (effects, input_fields, response_spec)
    - Methods (@post/@get + @op) → ExposeMethod steps (effects, trigger, method sig)
    """
    try:
        from derivelib._derive import get_patterns
        from derivelib._explain import _effect_repr, _trigger_short
        from derivelib._fold import fold_derive
    except ImportError:
        return None

    patterns = get_patterns(entity)
    if not patterns:
        return None

    entity_cls: type[object] = entity
    pattern_infos: list[PatternInfo] = []
    effect_seen: set[type] = set()
    all_effect_sources: dict[str, str] = {}
    all_method_sources: dict[str, str] = {}

    for pattern in patterns:
        steps = pattern.compile(entity)
        ctx = fold_derive(steps, entity_cls)
        surface = ctx.surface

        ops: list[OperationInfo] = []

        # Path 1: Dialect — specs carry everything
        for spec in surface.specs:
            trigger_str = _trigger_short(spec.trigger)
            effects = tuple(_effect_repr(e) for e in spec.effects)
            input_fields = tuple(
                f"{name}: {t.__name__}" if isinstance(t, type) else f"{name}: {t}"
                for name, t in spec.input_fields.items()
            )
            response_type = type(spec.response_spec).__name__
            ops.append(OperationInfo(
                name=spec.name,
                trigger=trigger_str,
                effects=effects,
                input_fields=input_fields,
                response_type=response_type,
            ))
            # Collect custom effect sources
            all_effect_sources.update(
                _collect_effect_sources(spec.effects, effect_seen),
            )

        # Path 2: Methods — effects live on ExposeMethod steps
        if not ops:
            method_ops, method_srcs = _extract_method_ops(
                steps, _effect_repr, _trigger_short,
            )
            ops.extend(method_ops)
            all_method_sources.update(method_srcs)
            # Collect effect sources from method steps too
            for step in steps:
                step_effects = getattr(step, "effects", ())
                if step_effects:
                    all_effect_sources.update(
                        _collect_effect_sources(step_effects, effect_seen),
                    )

        pattern_infos.append(PatternInfo(
            pattern_type=type(pattern).__name__,
            operations=tuple(ops),
        ))

    # Generic scan: collect any entity methods not already picked up
    # by ExposeMethod extraction (covers @query, @action, @format_card, finish, etc.)
    entity_methods = _collect_entity_methods(entity)
    for name, source in entity_methods.items():
        if name not in all_method_sources:
            all_method_sources[name] = source

    return DerivationInfo(
        pattern_count=len(patterns),
        patterns=tuple(pattern_infos),
        effect_sources=all_effect_sources,
        method_sources=all_method_sources,
    )


def _extract_method_ops(
    steps: tuple[object, ...],
    effect_repr: Callable[[object], str],
    trigger_short: Callable[[object], str],
) -> tuple[list[OperationInfo], dict[str, str]]:
    """Extract OperationInfo and method sources from ExposeMethod steps.

    ExposeMethod carries trigger, effects, and method_name.
    Input fields are read from the method signature via inspect.
    Returns (ops, method_sources) where method_sources maps name → source code.
    """
    try:
        from derivelib.patterns.methods import ExposeMethod
    except ImportError:
        return [], {}

    ops: list[OperationInfo] = []
    method_sources: dict[str, str] = {}

    for step in steps:
        if not isinstance(step, ExposeMethod):
            continue

        effects = tuple(effect_repr(e) for e in step.effects)
        trigger_str = trigger_short(step.trigger)

        # Read input fields from method signature
        method_fn = getattr(step.service, step.method_name)
        from typing import get_type_hints
        try:
            hints = get_type_hints(method_fn)
        except Exception:
            hints = {}
        sig = inspect.signature(method_fn)
        input_fields: list[str] = []
        for param_name in sig.parameters:
            if param_name in ("self", "cls", "return"):
                continue
            t = hints.get(param_name)
            if t is None:
                input_fields.append(param_name)
            elif isinstance(t, type):
                input_fields.append(f"{param_name}: {t.__name__}")
            else:
                input_fields.append(f"{param_name}: {t}")

        # Read @op name if available
        from derivelib.patterns.methods import OP_ENTRIES_ATTR
        fn = method_fn
        if isinstance(inspect.getattr_static(step.service, step.method_name), (classmethod, staticmethod)):
            fn = inspect.getattr_static(step.service, step.method_name).__func__
        op_entry = getattr(fn, OP_ENTRIES_ATTR, None)
        op_name = op_entry.name if op_entry is not None else step.method_name

        ops.append(OperationInfo(
            name=op_name,
            trigger=trigger_str,
            effects=effects,
            input_fields=tuple(input_fields),
        ))

        # Collect method source code
        source = _collect_method_source(step.service, step.method_name)
        if source is not None:
            method_sources[step.method_name] = source

    return ops, method_sources


# ═══════════════════════════════════════════════════════════════════════════════
# Empty defaults
# ═══════════════════════════════════════════════════════════════════════════════


def _empty_consistency() -> ConsistencyResult:
    return ConsistencyResult(issues=[], summary="No contracts found")


def _empty_check() -> ContractCheckResult:
    return ContractCheckResult(
        consistency=_empty_consistency(),
        adequacy=[],
        invariants=[],
        tests=[],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Program builders
# ═══════════════════════════════════════════════════════════════════════════════


def contract_check(
    entity: type,
    *,
    domain: str | None = None,
    invariant_count: int = 3,
    test_count: int = 5,
    timeout_seconds: float = 60.0,
) -> AI[ContractCheckResult]:
    """Build a full contract verification program.

    Reads entity's frozen IR, builds a Dialogue, returns AI[ContractCheckResult].
    Nothing is executed until .compile(provider) is called.
    """
    fold = _collect_fold_output(entity)

    if not fold.messages:
        return AI.pure(_empty_check())

    schema = schema_dict(entity)
    caps_repr = _extract_caps_repr(entity)
    custom_cap_sources = _collect_custom_cap_sources(entity)
    derivation = _extract_derivation(entity)
    entity_source = _extract_entity_source(entity)

    dialogue = build_full_check_dialogue(
        entity_name=entity.__name__,
        schema=schema,
        caps_repr=caps_repr,
        fold_messages=fold.messages,
        fold_tools=fold.tools,
        domain=domain,
        invariant_count=invariant_count,
        test_count=test_count,
        custom_cap_sources=custom_cap_sources,
        derivation=derivation,
        entity_source=entity_source,
    )

    return (
        AI.ask(dialogue, ContractCheckResult)
        .timeout(timeout_seconds)
        .fallback(AI.pure(_empty_check()))
    )


def check_consistency(
    entity: type,
    *,
    timeout_seconds: float = 30.0,
) -> AI[ConsistencyResult]:
    """Check contract-vs-capabilities consistency only."""
    fold = _collect_fold_output(entity)

    if not fold.messages:
        return AI.pure(_empty_consistency())

    schema = schema_dict(entity)
    caps_repr = _extract_caps_repr(entity)
    custom_cap_sources = _collect_custom_cap_sources(entity)
    derivation = _extract_derivation(entity)
    entity_source = _extract_entity_source(entity)

    dialogue = build_consistency_dialogue(
        entity_name=entity.__name__,
        schema=schema,
        caps_repr=caps_repr,
        fold_messages=fold.messages,
        fold_tools=fold.tools,
        custom_cap_sources=custom_cap_sources,
        derivation=derivation,
        entity_source=entity_source,
    )

    return (
        AI.ask(dialogue, ConsistencyResult)
        .timeout(timeout_seconds)
        .fallback(AI.pure(_empty_consistency()))
    )


def check_adequacy(
    entity: type,
    domain: str,
    *,
    timeout_seconds: float = 30.0,
) -> AI[list[AdequacyResult]]:
    """Check domain adequacy per field — runs in parallel."""
    fold = _collect_fold_output(entity)

    if not fold.messages:
        return AI.pure([])

    fields = inspect_type(entity)
    custom_cap_sources = _collect_custom_cap_sources(entity)
    entity_source = _extract_entity_source(entity)
    field_programs: list[AI[AdequacyResult]] = []
    for name, info in fields.items():
        # Collect fold messages for this specific field
        field_fold_messages = [
            msg for msg in fold.messages
            if name in msg.text.unwrap_or("")
        ]
        if not field_fold_messages:
            continue

        caps = [repr(c) for c in info.capabilities]
        type_name = info.base_type.__name__
        dialogue = build_field_adequacy_dialogue(
            field_name=name,
            type_name=type_name,
            caps=caps,
            fold_messages=tuple(field_fold_messages),
            domain=domain,
            custom_cap_sources=custom_cap_sources,
            entity_source=entity_source,
        )
        field_programs.append(
            AI.ask(dialogue, AdequacyResult).timeout(timeout_seconds),
        )

    if not field_programs:
        return AI.pure([])

    return AI.parallel(*field_programs)


def suggest_invariants(
    entity: type,
    *,
    count: int = 5,
    timeout_seconds: float = 30.0,
) -> AI[list[InvariantSpec]]:
    """Suggest property-based test invariants from contracts."""
    fold = _collect_fold_output(entity)

    if not fold.messages:
        return AI.pure([])

    schema = schema_dict(entity)
    caps_repr = _extract_caps_repr(entity)
    custom_cap_sources = _collect_custom_cap_sources(entity)
    derivation = _extract_derivation(entity)
    entity_source = _extract_entity_source(entity)

    dialogue = build_invariants_dialogue(
        entity_name=entity.__name__,
        schema=schema,
        caps_repr=caps_repr,
        fold_messages=fold.messages,
        fold_tools=fold.tools,
        count=count,
        custom_cap_sources=custom_cap_sources,
        derivation=derivation,
        entity_source=entity_source,
    )

    return (
        AI.ask(dialogue, _InvariantList)
        .map(lambda r: r.invariants)
        .timeout(timeout_seconds)
        .fallback(AI.pure([]))
    )


def suggest_tests(
    entity: type,
    *,
    count: int = 5,
    timeout_seconds: float = 30.0,
) -> AI[list[TestSpec]]:
    """Suggest concrete test cases from contracts."""
    fold = _collect_fold_output(entity)

    if not fold.messages:
        return AI.pure([])

    schema = schema_dict(entity)
    caps_repr = _extract_caps_repr(entity)
    custom_cap_sources = _collect_custom_cap_sources(entity)
    derivation = _extract_derivation(entity)
    entity_source = _extract_entity_source(entity)

    dialogue = build_tests_dialogue(
        entity_name=entity.__name__,
        schema=schema,
        caps_repr=caps_repr,
        fold_messages=fold.messages,
        fold_tools=fold.tools,
        count=count,
        custom_cap_sources=custom_cap_sources,
        derivation=derivation,
        entity_source=entity_source,
    )

    return (
        AI.ask(dialogue, _TestList)
        .map(lambda r: r.tests)
        .timeout(timeout_seconds)
        .fallback(AI.pure([]))
    )


def audit_method_logic(
    entity: type,
    *,
    timeout_seconds: float = 60.0,
) -> AI[MethodLogicAuditResult]:
    """Audit method implementation logic against contracts.

    Traces through each method's computation and verifies the result
    matches the contract's stated behavior. Only useful for entities
    with methods pattern that have non-trivial logic.
    """
    fold = _collect_fold_output(entity)

    if not fold.messages:
        return AI.pure(MethodLogicAuditResult(issues=[], summary="No contracts found"))

    derivation = _extract_derivation(entity)
    if derivation is None or not derivation.method_sources:
        return AI.pure(MethodLogicAuditResult(
            issues=[], summary="No method implementations to audit",
        ))

    schema = schema_dict(entity)
    caps_repr = _extract_caps_repr(entity)
    custom_cap_sources = _collect_custom_cap_sources(entity)
    entity_source = _extract_entity_source(entity)

    dialogue = build_method_logic_audit_dialogue(
        entity_name=entity.__name__,
        schema=schema,
        caps_repr=caps_repr,
        fold_messages=fold.messages,
        fold_tools=fold.tools,
        custom_cap_sources=custom_cap_sources,
        derivation=derivation,
        entity_source=entity_source,
    )

    return (
        AI.ask(dialogue, MethodLogicAuditResult)
        .timeout(timeout_seconds)
        .fallback(AI.pure(MethodLogicAuditResult(issues=[], summary="Audit timed out")))
    )


def _issue_key(field: str, message: str) -> str:
    """Fingerprint for deduplicating issues."""
    return f"{field.lower().strip()}::{message[:80].lower().strip()}"


async def accumulate(
    entity: type,
    provider: object,
    *,
    passes: int = 3,
    domain: str | None = None,
    timeout_seconds: float = 90.0,
) -> AccumulatedResult:
    """Run multiple verification passes, accumulating unique issues.

    Each pass tells the LLM which issues were already found, so it focuses
    on discovering NEW ones. Non-determinism means each pass may find
    different bugs. Returns merged, deduplicated results.
    """
    from kungfu import Error, Ok

    fold = _collect_fold_output(entity)
    if not fold.messages:
        return AccumulatedResult(
            consistency_issues=[], method_issues=[],
            passes_run=0, issues_per_pass=[],
        )

    schema = schema_dict(entity)
    caps_repr = _extract_caps_repr(entity)
    custom_cap_sources = _collect_custom_cap_sources(entity)
    derivation = _extract_derivation(entity)
    entity_source = _extract_entity_source(entity)
    has_methods = derivation is not None and bool(derivation.method_sources)

    all_consistency: list[ConsistencyIssue] = []
    all_method: list[MethodLogicIssue] = []
    seen_keys: set[str] = set()
    issues_per_pass: list[int] = []

    for pass_idx in range(passes):
        new_in_pass = 0

        # --- Consistency pass ---
        dialogue = build_full_check_dialogue(
            entity_name=entity.__name__,
            schema=schema,
            caps_repr=caps_repr,
            fold_messages=fold.messages,
            fold_tools=fold.tools,
            domain=domain,
            invariant_count=3,
            test_count=3,
            custom_cap_sources=custom_cap_sources,
            derivation=derivation,
            entity_source=entity_source,
            existing_consistency_issues=all_consistency,
        )

        res = await dialogue.interpret(provider, ContractCheckResult)
        match res:
            case Ok(result):
                for issue in result.consistency.issues:
                    key = _issue_key(issue.field, issue.message)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_consistency.append(issue)
                        new_in_pass += 1
            case Error():
                pass

        # --- Method audit pass ---
        if has_methods:
            audit_dialogue = build_method_logic_audit_dialogue(
                entity_name=entity.__name__,
                schema=schema,
                caps_repr=caps_repr,
                fold_messages=fold.messages,
                fold_tools=fold.tools,
                custom_cap_sources=custom_cap_sources,
                derivation=derivation,
                entity_source=entity_source,
                existing_method_issues=all_method,
            )

            audit_res = await audit_dialogue.interpret(provider, MethodLogicAuditResult)
            match audit_res:
                case Ok(audit):
                    for issue in audit.issues:
                        key = _issue_key(issue.method, issue.message)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            all_method.append(issue)
                            new_in_pass += 1
                case Error():
                    pass

        issues_per_pass.append(new_in_pass)
        print(f"  pass {pass_idx + 1}/{passes}: +{new_in_pass} new issues "
              f"(total: {len(all_consistency)} consistency, {len(all_method)} method)")

        if new_in_pass == 0 and pass_idx > 0:
            print("  converged — no new issues found, stopping early")
            break

    return AccumulatedResult(
        consistency_issues=all_consistency,
        method_issues=all_method,
        passes_run=len(issues_per_pass),
        issues_per_pass=issues_per_pass,
    )


# Wrapper models for list-returning programs (LLM needs a top-level object)
from pydantic import BaseModel as _BaseModel


class _InvariantList(_BaseModel):
    invariants: list[InvariantSpec]


class _TestList(_BaseModel):
    tests: list[TestSpec]
