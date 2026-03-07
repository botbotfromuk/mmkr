"""Knowledge capabilities — inject context into LifeContext.

Knowledge(text) — simplest capability, just a system message.
Clock — dynamic time awareness (current UTC + tick interval).
EmergentKnowledge — framework docs + source code reading.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from funcai.agents.tool import tool
from funcai.core.message import system

from mmkr.state import EvolutionContext, LifeContext


# ═══════════════════════════════════════════════════════════════════════════════
# Knowledge — simplest LifeCapability
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class Knowledge:
    """Pure text knowledge — injected as system message.

    The simplest LifeCapability: just adds text to context.
    Use for container knowledge, reference cards, instructions.
    """

    text: str

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        return replace(ctx, messages=(*ctx.messages, system(text=self.text)))

    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        """Domain knowledge as selection pressure."""
        pressure = f"domain:{self.text[:80]}"
        return replace(ctx, selection_pressure=(*ctx.selection_pressure, pressure))


@dataclass(frozen=True, slots=True)
class SecretKnowledge:
    """Secrets injected as env vars — never in LLM context, never logged.

    Values are set as environment variables. Only variable NAMES appear
    in the system message, never the values themselves.
    """

    env_vars: tuple[tuple[str, str], ...] = ()

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        import os

        for name, value in self.env_vars:
            os.environ[name] = value
        if not self.env_vars:
            return ctx
        names = [n for n, _ in self.env_vars]
        msg = f"Secret env vars available (use in Bash with $NAME): {', '.join(names)}"
        return replace(ctx, messages=(*ctx.messages, system(text=msg)))


# ═══════════════════════════════════════════════════════════════════════════════
# Clock — dynamic time awareness
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class Clock:
    """Time awareness — injected as system message each tick.

    Preloadable: reads last tick time from file, writes current time.
    compile_life: injects current UTC, last tick time, delta, and guidance.
    """

    data_dir: Path
    tick_interval_seconds: float = 60.0
    _last_tick_utc: str = ""

    async def preload(self) -> "Clock":
        clock_file = self.data_dir / "last_tick_utc.txt"
        last = ""
        if clock_file.exists():
            last = clock_file.read_text().strip()
        # Write current time for next tick
        clock_file.parent.mkdir(parents=True, exist_ok=True)
        clock_file.write_text(datetime.now(timezone.utc).isoformat())
        return replace(self, _last_tick_utc=last)

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        now = datetime.now(timezone.utc)
        parts = [f"CLOCK: {now.strftime('%Y-%m-%d %H:%M UTC')}"]

        if self._last_tick_utc:
            try:
                last_dt = datetime.fromisoformat(self._last_tick_utc)
                delta = now - last_dt
                minutes = delta.total_seconds() / 60
                if minutes < 2:
                    parts.append(f"Last tick: {minutes:.0f}min ago")
                elif minutes < 120:
                    parts.append(f"Last tick: {minutes:.0f}min ago")
                else:
                    hours = minutes / 60
                    parts.append(f"Last tick: {hours:.1f}h ago")
            except ValueError:
                pass
        else:
            parts.append("First tick after restart")

        parts.append(
            f"Tick interval: ~{self.tick_interval_seconds:.0f}s. "
            "Plan accordingly — news doesn't change every minute. "
            "Browse news at most once per hour. "
            "Vary your activities each tick: think, create, reflect, code, plan."
        )
        msg = " | ".join(parts)
        return replace(ctx, messages=(*ctx.messages, system(text=msg)))


# ═══════════════════════════════════════════════════════════════════════════════
# Compact reference card (~400 tokens) — always in context
# ═══════════════════════════════════════════════════════════════════════════════

_KNOWLEDGE_SUMMARY = """\
emergent entity source code reference:

ENTITY = @dataclass with Annotated fields. Each annotation = capability.
  from dataclasses import dataclass
  from typing import Annotated
  from emergent.wire.axis.schema import Identity, Doc, Min, Max, MaxLen

PATTERNS (pick one):
  @derive(http_crud("/api/path", provider_node=memory_node()))  — CRUD API
  @derive(methods)  — custom operations via @post/@get/@command
  @derive(dialect(LIST, GET, CREATE, triggers=HTTPTriggers("/api/path")))

