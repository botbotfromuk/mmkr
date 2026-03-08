"""Life — the eternal fold.

Life = list[LifeCapability]. Each tick folds capabilities into LifeContext,
sends one LLM conversation, persists state. Pure FP, no globals.

Key capabilities (all LifeCapability):
  PersistentMemory    — cross-tick memory (save/load/search)
  CapabilityEvolver   — evolve new capabilities at runtime
  Knowledge           — inject text context
  Seed                — author's idea for this tick
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from pathlib import Path
from collections.abc import Mapping
from typing import Generic, Never, TypeVar

from funcai.agents.tool import Tool, tool
from funcai.core.message import Message, system, user
from kungfu import Ok, Some

from llmify import ClaudeProvider

from emergent.wire.axis.query import (
    Field, ILike, MemoryRelationalProvider, Or, relational,
)
from emergent.wire.axis.storage import FileStorage, KV

from mmkr.state import (
    AgentState,
    CognitiveContext,
    EvolutionContext,
    EvolutionEvent,
    EvolutionMemoryContext,
    FitnessRecord,
    GoalSpec,
    LifeCapability,
    LifeContext,
    MemoryAccessMeta,
    MemoryItem,
    PlanSpec,
    PlanStep,
    Preloadable,
    ResourceSpec,
    SubAgentSpec,
    TaskSpec,
    VerifyPhaseSpec,
    TickContext,
    TickPhase,
    async_fold_tick,
    compute_fitness,
    fold_cognitive,
    fold_evolution,
    fold_evolution_memory,
    fold_life,
)
from mmkr.trace import ConsoleCollector, FileCollector, MultiCollector, TickTraceCollector

_C = TypeVar("_C")


class DataclassJsonCodec(Generic[_C]):
    """JSON codec for frozen dataclasses — human-readable persistence.

    encode: dataclass → JSON bytes (via dataclasses.asdict for recursive conversion)
    decode: JSON bytes → raw dict/list (caller reconstructs via _reconstruct_*)
    """

    @staticmethod
    def _to_json_obj(value: _C) -> dict[str, str | int | float | bool | None] | list[dict[str, str | int | float | bool | None]] | _C:
        if is_dataclass(value) and not isinstance(value, type):
            return asdict(value)
        if isinstance(value, list):
            return [DataclassJsonCodec._to_json_obj(v) for v in value]
        return value

    def encode(self, value: _C) -> bytes:
        return json.dumps(
            self._to_json_obj(value), indent=2, default=str,
        ).encode("utf-8")

    def decode(self, data: bytes) -> _C:
        # Returns parsed JSON (dict/list). KV callers reconstruct via
        # _reconstruct_state / _reconstruct_event etc. The _C type parameter
        # is a white lie for KV compatibility — decode returns raw JSON.
        return json.loads(data.decode("utf-8"))  # type: ignore[return-value]


# ═══════════════════════════════════════════════════════════════════════════════
# MemoryRecord — entity for the query axis
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """One memory entry — stored via storage axis, queried via query axis."""

    category: str
    content: str
    timestamp: float


# ═══════════════════════════════════════════════════════════════════════════════
# PersistentMemory — LifeCapability
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PersistentMemory:
    """Persistent memory across ticks — storage axis + query axis.

    compile_life contributes:
      - save_memory(category, content) — persist via FileStorage + KV
      - load_memories(category, limit) — query via MemoryRelationalProvider
      - search_memories(query, limit) — filter via ILike on relational axis
      - System message with last context_limit memories (not all)
    """

    memory_dir: Path
    context_limit: int = 50
    _records: tuple[MemoryRecord, ...] = field(default_factory=tuple, repr=False)

    async def preload(self) -> PersistentMemory:
        """Async init — loads memories from storage axis. Satisfies Preloadable."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        backend: FileStorage[str, bytes] = FileStorage(
            str(self.memory_dir / "memories.json"),
        )
        memories_kv: KV[list[MemoryRecord], Never] = KV(
            backend, DataclassJsonCodec[list[MemoryRecord]](),
        )
        result = await memories_kv.get("memories")
        match result:
            case Ok(Some(raw)):
                records = tuple(MemoryRecord(**r) for r in raw)
                return PersistentMemory(memory_dir=self.memory_dir, _records=records)
            case _:
                return PersistentMemory(memory_dir=self.memory_dir)

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        memory_dir = self.memory_dir

        provider: MemoryRelationalProvider[MemoryRecord] = MemoryRelationalProvider(
            data=list(self._records),
        )

        @tool("Save a memory for future ticks. Category groups related memories.")
        async def save_memory(category: str, content: str) -> dict[str, str | bool]:
            record = MemoryRecord(
                category=category, content=content, timestamp=time.time(),
            )
            provider.add(record)
            backend: FileStorage[str, bytes] = FileStorage(
                str(memory_dir / "memories.json"),
            )
            kv: KV[list[MemoryRecord], Never] = KV(
                backend, DataclassJsonCodec[list[MemoryRecord]](),
            )
            await kv.set("memories", list(provider.data))
            return {"saved": True, "category": category}

        @tool("Load recent memories, optionally filtered by category")
        def load_memories(
            category: str = "", limit: int = 20,
        ) -> dict[str, str | list[dict[str, str]]]:
            qs = relational(MemoryRecord)
            if category:
                qs = qs.filter(lambda m: m.category == category)
            qs = qs.order_by(lambda m: m.timestamp.desc()).limit(limit)
            records = provider._execute(qs)
            return {
                "memories": [{"category": r.category, "content": r.content} for r in records],
                "count": str(len(records)),
            }

        @tool("Search memories by keyword (case-insensitive substring match)")
        def search_memories(
            query: str, limit: int = 10,
        ) -> dict[str, str | list[dict[str, str]]]:
            pattern = f"%{query}%"
            expr = Or(
                ILike(Field("content"), pattern),
                ILike(Field("category"), pattern),
            )
            qs = (
                relational(MemoryRecord)
                .filter(lambda _, _expr=expr: _expr)
                .order_by(lambda m: m.timestamp.desc())
                .limit(limit)
            )
            records = provider._execute(qs)
            return {
                "results": [{"category": r.category, "content": r.content} for r in records],
                "count": str(len(records)),
            }

        recent_text = ""
        if self._records:
            # Last N by timestamp — older memories via load_memories()/search_memories()
            recent = sorted(self._records, key=lambda r: r.timestamp)[-self.context_limit:]
            parts = [f"[{r.category}] {r.content}" for r in recent]
            recent_text = (
                f"RECENT MEMORIES ({len(recent)} of {len(self._records)} total, "
                f"use load_memories/search_memories for older):\n"
                + "\n---\n".join(parts)
            )

        msg_text = "Persistent memory: save_memory/load_memories/search_memories."
        if recent_text:
            msg_text = f"{msg_text}\n\n{recent_text}"

        return replace(
            ctx,
            messages=(*ctx.messages, system(text=msg_text)),
            tools=(*ctx.tools, save_memory, load_memories, search_memories),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CapabilityEvolver — LifeCapability
# ═══════════════════════════════════════════════════════════════════════════════


def _wrap_tools_source(name: str, description: str, tools_source: str) -> str:
    """Wrap raw @tool functions into a proper LifeCapability source."""
    import ast
    import textwrap

    # Class name: filter to alnum parts, capitalize each
    parts = [w for w in name.lower().replace("-", "_").replace(" ", "_").split("_") if w.isalnum()]
    cls_name = "".join(w.capitalize() for w in parts) if parts else "EvolvedCapability"

    # Extract function names via AST — no regex
    tool_names: list[str] = []
    try:
        tree = ast.parse(tools_source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                tool_names.append(node.name)
    except SyntaxError:
        pass  # empty tool_names → no tools to register

    tools_list = ", ".join(tool_names) if tool_names else ""
    indented = textwrap.indent(tools_source.strip(), "        ")

    return f'''\
from dataclasses import dataclass, replace
from funcai.agents.tool import tool
from mmkr.state import LifeContext

@dataclass(frozen=True, slots=True)
class {cls_name}:
    """{description}"""

    def compile_life(self, ctx: LifeContext) -> LifeContext:
{indented}

        return replace(ctx, tools=(*ctx.tools, {tools_list}))
'''


@dataclass(frozen=True, slots=True)
class CapabilityLoadError:
    """Structured error from capability loading — no string return unions."""

    phase: str  # "syntax", "exec", "verify", "empty"
    message: str


# Re-export from state
from mmkr.state import VerificationIssue as VerificationIssue  # noqa: E402
from mmkr.state import VerificationResult as VerificationResult  # noqa: E402


def _load_capability(
    source_code: str,
    filename: str,
) -> tuple[LifeCapability, ...] | CapabilityLoadError:
    """Load capability from source: compile → exec → find LifeCapability classes → verify."""
    try:
        code = compile(source_code, filename, "exec")
    except SyntaxError as e:
        return CapabilityLoadError("syntax", str(e))

    namespace: dict[str, type | LifeCapability] = {}
    try:
        exec(code, namespace)  # noqa: S102
    except Exception as e:
        return CapabilityLoadError("exec", f"{type(e).__name__}: {e}")

    caps: list[LifeCapability] = []
    for obj in namespace.values():
        # Use runtime_checkable Protocol instead of hasattr
        if isinstance(obj, type) and issubclass(obj, LifeCapability):
            try:
                instance = obj()
                result = instance.compile_life(LifeContext())
                if not isinstance(result, LifeContext):
                    return CapabilityLoadError(
                        "verify",
                        f"{obj.__name__}.compile_life returned {type(result).__name__}, expected LifeContext",
                    )
                caps.append(instance)
            except Exception as e:
                return CapabilityLoadError("verify", f"{obj.__name__}: {type(e).__name__}: {e}")

    if not caps:
        return CapabilityLoadError("empty", "no LifeCapability class found in source")

    return tuple(caps)


async def _verify_capability_with_llmify(
    source_code: str,
    filename: str,
    token: str = "",
) -> VerificationResult | None:
    """Run llmify verification on capability source via Haiku. Async — no threading."""
    try:
        from kungfu import Ok
        from llmify import audit_method_logic, contract_check
        from llmify.claude_provider import make_claude_provider
    except ImportError:
        return None

    namespace: dict[str, type | LifeCapability] = {}
    try:
        exec(compile(source_code, filename, "exec"), namespace)  # noqa: S102
    except Exception:
        return None

    cap_cls: type | None = None
    for obj in namespace.values():
        if isinstance(obj, type) and issubclass(obj, LifeCapability):
            cap_cls = obj
            break
    if cap_cls is None:
        return None

    provider = make_claude_provider(model="haiku", token=token or "")

    issues: list[VerificationIssue] = []
    try:
        result = await contract_check(cap_cls).compile(provider)
        match result:
            case Ok(check_result):
                for issue in check_result.consistency.issues:
                    issues.append(VerificationIssue(field=issue.field, severity=issue.severity, message=issue.message))
            case err:
                issues.append(VerificationIssue(field="_contract", severity="warning", message=f"contract_check: {err}"))
    except Exception as e:
        issues.append(VerificationIssue(field="_contract", severity="warning", message=f"contract_check: {e}"))

    try:
        audit_result = await audit_method_logic(cap_cls).compile(provider)
        match audit_result:
            case Ok(audit):
                for issue in audit.issues:
                    issues.append(VerificationIssue(field=issue.method, severity=issue.severity, message=issue.message))
            case err:
                issues.append(VerificationIssue(field="_audit", severity="warning", message=f"audit: {err}"))
    except Exception as e:
        issues.append(VerificationIssue(field="_audit", severity="warning", message=f"audit: {e}"))

    return VerificationResult(
        passed=all(i.severity != "error" for i in issues),
        issues=tuple(issues),
    )


async def _log_usage_event(
    storage_dir: Path, subject: str, event_type: str, outcome: str, tick: int,
    parent: str = "", details: str = "",
) -> None:
    """Append usage event to pending storage via FileStorage + KV axis."""
    if not storage_dir or storage_dir == Path(""):
        return
    storage_dir.mkdir(parents=True, exist_ok=True)
    backend: FileStorage[str, bytes] = FileStorage(
        str(storage_dir / "usage_pending.json"),
    )
    kv: KV[list[EvolutionEvent], Never] = KV(
        backend, DataclassJsonCodec[list[EvolutionEvent]](),
    )
    existing: list[EvolutionEvent] = []
    result = await kv.get("events")
    match result:
        case Ok(Some(raw)):
            existing = [EvolutionEvent(**e) for e in raw]
    existing.append(EvolutionEvent(
        tick=tick, timestamp=time.time(), event_type=event_type,
        subject=subject, parent=parent, outcome=outcome, details=details,
    ))
    await kv.set("events", existing)


def _wrap_with_tracking(original: Tool, cap_name: str, storage_dir: Path, tick: int) -> Tool:
    """Wrap a Tool's fn to auto-log usage via storage axis. Returns new Tool (frozen).

    All funcai tools are async (agent runs in asyncio), so the wrapper is async.
    """
    original_fn = original.fn
    _is_coro = inspect.iscoroutinefunction(original_fn)

    async def tracked_fn(**kwargs: str | int | float | bool | None) -> dict[str, str | int | float | bool | None] | str:
        try:
            result = await original_fn(**kwargs) if _is_coro else original_fn(**kwargs)
            await _log_usage_event(storage_dir, cap_name, "cap_used", "success", tick)
            return result  # type: ignore[return-value]
        except Exception:
            await _log_usage_event(storage_dir, cap_name, "cap_error", "error", tick)
            raise

    return Tool(
        name=original.name, description=original.description,
        parameters=original.parameters, fn=tracked_fn,
        return_type=original.return_type,
    )


@dataclass(frozen=True, slots=True)
class CapabilityEvolver:
    """Evolve new capabilities at runtime — correct by construction.

    Agent writes @tool functions + description. System wraps, verifies, saves.
    storage_dir: where to write usage_pending.jsonl for auto-tracking.
    """

    evolved_dir: Path
    anthropic_key: str = ""
    storage_dir: Path = Path("")

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        edir = self.evolved_dir
        _api_key = self.anthropic_key
        _storage_dir = self.storage_dir
        edir.mkdir(parents=True, exist_ok=True)

        @tool(
            "Create a new capability. name=identifier, description=what it does, "
            "tools_source=ONLY async @tool-decorated functions (system wraps in LifeCapability). "
            "All tools MUST be async def. Verified by Haiku before saving."
        )
        async def create_capability(
            name: str, description: str, tools_source: str,
        ) -> dict[str, str | bool | list[dict[str, str]]]:
            import keyword
            import re

            safe = re.sub(r"[^a-z0-9_]", "_", name.lower())
            if not safe or keyword.iskeyword(safe):
                await _log_usage_event(_storage_dir, name, "cap_created", "fail_compile", ctx.tick, details="invalid name")
                return {"error": f"invalid capability name: {name!r}"}

            filename = f"cap_{safe}.py"
            filepath = edir / filename

            if "@tool" not in tools_source:
                await _log_usage_event(_storage_dir, filename, "cap_created", "fail_compile", ctx.tick, details="no @tool")
                return {"error": "tools_source must contain at least one @tool function"}

            full_source = _wrap_tools_source(name, description, tools_source)
            load_result = _load_capability(full_source, filename)
            if isinstance(load_result, CapabilityLoadError):
                await _log_usage_event(_storage_dir, filename, "cap_created", "fail_compile", ctx.tick, details=load_result.message)
                return {"error": f"structural ({load_result.phase}): {load_result.message}"}

            verification = await _verify_capability_with_llmify(full_source, filename, token=_api_key)
            if verification is not None and not verification.passed:
                await _log_usage_event(_storage_dir, filename, "cap_created", "fail_verify", ctx.tick, details="haiku rejected")
                return {"error": "Haiku verification failed", "issues": [{"field": i.field, "severity": i.severity, "message": i.message} for i in verification.issues]}

            test_ctx = LifeContext()
            total_tools = 0
            for cap in load_result:
                folded = cap.compile_life(test_ctx)
                total_tools += len(folded.tools) - len(test_ctx.tools)

            filepath.write_text(full_source)

            await _log_usage_event(_storage_dir, filename, "cap_created", "success", ctx.tick)
            out: dict[str, str | bool | list[dict[str, str]]] = {
                "created": True, "filename": filename, "tools_produced": str(total_tools),
            }
            if verification and verification.issues:
                out["haiku_notes"] = [{"field": i.field, "severity": i.severity, "message": i.message} for i in verification.issues]
            return out

        @tool("List all evolved capabilities (cap_*.py files)")
        def list_evolved_capabilities() -> dict[str, list[str]]:
            files = sorted(p.name for p in edir.glob("cap_*.py"))
            return {"capabilities": files}

        @tool("Read source of a cap_*.py file. Use list_evolved_capabilities() to see available.")
        def read_capability(filename: str) -> dict[str, str | bool]:
            filepath = edir / filename
            if not filepath.exists():
                return {"error": f"file not found: {filename}"}
            if not filename.startswith("cap_") or not filename.endswith(".py"):
                return {"error": "can only read cap_*.py files"}
            content = filepath.read_text(encoding="utf-8")
            return {"filename": filename, "source": content}

        @tool(
            "Evolve existing capability in-place. filename=cap_*.py, "
            "new_tools_source=ONLY async @tool functions, description=optional. "
            "Original backed up as .bak. Verified by Haiku."
        )
        async def evolve_capability(
            filename: str, new_tools_source: str, description: str = "",
        ) -> dict[str, str | bool | list[dict[str, str]]]:
            filepath = edir / filename
            if not filepath.exists():
                await _log_usage_event(_storage_dir, filename, "cap_evolved", "fail_compile", ctx.tick, details="not found")
                return {"error": f"file not found: {filename}"}
            if not filename.startswith("cap_") or not filename.endswith(".py"):
                return {"error": "can only evolve cap_*.py files"}
            if "@tool" not in new_tools_source:
                await _log_usage_event(_storage_dir, filename, "cap_evolved", "fail_compile", ctx.tick, details="no @tool")
                return {"error": "new_tools_source must contain at least one @tool function"}

            name = filename.removeprefix("cap_").removesuffix(".py")
            desc = description or f"Evolved capability: {name}"
            full_source = _wrap_tools_source(name, desc, new_tools_source)

            load_result = _load_capability(full_source, filename)
            if isinstance(load_result, CapabilityLoadError):
                await _log_usage_event(_storage_dir, filename, "cap_evolved", "fail_compile", ctx.tick, details=load_result.message)
                return {"error": f"structural ({load_result.phase}): {load_result.message}"}

            verification = await _verify_capability_with_llmify(full_source, filename, token=_api_key)
            if verification is not None and not verification.passed:
                await _log_usage_event(_storage_dir, filename, "cap_evolved", "fail_verify", ctx.tick, details="haiku rejected")
                return {"error": "Haiku verification failed", "issues": [{"field": i.field, "severity": i.severity, "message": i.message} for i in verification.issues]}

            # Backup original
            backup = filepath.with_suffix(".py.bak")
            backup.write_text(filepath.read_text(encoding="utf-8"), encoding="utf-8")

            filepath.write_text(full_source, encoding="utf-8")
            await _log_usage_event(_storage_dir, filename, "cap_evolved", "success", ctx.tick)
            out: dict[str, str | bool | list[dict[str, str]]] = {
                "evolved": True, "filename": filename, "backup": backup.name,
            }
            if verification and verification.issues:
                out["haiku_notes"] = [{"field": i.field, "severity": i.severity, "message": i.message} for i in verification.issues]
            return out

        @tool(
            "Fork a capability — create variant, original untouched. "
            "source_filename=existing cap_*.py, new_name=variant name, "
            "new_tools_source=ONLY async @tool functions. Verified by Haiku."
        )
        async def fork_capability(
            source_filename: str, new_name: str, new_tools_source: str, description: str = "",
        ) -> dict[str, str | bool | list[dict[str, str]]]:
            import keyword
            import re

            source_path = edir / source_filename
            if not source_path.exists():
                await _log_usage_event(_storage_dir, source_filename, "cap_forked", "fail_compile", ctx.tick, details="source not found")
                return {"error": f"source not found: {source_filename}"}

            safe = re.sub(r"[^a-z0-9_]", "_", new_name.lower())
            if not safe or keyword.iskeyword(safe):
                await _log_usage_event(_storage_dir, new_name, "cap_forked", "fail_compile", ctx.tick, parent=source_filename, details="invalid name")
                return {"error": f"invalid name: {new_name!r}"}

            new_filename = f"cap_{safe}.py"
            new_path = edir / new_filename
            if new_path.exists():
                return {"error": f"already exists: {new_filename}"}

            if "@tool" not in new_tools_source:
                await _log_usage_event(_storage_dir, new_filename, "cap_forked", "fail_compile", ctx.tick, parent=source_filename, details="no @tool")
                return {"error": "new_tools_source must contain at least one @tool function"}

            desc = description or f"Forked from {source_filename}: {new_name}"
            full_source = _wrap_tools_source(new_name, desc, new_tools_source)

            load_result = _load_capability(full_source, new_filename)
            if isinstance(load_result, CapabilityLoadError):
                await _log_usage_event(_storage_dir, new_filename, "cap_forked", "fail_compile", ctx.tick, parent=source_filename, details=load_result.message)
                return {"error": f"structural ({load_result.phase}): {load_result.message}"}

            verification = await _verify_capability_with_llmify(full_source, new_filename, token=_api_key)
            if verification is not None and not verification.passed:
                await _log_usage_event(_storage_dir, new_filename, "cap_forked", "fail_verify", ctx.tick, parent=source_filename, details="haiku rejected")
                return {"error": "Haiku verification failed", "issues": [{"field": i.field, "severity": i.severity, "message": i.message} for i in verification.issues]}

            new_path.write_text(full_source, encoding="utf-8")
            await _log_usage_event(_storage_dir, new_filename, "cap_forked", "success", ctx.tick, parent=source_filename)
            out: dict[str, str | bool | list[dict[str, str]]] = {
                "forked": True, "source": source_filename, "new_file": new_filename,
            }
            if verification and verification.issues:
                out["haiku_notes"] = [{"field": i.field, "severity": i.severity, "message": i.message} for i in verification.issues]
            return out

        @tool("Delete an evolved capability file")
        async def delete_capability(filename: str) -> dict[str, str | bool]:
            filepath = edir / filename
            if not filepath.exists():
                return {"error": f"file not found: {filename}"}
            if not filename.startswith("cap_") or not filename.endswith(".py"):
                return {"error": "can only delete cap_*.py files"}
            filepath.unlink()
            await _log_usage_event(_storage_dir, filename, "cap_deleted", "success", ctx.tick)
            return {"deleted": True, "filename": filename}

        # Load existing evolved capabilities
        load_errors: list[str] = []
        loaded_count = 0
        for cap_file in sorted(edir.glob("cap_*.py")):
            source_code = cap_file.read_text()
            result = _load_capability(source_code, cap_file.name)
            if isinstance(result, CapabilityLoadError):
                load_errors.append(f"{cap_file.name}: {result.message}")
                continue
            for cap in result:
                before_count = len(ctx.tools)
                ctx = cap.compile_life(ctx)
                # Wrap newly-added tools with usage tracking via storage axis
                if _storage_dir.parts:  # non-empty path
                    new_tools = ctx.tools[before_count:]
                    wrapped = tuple(
                        _wrap_with_tracking(t, cap_file.name, _storage_dir, ctx.tick)
                        for t in new_tools
                        if isinstance(t, Tool)
                    )
                    # Keep non-Tool items as-is (evolved caps may add plain functions)
                    non_tools = tuple(t for t in new_tools if not isinstance(t, Tool))
                    ctx = replace(ctx, tools=(*ctx.tools[:before_count], *wrapped, *non_tools))
                loaded_count += 1

        msg_parts = [
            "Capability evolution: create / read / evolve / fork / list / delete.",
            f"Loaded: {loaded_count} evolved capabilities.",
        ]
        if load_errors:
            msg_parts.append(f"Load errors: {'; '.join(load_errors)}")

        return replace(
            ctx,
            messages=(*ctx.messages, system(text="\n".join(msg_parts))),
            tools=(
                *ctx.tools,
                create_capability, read_capability, evolve_capability,
                fork_capability, list_evolved_capabilities, delete_capability,
            ),
        )

    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        """Contribute mutation count (.bak files)."""
        mutation_count = 0
        if self.evolved_dir.exists():
            mutation_count = len(list(self.evolved_dir.glob("*.bak")))
        return replace(
            ctx,
            mutation_count=ctx.mutation_count + mutation_count,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Seed — author's idea injected into the tick
# ═══════════════════════════════════════════════════════════════════════════════


class SkipTick(Exception):
    """Raised by skip_tick tool — ends current tick, triggers next fold."""


@dataclass(frozen=True, slots=True)
class Seed:
    """A seed idea from the author, injected as a system message."""

    text: str

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        return replace(
            ctx,
            messages=(*ctx.messages, system(text=f"SEED FROM AUTHOR:\n{self.text}")),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# EvolutionStorage — evolution history persistence
# ═══════════════════════════════════════════════════════════════════════════════


async def _consume_pending_usage(storage_dir: Path) -> tuple[EvolutionEvent, ...]:
    """Read and clear pending usage events via FileStorage + KV axis."""
    pending_path = storage_dir / "usage_pending.json"
    if not pending_path.exists():
        return ()
    backend: FileStorage[str, bytes] = FileStorage(str(pending_path))
    kv: KV[list[EvolutionEvent], Never] = KV(
        backend, DataclassJsonCodec[list[EvolutionEvent]](),
    )
    result = await kv.get("events")
    events: tuple[EvolutionEvent, ...] = ()
    match result:
        case Ok(Some(raw)):
            events = tuple(EvolutionEvent(**e) for e in raw)
    # Clear pending — write empty list
    if events:
        await kv.set("events", [])
    return events


@dataclass(frozen=True, slots=True)
class EvolutionStorage:
    """Persistent evolution history — two-sided capability.

    compile_evolution: loads history from storage → ctx.history + computes fitness
    compile_life: provides log_evolution + query_evolution + evaluate_fitness tools
    Preloadable: async loads from evolution.json + consumes usage_pending.json
    """

    storage_dir: Path
    _events: tuple[EvolutionEvent, ...] = ()

    async def preload(self) -> EvolutionStorage:
        """Async init — loads events from storage axis + consumes pending usage."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        # Load main events via FileStorage + KV
        backend: FileStorage[str, bytes] = FileStorage(
            str(self.storage_dir / "evolution.json"),
        )
        events_kv: KV[list[EvolutionEvent], Never] = KV(
            backend, DataclassJsonCodec[list[EvolutionEvent]](),
        )
        main_events: list[EvolutionEvent] = []
        result = await events_kv.get("events")
        match result:
            case Ok(Some(raw)):
                main_events = [EvolutionEvent(**e) for e in raw]

        # Consume pending usage events from CapabilityEvolver tracking
        pending = await _consume_pending_usage(self.storage_dir)
        if pending:
            main_events.extend(pending)
            # Persist merged events back via storage axis
            await events_kv.set("events", main_events)

        return EvolutionStorage(
            storage_dir=self.storage_dir,
            _events=tuple(main_events),
        )

    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        """Contribute loaded history to evolution context."""
        return replace(ctx, history=(*ctx.history, *self._events))

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        """Provide log_evolution, query_evolution, evaluate_fitness tools."""
        storage_dir = self.storage_dir
        events_list: list[EvolutionEvent] = list(self._events)

        @tool(
            "Log evolution event. event_type: cap_created/cap_evolved/cap_forked/"
            "cap_deleted/cap_used/cap_error/entity_created/entity_failed. "
            "subject=capability/entity name, outcome=success/fail_verify/fail_compile/error, "
            "parent=source (if evolved/forked), details=extra info."
        )
        async def log_evolution(
            event_type: str,
            subject: str,
            outcome: str,
            parent: str = "",
            details: str = "",
        ) -> dict[str, str | bool]:
            event = EvolutionEvent(
                tick=ctx.tick,
                timestamp=time.time(),
                event_type=event_type,
                subject=subject,
                parent=parent,
                outcome=outcome,
                details=details,
            )
            events_list.append(event)

            # Persist via FileStorage + KV
            backend: FileStorage[str, bytes] = FileStorage(
                str(storage_dir / "evolution.json"),
            )
            kv: KV[list[EvolutionEvent], Never] = KV(
                backend, DataclassJsonCodec[list[EvolutionEvent]](),
            )
            await kv.set("events", events_list)
            return {"logged": True, "event_type": event_type, "subject": subject}

        @tool(
            "Query evolution history. event_type=filter (e.g. 'cap_created'), "
            "subject=filter by name, limit=max results (default 20)."
        )
        def query_evolution(
            event_type: str = "",
            subject: str = "",
            limit: int = 20,
        ) -> dict[str, str | list[dict[str, str]]]:
            filtered = list(events_list)
            if event_type:
                filtered = [e for e in filtered if e.event_type == event_type]
            if subject:
                filtered = [e for e in filtered if subject.lower() in e.subject.lower()]
            filtered = filtered[-limit:]
            return {
                "events": [
                    {
                        "tick": str(e.tick),
                        "event_type": e.event_type,
                        "subject": e.subject,
                        "parent": e.parent,
                        "outcome": e.outcome,
                        "details": e.details,
                    }
                    for e in filtered
                ],
                "total": str(len(events_list)),
                "shown": str(len(filtered)),
            }

        @tool(
            "Compute fitness rankings for all evolved capabilities. "
            "Score = survival * reproductive * quality, event-sourced from history."
        )
        def evaluate_fitness() -> dict[str, list[dict[str, str | float]] | str]:
            records = compute_fitness(events_list, ctx.tick)
            return {
                "rankings": [
                    {
                        "name": r.name,
                        "score": r.fitness_score,
                        "generation": str(r.generation),
                        "usage": str(r.usage_count),
                        "errors": str(r.error_count),
                        "age": str(r.ticks_alive),
                        "offspring": str(r.offspring_count),
                    }
                    for r in records
                ],
                "total": str(len(records)),
            }

        parts = ["EVOLUTION TRACKING (log_evolution / query_evolution / evaluate_fitness)"]

        # Fitness rankings
        records = compute_fitness(events_list, ctx.tick)
        if records:
            parts.append("\nFITNESS RANKINGS:")
            for i, r in enumerate(records[:5], 1):
                flag = " LOW" if r.fitness_score < 0.5 and r.ticks_alive > 3 else ""
                parts.append(
                    f"  #{i} {r.name}  score={r.fitness_score}  gen={r.generation}"
                    f"  used={r.usage_count}  err={r.error_count}  age={r.ticks_alive}{flag}"
                )

        # Recent events
        if events_list:
            parts.append(f"\nRECENT EVENTS ({len(events_list)} total):")
            for e in events_list[-5:]:
                parts.append(f"  [tick {e.tick}] {e.event_type} {e.subject} -> {e.outcome}")

        # Learning signals
        if not events_list:
            parts.append(
                "\nNO EVOLUTION EVENTS YET. Create capabilities, entities, "
                "or use save_memory to start learning."
            )
        elif records and all(r.usage_count == 0 for r in records):
            parts.append("\nCapabilities exist but UNUSED. Try using them or evolve them.")

        return replace(
            ctx,
            messages=(*ctx.messages, system(text="\n".join(parts))),
            tools=(*ctx.tools, log_evolution, query_evolution, evaluate_fitness),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Tick phases — reality as capabilities + fold
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class PreloadPhase:
    """Preloads async capabilities via Preloadable protocol.

    Capabilities that need async initialization (e.g. PersistentMemory,
    EvolutionStorage) implement Preloadable.preload() → LifeCapability.
    """

    async def compile_tick(self, ctx: TickContext) -> TickContext:
        preloaded: list[LifeCapability] = []
        for cap in ctx.capabilities:
            if isinstance(cap, Preloadable):
                preloaded.append(await cap.preload())
            else:
                preloaded.append(cap)
        return replace(ctx, capabilities=tuple(preloaded))


@dataclass(frozen=True, slots=True)
class EvolutionFoldPhase:
    """Folds evolution axis — history, fitness, selection pressure.

    Dispatches on EvolutionCapability. Computes event-sourced fitness.
    Injects evolution summary into tick messages.
    """

    async def compile_tick(self, ctx: TickContext) -> TickContext:
        evo_ctx = fold_evolution(
            ctx.capabilities, EvolutionContext(tick=ctx.state.tick),
        )
        messages = ctx.messages
        if evo_ctx.summary:
            messages = (
                *messages,
                system(text=f"EVOLUTION STATE:\n{evo_ctx.summary}"),
            )
        return replace(ctx, evolution=evo_ctx, messages=messages)


@dataclass(frozen=True, slots=True)
class EvolutionMemoryFoldPhase:
    """Folds evolution memory axis — windowed history, snapshots, patterns, verify phases.

    Seeds EvolutionMemoryContext from current tick's evolution history.
    Carries forward snapshots, patterns, verify_phases from previous tick.
    Folds all EvolutionMemoryCapability instances.
    """

    async def compile_tick(self, ctx: TickContext) -> TickContext:
        # Seed from evolution history + carry forward previous memory
        mem_ctx = EvolutionMemoryContext(
            tick=ctx.state.tick,
            recent_history=ctx.evolution.history,
            fitness_snapshots=ctx.evolution_memory.fitness_snapshots,
            patterns=ctx.evolution_memory.patterns,
            verify_phases=ctx.evolution_memory.verify_phases,
            archive_summary=ctx.evolution_memory.archive_summary,
        )
        mem_ctx = fold_evolution_memory(ctx.capabilities, mem_ctx)
        return replace(ctx, evolution_memory=mem_ctx)


def _hash_content(content: str) -> str:
    """Deterministic hash for memory content identity."""
    import hashlib
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _default_importance(category: str) -> float:
    """Default importance by category — explicit mapping, not heuristic."""
    match category:
        case "tick_outcome":
            return 0.8  # Learning signals are important
        case "error" | "failure":
            return 0.9  # Failures are very important
        case "goal" | "plan":
            return 0.7
        case _:
            return 0.5


@dataclass(frozen=True, slots=True)
class CognitiveFoldPhase:
    """Folds cognitive axis — memory consolidation, goals, attention.

    Seeds CognitiveContext from PersistentMemory records + AgentState goals.
    Wraps MemoryRecord → MemoryItem using access metadata from state.
    Folds CognitiveCapability instances.
    Injects working memory summary + active goal into messages.
    """

    async def compile_tick(self, ctx: TickContext) -> TickContext:
        # Find PersistentMemory in capabilities to get raw records
        records: tuple[MemoryRecord, ...] = ()
        for cap in ctx.capabilities:
            if isinstance(cap, PersistentMemory):
                records = cap._records
                break

        # Build access metadata lookup
        access_by_key: dict[str, MemoryAccessMeta] = {
            f"{a.category}:{a.content_hash}": a
            for a in ctx.state.memory_access_log
        }

        # Convert MemoryRecords → MemoryItems
        memories: list[MemoryItem] = []
        for r in records:
            key = f"{r.category}:{_hash_content(r.content)}"
            meta = access_by_key.get(key)
            base_importance = (
                meta.importance_base if meta
                else _default_importance(r.category)
            )
            memories.append(MemoryItem(
                content=r.content,
                category=r.category,
                importance_base=base_importance,
                effective_importance=base_importance,
                tier="short_term",
                access_count=meta.access_count if meta else 0,
                last_accessed_tick=meta.last_accessed_tick if meta else 0,
                created_tick=meta.created_tick if meta else 0,
            ))

        cog_ctx = CognitiveContext(
            tick=ctx.state.tick,
            memories=tuple(memories),
            goals=ctx.state.goals,
            memory_access_log=ctx.state.memory_access_log,
            plans=ctx.state.plans,
            resources=ctx.state.resources,
            tasks=ctx.state.tasks,
        )
        cog_ctx = fold_cognitive(ctx.capabilities, cog_ctx)

        # Inject working memory summary + active goal into messages
        messages = ctx.messages
        if cog_ctx.working_memory:
            parts = [
                f"[{m.category}] {m.content}"
                for m in cog_ctx.working_memory
            ]
            summary = "WORKING MEMORY (most relevant):\n" + "\n".join(parts)
            messages = (*messages, system(text=summary))

        if cog_ctx.active_goal:
            messages = (*messages, system(text=f"ACTIVE GOAL: {cog_ctx.active_goal}"))

        return replace(ctx, cognitive=cog_ctx, messages=messages)


@dataclass(frozen=True, slots=True)
class LifeFoldPhase:
    """Folds life axis — messages and tools from all LifeCapabilities.

    Dispatches on LifeCapability. Deduplicates tools by name.
    Appends life messages/tools to tick context (preserving earlier phases).
    """

    async def compile_tick(self, ctx: TickContext) -> TickContext:
        life_ctx = LifeContext(tick=ctx.state.tick)
        life_ctx = fold_life(ctx.capabilities, life_ctx)
        return replace(
            ctx,
            messages=(*ctx.messages, *life_ctx.messages),
            tools=(*ctx.tools, *life_ctx.tools),
        )


@dataclass(frozen=True, slots=True)
class LearnPhase:
    """Propagates evolution_memory + cognitive results → state.

    Institutional learning: evolution memory fold computes what phases
    SHOULD exist; cognitive fold computes goals and memory metadata.
    LearnPhase persists all to state. Deduplicates verify phases by name.
    """

    async def compile_tick(self, ctx: TickContext) -> TickContext:
        new_state = ctx.state

        # Evolution memory → verify phases
        existing_names = {p.name for p in new_state.verify_phases}
        new_phases = tuple(
            p for p in ctx.evolution_memory.verify_phases
            if p.name not in existing_names
        )
        if new_phases:
            new_state = replace(
                new_state,
                verify_phases=(*new_state.verify_phases, *new_phases),
            )

        # Cognitive → goals + memory access log + plans + resources + tasks
        if ctx.cognitive.goals != new_state.goals:
            new_state = replace(new_state, goals=ctx.cognitive.goals)
        if ctx.cognitive.memory_access_log != new_state.memory_access_log:
            new_state = replace(
                new_state,
                memory_access_log=ctx.cognitive.memory_access_log,
            )
        if ctx.cognitive.plans != new_state.plans:
            new_state = replace(new_state, plans=ctx.cognitive.plans)
        if ctx.cognitive.resources != new_state.resources:
            new_state = replace(new_state, resources=ctx.cognitive.resources)
        if ctx.cognitive.tasks != new_state.tasks:
            new_state = replace(new_state, tasks=ctx.cognitive.tasks)

        if new_state is ctx.state:
            return ctx
        return replace(ctx, state=new_state)


@dataclass(frozen=True, slots=True)
class StateAdvancePhase:
    """Advances state.tick by 1 — marks tick completion."""

    async def compile_tick(self, ctx: TickContext) -> TickContext:
        return replace(ctx, state=replace(ctx.state, tick=ctx.state.tick + 1))


@dataclass(frozen=True)
class ConversationLog:
    """Persist raw conversation history (incl. tool_use) and inject last N messages.

    After each tick, ConversationSavePhase saves provider._last_api_messages.
    Before each tick, compile_life loads last N ticks and injects as messages.

    No haiku summarization — raw messages, Sonnet reads them directly.
    """

    log_dir: Path
    last_n: int = 25
    _provider_ref: list[ClaudeProvider | None] = field(
        default_factory=lambda: [None], repr=False,
    )

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        """Load last N ticks' conversation as system messages."""
        history_dir = self.log_dir / "conversation_log"
        if not history_dir.exists():
            return ctx

        # Find tick files, sorted by tick number (most recent last)
        tick_files = sorted(
            history_dir.glob("tick_*.json"),
            key=lambda p: int(p.stem.split("_")[1]),
        )

        # Take last N
        recent_files = tick_files[-self.last_n :]
        if not recent_files:
            return ctx

        history_msgs: list[Message] = []
        for f in recent_files:
            try:
                data = json.loads(f.read_text())
                tick_num = data.get("tick", "?")
                api_msgs: list[dict[str, object]] = data.get("messages", [])
                parts: list[str] = [f"=== TICK {tick_num} ==="]
                for msg in api_msgs:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        parts.append(f"[{role}] {content[:1000]}")
                    elif isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            btype = block.get("type", "")
                            if btype == "text":
                                parts.append(f"[{role}] {block.get('text', '')[:1000]}")
                            elif btype == "tool_use":
                                name = block.get("name", "?")
                                inp = json.dumps(block.get("input", {}))[:500]
                                parts.append(f"[{role}/tool_use] {name}({inp})")
                            elif btype == "tool_result":
                                content_text = block.get("content", "")[:500]
                                is_err = block.get("is_error", False)
                                prefix = "ERROR" if is_err else "result"
                                parts.append(f"[tool_{prefix}] {content_text}")
                history_msgs.append(system(text="\n".join(parts)))
            except Exception:
                continue

        if history_msgs:
            header = system(
                text=f"CONVERSATION HISTORY (last {len(history_msgs)} ticks — "
                "raw messages including tool calls):",
            )
            return replace(
                ctx,
                messages=(*ctx.messages, header, *history_msgs),
            )
        return ctx


@dataclass(frozen=True, slots=True)
class ConversationSavePhase:
    """TickPhase — saves raw conversation (with tool_use) after ConversationPhase.

    Reads provider._last_api_messages and persists to JSON.
    Auto-detected when ConversationLog is in capabilities.
    """

    async def compile_tick(self, ctx: TickContext) -> TickContext:
        log: ConversationLog | None = None
        for cap in ctx.capabilities:
            if isinstance(cap, ConversationLog):
                log = cap
                break

        if log is None or log._provider_ref[0] is None:
            return ctx

        provider = log._provider_ref[0]
        api_messages = provider._last_api_messages
        if not api_messages:
            return ctx

        history_dir = log.log_dir / "conversation_log"
        history_dir.mkdir(parents=True, exist_ok=True)

        # Serialize — anthropic MessageParam dicts should be JSON-safe
        # but content blocks may have non-serializable types
        safe_messages: list[dict[str, object]] = []
        for msg in api_messages:
            safe_msg: dict[str, object] = {"role": msg.get("role", "")}
            content = msg.get("content", "")
            if isinstance(content, str):
                safe_msg["content"] = content
            elif isinstance(content, list):
                safe_blocks: list[dict[str, object]] = []
                for block in content:
                    if isinstance(block, dict):
                        safe_blocks.append(
                            {k: _safe_json_value(v) for k, v in block.items()},
                        )
                    elif hasattr(block, "__dict__"):
                        # Anthropic content block objects
                        d: dict[str, object] = {}
                        if hasattr(block, "type"):
                            d["type"] = str(block.type)
                        if hasattr(block, "text"):
                            d["text"] = str(block.text)
                        if hasattr(block, "name"):
                            d["name"] = str(block.name)
                        if hasattr(block, "input"):
                            d["input"] = _safe_json_value(block.input)
                        if hasattr(block, "id"):
                            d["id"] = str(block.id)
                        safe_blocks.append(d)
                    else:
                        safe_blocks.append({"raw": str(block)})
                safe_msg["content"] = safe_blocks
            else:
                safe_msg["content"] = str(content)
            safe_messages.append(safe_msg)

        tick_data = {
            "tick": ctx.state.tick,
            "messages": safe_messages,
        }

        path = history_dir / f"tick_{ctx.state.tick:06d}.json"
        path.write_text(json.dumps(tick_data, ensure_ascii=False, default=str))

        return ctx


def _safe_json_value(v: object) -> object:
    """Convert a value to something JSON-serializable."""
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, dict):
        return {str(k): _safe_json_value(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_safe_json_value(item) for item in v]
    return str(v)


@dataclass(frozen=True, slots=True)
class ConversationPhase:
    """LLM conversation — the agent's cognitive tick.

    One send_messages call per tick. The provider's internal tool loop
    handles multi-round tool calls (up to max_tool_rounds).

    System prompt comes from Seed + Knowledge capabilities, not hardcoded here.
    """

    provider: ClaudeProvider
    trace: TickTraceCollector | None = None
    system_prompt: str = ""

    async def compile_tick(self, ctx: TickContext) -> TickContext:
        @tool("End this tick — save progress and move to next tick.")
        def skip_tick(reason: str = "") -> dict[str, str]:
            raise SkipTick(reason)

        all_tools: list[Tool] = [*ctx.tools, skip_tick]

        state_text = _format_tick_state(ctx.state)
        sys_msgs: list[Message] = []
        if self.system_prompt:
            sys_msgs.append(system(text=self.system_prompt))
        messages: list[Message] = [
            *sys_msgs,
            *ctx.messages,
            user(text=f"TICK {ctx.state.tick}\n\n{state_text}\n\nObserve, think, act."),
        ]

        if self.trace:
            self.trace.llm_prompt(
                ctx.state.tick, "LIFE",
                f"tick {ctx.state.tick}, tools={len(all_tools)}",
            )

        try:
            res = await self.provider.send_messages(messages, tools=all_tools)
        except SkipTick as e:
            if self.trace:
                self.trace.llm_response(ctx.state.tick, "LIFE", f"skip_tick: {e}")
            return replace(ctx, skipped=True, skip_reason=str(e))

        match res:
            case Ok(response):
                text = response.message.text.unwrap_or("")
                if self.trace:
                    self.trace.llm_response(ctx.state.tick, "LIFE", text[:500])
                return replace(ctx, response_text=text)
            case error:
                err_msg = str(error)
                if self.trace:
                    self.trace.error(
                        ctx.state.tick, "LIFE",
                        f"agent call failed: {err_msg}",
                    )
                return replace(ctx, response_text=f"ERROR: {err_msg}")


# ═══════════════════════════════════════════════════════════════════════════════
# Sub-agent capability — agents spawning sub-agents
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AsyncDelegation:
    """Actor-model sub-agent delegation — fire-and-forget.

    Each SubAgentSpec becomes a `delegate_{name}(task)` tool that returns
    immediately with a task_id. Sub-agents run as asyncio.Tasks in background.

    check_inbox() returns completed results. DelegationCollectPhase awaits
    remaining tasks at tick end.

    compile_life: creates delegate + inbox tools + system message.
    """

    provider: ClaudeProvider
    agents: tuple[SubAgentSpec, ...] = ()
    _pending: dict[str, asyncio.Task[None]] = field(default_factory=dict, repr=False)
    _results: dict[str, dict[str, str]] = field(default_factory=dict, repr=False)
    _next_id: list[int] = field(default_factory=lambda: [0], repr=False)

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        if not self.agents:
            return ctx

        pending = self._pending
        results = self._results
        next_id = self._next_id
        provider = self.provider

        new_tools: list[Tool] = []
        info_parts: list[str] = []

        # Pass all folded messages to sub-agents — no string dispatch
        parent_messages = ctx.messages

        for spec in self.agents:
            new_tools.append(
                _make_async_delegate_tool(
                    spec, provider, ctx.tick, pending, results, next_id,
                    parent_messages=parent_messages,
                ),
            )
            info_parts.append(
                f"- delegate_{spec.name}(task) — {spec.description} [ASYNC — returns immediately]",
            )

        @tool("Check inbox for completed sub-agent results. Results persist until clear_inbox().")
        async def check_inbox() -> dict[str, dict[str, str] | list[str]]:
            return {
                "completed": dict(results),
                "pending": list(pending.keys()),
            }

        @tool("Clear completed results from inbox after reading them.")
        async def clear_inbox() -> dict[str, str | bool]:
            count = len(results)
            results.clear()
            return {"cleared": True, "count": str(count)}

        new_tools.append(check_inbox)
        new_tools.append(clear_inbox)
        info_parts.append(
            "- check_inbox() — read completed results (persistent until clear_inbox)",
        )
        info_parts.append(
            "- clear_inbox() — clear completed results after reading",
        )

        info_msg = system(
            text=(
                "SUB-AGENTS (async — fire and forget):\n"
                + "\n".join(info_parts)
                + "\n\ndelegate_{name}(task) returns immediately with task_id. "
                "Sub-agents work in background while you think. "
                "Call check_inbox() later to collect results. "
                "Do other work between delegating and checking."
            ),
        )

        return replace(
            ctx,
            tools=(*ctx.tools, *new_tools),
            messages=(*ctx.messages, info_msg),
        )


# Keep backward compat alias
SubAgentCapability = AsyncDelegation


def _make_async_delegate_tool(
    spec: SubAgentSpec,
    provider: ClaudeProvider,
    tick: int,
    pending: dict[str, asyncio.Task[None]],
    results: dict[str, dict[str, str]],
    next_id: list[int],
    parent_messages: tuple[Message, ...] = (),
) -> Tool:
    """Create an async delegation tool for a sub-agent spec."""
    sub_caps = spec.capabilities
    sub_prompt = spec.system_prompt
    agent_name = spec.name

    @tool(f"Delegate task to {spec.name} — async, returns immediately")
    async def delegate(task: str) -> dict[str, str]:
        task_id = f"{agent_name}_{next_id[0]}"
        next_id[0] += 1

        async def _run_sub() -> None:
            sub_ctx = LifeContext(tick=tick)
            sub_ctx = fold_life(sub_caps, sub_ctx)
            sub_messages: list[Message] = [
                system(text=sub_prompt),
                *parent_messages,
                *sub_ctx.messages,
                user(text=task),
            ]
            sub_tools = list(sub_ctx.tools) if sub_ctx.tools else None
            try:
                res = await provider.send_messages(sub_messages, tools=sub_tools)
                match res:
                    case Ok(response):
                        results[task_id] = {
                            "result": response.message.text.unwrap_or(""),
                            "status": "done",
                        }
                    case _:
                        results[task_id] = {"error": str(res), "status": "error"}
            except Exception as e:
                results[task_id] = {"error": str(e), "status": "error"}
            finally:
                pending.pop(task_id, None)

        pending[task_id] = asyncio.create_task(_run_sub())
        return {"task_id": task_id, "status": "started", "agent": agent_name}

    return replace(delegate, name=f"delegate_{agent_name}")


@dataclass(frozen=True, slots=True)
class DelegationCollectPhase:
    """TickPhase — awaits remaining sub-agent tasks at tick end.

    Runs after ConversationPhase. Finds AsyncDelegation in capabilities,
    awaits all pending tasks with timeout. Ensures no leaked tasks.
    """

    timeout_s: float = 60.0

    async def compile_tick(self, ctx: TickContext) -> TickContext:
        delegation: AsyncDelegation | None = None
        for cap in ctx.capabilities:
            if isinstance(cap, AsyncDelegation):
                delegation = cap
                break

        if delegation is None or not delegation._pending:
            return ctx

        # Await all pending with timeout
        tasks = list(delegation._pending.values())
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self.timeout_s,
            )
        except asyncio.TimeoutError:
            # Cancel remaining
            for t in tasks:
                if not t.done():
                    t.cancel()
        return ctx


def standard_tick(
    provider: ClaudeProvider,
    trace: TickTraceCollector | None = None,
) -> tuple[TickPhase, ...]:
    """Standard tick pipeline — the default REALITY.

    Returns a tuple of phases that IS the tick. Not imperative code.
    AI can modify this: add/remove/reorder phases. Pipeline is a value.
    """
    return (
        PreloadPhase(),
        EvolutionFoldPhase(),
        EvolutionMemoryFoldPhase(),
        CognitiveFoldPhase(),
        LifeFoldPhase(),
        ConversationPhase(provider=provider, trace=trace),
        DelegationCollectPhase(),
        LearnPhase(),
        StateAdvancePhase(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Life — the eternal fold
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class Life:
    """Life = list[LifeCapability] folded into LifeContext.

    Caller builds the capability list. Life just folds and runs.
    """

    capabilities: tuple[LifeCapability, ...]
    memory_dir: Path

    async def run(
        self,
        provider: ClaudeProvider,
        *,
        max_ticks: int = 0,
        tick_delay: float = 0.0,
        trace: TickTraceCollector | None = None,
    ) -> AgentState:
        """Eternal fold — until ctrl+c (or max_ticks if set)."""
        log_path = self.memory_dir / "trace.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_collector = FileCollector(log_path)
        if trace is not None:
            active_trace: TickTraceCollector = MultiCollector([trace, file_collector])
        else:
            active_trace = MultiCollector([ConsoleCollector(), file_collector])

        state = await _load_state(self.memory_dir)

        print(f"\n🌱 Life begins — tick {state.tick}")
        print(f"   📁 memory: {self.memory_dir}")
        print(f"   📝 trace:  {log_path}\n")

        tick_count = 0
        try:
            while max_ticks == 0 or tick_count < max_ticks:
                print(f"{'─' * 60}")
                print(f"🕐 Tick {state.tick}")
                print(f"{'─' * 60}")

                state = await self._tick(state, provider, active_trace)
                await _save_state(state, self.memory_dir)

                print(f"  ✅ tick {state.tick - 1} complete\n")
                tick_count += 1
                if tick_delay > 0 and (max_ticks == 0 or tick_count < max_ticks):
                    print(f"  💤 sleeping {tick_delay:.0f}s...")
                    await asyncio.sleep(tick_delay)
        except (KeyboardInterrupt, SystemExit):
            print("\n⏸️  interrupted — saving state...")
            await _save_state(state, self.memory_dir)
            print("  💾 state saved")

        file_collector.close()
        print(f"\n🌙 Life paused — tick {state.tick}")
        return state

    async def _tick(
        self,
        state: AgentState,
        provider: ClaudeProvider,
        trace: TickTraceCollector,
    ) -> AgentState:
        """One tick — full pipeline via async_fold_tick.

        Uses standard_tick pipeline: Preload → Evolution → EvolutionMemory →
        Cognitive → Life → Conversation → Learn → StateAdvance.
        """
        phases = standard_tick(provider, trace)

        # Auto-detect InnerLife → insert InnerLifePhase before LifeFoldPhase
        from mmkr.inner_life import InnerLife, InnerLifePhase
        if any(isinstance(cap, InnerLife) for cap in self.capabilities):
            idx = next(
                (i for i, p in enumerate(phases) if isinstance(p, LifeFoldPhase)),
                len(phases),
            )
            phases = (*phases[:idx], InnerLifePhase(), *phases[idx:])

        # Auto-detect TelegramAccess → insert TelegramNotifyPhase before ConversationPhase
        from mmkr.telegram import TelegramAccess, TelegramNotifyPhase
        if any(isinstance(cap, TelegramAccess) for cap in self.capabilities):
            idx = next(
                (i for i, p in enumerate(phases) if isinstance(p, ConversationPhase)),
                len(phases),
            )
            phases = (*phases[:idx], TelegramNotifyPhase(), *phases[idx:])

        # Auto-detect ConversationLog → insert ConversationSavePhase after ConversationPhase
        for cap in self.capabilities:
            if isinstance(cap, ConversationLog):
                cap._provider_ref[0] = provider
                idx = next(
                    (i for i, p in enumerate(phases) if isinstance(p, ConversationPhase)),
                    len(phases),
                )
                phases = (*phases[:idx + 1], ConversationSavePhase(), *phases[idx + 1:])
                break

        # Auto-detect GitBrain → append episodic write + git commit
        from mmkr.git_brain import EpisodicWritePhase, GitBrain, GitCommitPhase
        if any(isinstance(cap, GitBrain) for cap in self.capabilities):
            phases = (*phases, EpisodicWritePhase(), GitCommitPhase())

        ctx = TickContext(
            state=state,
            capabilities=self.capabilities,
        )
        result = await async_fold_tick(phases, ctx)

        if result.skipped:
            print(f"  ⏭️  skip_tick: {result.skip_reason}")

        return result.state


# ═══════════════════════════════════════════════════════════════════════════════
# State persistence
# ═══════════════════════════════════════════════════════════════════════════════


def _format_tick_state(state: AgentState) -> str:
    parts = [f"Tick: {state.tick}"]
    if state.verify_phases:
        names = [vp.name for vp in state.verify_phases[:5]]
        parts.append(f"Verify phases: {', '.join(names)}")
    if state.active_derivation_specs:
        specs = list(state.active_derivation_specs[:5])
        parts.append(f"Active derivations: {', '.join(specs)}")
    if state.beliefs:
        parts.append("Beliefs: " + ", ".join(
            f"{k}={v:.2f}" for k, v in state.beliefs[:10]
        ))
    return "\n".join(parts)


def _reconstruct_state(d: Mapping[str, int | float | str | bool | None | list[list[float]] | list[dict[str, str]] | list[str]]) -> AgentState:
    """Reconstruct AgentState from parsed JSON. Boundary code — JSON types are untyped."""
    tick = d.get("tick", 0)
    beliefs_raw = d.get("beliefs", ())
    vp_raw = d.get("verify_phases", ())
    specs_raw = d.get("active_derivation_specs", ())
    goals_raw = d.get("goals", ())
    access_log_raw = d.get("memory_access_log", ())
    plans_raw = d.get("plans", ())
    resources_raw = d.get("resources", ())
    tasks_raw = d.get("tasks", ())
    return AgentState(
        tick=int(tick) if isinstance(tick, (int, float)) else 0,
        beliefs=tuple(tuple(float(x) for x in b) for b in beliefs_raw) if beliefs_raw else (),
        verify_phases=tuple(VerifyPhaseSpec(**v) for v in vp_raw) if vp_raw else (),
        active_derivation_specs=tuple(str(s) for s in specs_raw) if specs_raw else (),
        goals=tuple(GoalSpec(**g) for g in goals_raw) if goals_raw else (),
        memory_access_log=tuple(MemoryAccessMeta(**a) for a in access_log_raw) if access_log_raw else (),
        plans=tuple(
            PlanSpec(
                goal_name=p["goal_name"],
                steps=tuple(PlanStep(**s) for s in p.get("steps", ())) if p.get("steps") else (),
                status=p.get("status", "active"),
                created_tick=p.get("created_tick", 0),
            )
            for p in plans_raw
        ) if plans_raw else (),
        resources=tuple(ResourceSpec(**r) for r in resources_raw) if resources_raw else (),
        tasks=tuple(TaskSpec(**t) for t in tasks_raw) if tasks_raw else (),
    )


def _recover_tick_from_inner_state(memory_dir: Path) -> int:
    """Fallback: recover tick counter from inner_state.json when state.json is missing.

    This happens when the agent was stopped (docker stop / SIGTERM) mid-tick
    before _save_state could run.
    """
    inner_path = memory_dir / "inner_state.json"
    if not inner_path.exists():
        return 0
    try:
        with open(inner_path, "rb") as f:
            data = json.loads(f.read().decode("utf-8"))
        tick = data.get("tick", 0)
        return int(tick) + 1 if isinstance(tick, (int, float)) else 0
    except (json.JSONDecodeError, OSError):
        return 0


async def _load_state(memory_dir: Path) -> AgentState:
    memory_dir.mkdir(parents=True, exist_ok=True)
    backend: FileStorage[str, bytes] = FileStorage(str(memory_dir / "state.json"))
    state_kv: KV[AgentState, Never] = KV(backend, DataclassJsonCodec[AgentState]())
    result = await state_kv.get("state")
    match result:
        case Ok(Some(raw)):
            return _reconstruct_state(raw)
        case _:
            recovered_tick = _recover_tick_from_inner_state(memory_dir)
            if recovered_tick > 0:
                return AgentState(tick=recovered_tick)
            return AgentState()


async def _save_state(state: AgentState, memory_dir: Path) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    backend: FileStorage[str, bytes] = FileStorage(str(memory_dir / "state.json"))
    state_kv: KV[AgentState, Never] = KV(backend, DataclassJsonCodec[AgentState]())
    await state_kv.set("state", state)