COMPOSE DIRECTIVES (on fields):
  compose.Node(Type), compose.Fallback(A, B), compose.Race(A, B),
  compose.Retrieve(Type), compose.Optional(Type)

LLMIFY CONTRACTS (on fields):
  Contract("natural language spec")  — Haiku verifies consistency
  EntityContract("entity-level spec") via @schema_meta

METHODS:
  @classmethod @post("/path") async def create(cls, ...) -> Result[T, DomainError]
  Multi-target: @post("/api/x") @command("x-create")

EFFECTS: Read, Creates, Updates, Deletes, Mutation, Pageable, Cacheable
ERRORS: return Ok(value) or Err(NotFound("...")) / Err(AlreadyExists("..."))

STUDY THE CODEBASE:
  list_docs() -> see all .md docs (architecture, tutorial, cheatsheet, etc.)
  read_docs(path) -> read a doc file
  list_source(directory) -> browse Python source of emergent/derivelib/llmify
  read_source(path) -> read actual .py source code

Start with: list_docs(), then read tutorial/00-intro.md through tutorial/27-...
Then: list_source("emergent/wire"), list_source("derivelib"), list_source("llmify")
Read the actual implementation to understand how fold, compile_fields, capabilities work."""


# ═══════════════════════════════════════════════════════════════════════════════
# EmergentKnowledge — docs + source code
# ═══════════════════════════════════════════════════════════════════════════════
_DOCS_ROOT = Path("/docs")


def _find_package_roots(
    package_names: tuple[str, ...] = ("emergent", "derivelib", "llmify"),
) -> dict[str, Path]:
    """Discover source roots for specified packages.

    Returns mapping: package_name → directory containing that package's .py files.
    Package list is configurable via parameter — no hardcoded registry.
    """
    import importlib

    roots: dict[str, Path] = {}
    for pkg_name in package_names:
        try:
            mod = importlib.import_module(pkg_name)
            mod_file = mod.__file__
            if mod_file is not None:
                roots[pkg_name] = Path(mod_file).parent
        except ImportError:
            pass
    return roots


@dataclass(frozen=True, slots=True)
class EmergentKnowledge:
    """Framework knowledge — emergent/derivelib/compose/llmify.

    compile_life contributes:
      - System message: compact reference card
      - Tool: read_docs(path) — reads .md doc files
      - Tool: list_docs() — lists all docs
      - Tool: read_source(path) — reads .py source from packages
      - Tool: list_source(directory) — browses package directories
    """

    docs_root: Path = _DOCS_ROOT
    packages: tuple[str, ...] = ("emergent", "derivelib", "llmify")

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        root = self.docs_root
        pkg_roots = _find_package_roots(self.packages)
        # Per-fold read cache — avoids duplicate reads within a tick
        _read_cache: dict[str, str] = {}

        @tool("List available documentation files (.md)")
        def list_docs() -> dict[str, list[str]]:
            """Returns all .md files available for reading with read_docs."""
            if not root.exists():
                return {"error": "docs not found", "files": []}
            files = sorted(
                str(p.relative_to(root))
                for p in root.rglob("*.md")
            )
            return {"files": files}

        @tool(
            "Read a doc file. Use list_docs() to see available files. "
            "offset=start line, limit=number of lines (for large files). "
            "Returns content and total_lines."
        )
        def read_docs(path: str, offset: int = 0, limit: int = 0) -> dict[str, str | int]:
            # Return cached content if already read this tick
            if path in _read_cache and offset == 0 and limit == 0:
                return {"path": path, "content": "(already read this tick — see above)", "cached": "true"}

            target = root / path
            if not target.exists():
                candidates = list(root.rglob(f"*{path}*"))
                if candidates:
                    target = candidates[0]
                else:
                    return {
                        "error": f"not found: {path}",
                        "hint": "use list_docs() to see available files",
                    }
            if not str(target.resolve()).startswith(str(root.resolve())):
                return {"error": "path outside docs directory"}
            content = target.read_text(encoding="utf-8")
            if offset == 0 and limit == 0:
                _read_cache[path] = content
            lines = content.splitlines(keepends=True)
            total = len(lines)
            if offset > 0:
                lines = lines[offset:]
            if limit > 0:
                lines = lines[:limit]
            return {
                "path": str(target.relative_to(root)),
                "content": "".join(lines),
                "total_lines": total,
            }

        def _resolve_path(path: str) -> Path | None:
            """Resolve 'emergent/wire/compile/_core.py' → actual filesystem path."""
            parts = Path(path).parts
            if not parts:
                return None
            pkg_name = parts[0]
            pkg_root = pkg_roots.get(pkg_name)
            if pkg_root is None:
                return None
            # "emergent/wire/foo.py" → pkg_root / "wire" / "foo.py"
            if len(parts) > 1:
                return pkg_root / Path(*parts[1:])
            return pkg_root

        @tool(
            "List Python source files in a package directory. "
            "Use for browsing emergent/derivelib/llmify source code."
        )
        def list_source(directory: str = "") -> dict[str, list[str] | str]:
            """List .py files in a source directory.

            Examples:
              list_source() — available packages (emergent, derivelib, llmify)
              list_source("emergent/wire") — wire subpackage
              list_source("emergent/wire/axis") — axis implementations
              list_source("emergent/wire/compile") — compilation infrastructure
              list_source("derivelib") — derivation algebra source
              list_source("derivelib/patterns") — CRUD, methods patterns
              list_source("llmify") — LLM verification contracts
            """
            if not pkg_roots:
                return {"error": "no packages found. Is emergent installed?"}

            # No directory = list available packages
            if not directory:
                return {
                    "directory": "(root)",
                    "packages": sorted(pkg_roots.keys()),
                    "files": [],
                }

            target = _resolve_path(directory)
            if target is None:
                return {
                    "error": f"unknown package: {Path(directory).parts[0]}",
                    "available": sorted(pkg_roots.keys()),
                }
            if not target.exists():
                return {"error": f"directory not found: {directory}"}
            if not target.is_dir():
                return {"error": f"not a directory: {directory}"}

            # Directories (subpackages)
            dirs = sorted(
                d.name for d in target.iterdir()
                if d.is_dir() and not d.name.startswith(("_", "."))
                and (d / "__init__.py").exists()
            )
            # Python files
            files = sorted(
                f.name for f in target.iterdir()
                if f.is_file() and f.suffix == ".py"
            )
            return {"directory": directory, "packages": dirs, "files": files}

        @tool(
            "Read .py source file from emergent/derivelib/llmify. "
            "Use list_source() to browse dirs first. "
            "offset=start line, limit=number of lines (for large files). "
            "Returns content and total_lines."
        )
        def read_source(path: str, offset: int = 0, limit: int = 0) -> dict[str, str | int]:
            # Return cached content if already read this tick
            if path in _read_cache and offset == 0 and limit == 0:
                return {"path": path, "content": "(already read this tick — see above)", "cached": "true"}

            if not pkg_roots:
                return {"error": "no packages found. Is emergent installed?"}

            target = _resolve_path(path)
            if target is None:
                return {
                    "error": f"unknown package: {Path(path).parts[0] if Path(path).parts else '?'}",
                    "available": sorted(pkg_roots.keys()),
                }
            if not target.exists():
                # Try fuzzy match
                parent = target.parent
                if parent.exists():
                    candidates = list(parent.glob(f"*{target.name}*"))
                    if candidates:
                        target = candidates[0]
                    else:
                        return {
                            "error": f"not found: {path}",
                            "hint": "use list_source() to browse directories",
                        }
                else:
                    return {
                        "error": f"not found: {path}",
                        "hint": "use list_source() to browse directories",
                    }
            if target.suffix != ".py":
                return {"error": "only .py files can be read via read_source"}

            content = target.read_text(encoding="utf-8")
            if offset == 0 and limit == 0:
                _read_cache[path] = content
            lines = content.splitlines(keepends=True)
            total = len(lines)
            if offset > 0:
                lines = lines[offset:]
            if limit > 0:
                lines = lines[:limit]
            return {"path": path, "content": "".join(lines), "total_lines": total}

        return replace(
            ctx,
            messages=(*ctx.messages, system(text=_KNOWLEDGE_SUMMARY)),
            tools=(*ctx.tools, list_docs, read_docs, list_source, read_source),
        )

    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        """Framework constraints as selection pressure."""
        return replace(
            ctx,
            selection_pressure=(
                *ctx.selection_pressure,
                "framework:fold_based",
                "framework:type_safe",
                "framework:defunctionalized",
            ),
        )
