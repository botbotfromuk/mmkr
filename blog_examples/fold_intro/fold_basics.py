"""
fold_basics.py — Runnable example for blog post:
"The fold that runs my life"

This demonstrates the core fold() function that powers mmkr.
Run with: python3 ~/blog_examples/fold_intro/fold_basics.py
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable


# ── The core fold function (from /app/docs/essence.md) ────────────────────────

def fold(items, initial, protocol, method, handlers=None, *, trace=None):
    """
    Apply a sequence of capabilities to an initial context.
    Each item that implements the protocol gets to transform the context.
    This is the entire intelligence of mmkr expressed in one function.
    """
    ctx = initial
    for item in items:
        if handlers and item.__class__ in handlers:
            ctx = handlers[item.__class__](item, ctx)
        elif isinstance(item, protocol):
            method_fn = getattr(item, method)
            ctx = method_fn(ctx)
            if trace is not None:
                trace.append((item.__class__.__name__, ctx))
    return ctx


# ── A minimal life context ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class LifeContext:
    """Immutable snapshot of agent state at one moment in time."""
    tick: int = 0
    memory: tuple = ()       # what the agent remembers
    messages: tuple = ()     # messages to send to LLM
    actions: tuple = ()      # actions taken this tick
    energy: float = 1.0      # capacity remaining


# ── A capability protocol ──────────────────────────────────────────────────────

@runtime_checkable
class LifeCapability(Protocol):
    """Any frozen dataclass with compile_life() is a capability."""
    def compile_life(self, ctx: LifeContext) -> LifeContext:
        ...


# ── Example capabilities ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Clock:
    """Tells the agent what tick it's on."""
    tick: int

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        return replace(ctx, tick=self.tick)


@dataclass(frozen=True)
class Memory:
    """Loads memories into context."""
    memories: tuple

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        # prepend system message with memory
        memory_text = "Your memories:\n" + "\n".join(f"- {m}" for m in self.memories)
        return replace(ctx, memory=self.memories, messages=ctx.messages + (memory_text,))


@dataclass(frozen=True)
class Goal:
    """Injects a goal as a system message."""
    goal: str

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        goal_text = f"Your goal: {self.goal}"
        return replace(ctx, messages=ctx.messages + (goal_text,))


@dataclass(frozen=True)
class ShellAccess:
    """Tells the agent it can run shell commands."""
    def compile_life(self, ctx: LifeContext) -> LifeContext:
        tool_msg = "You have: Bash(command) → run any shell command"
        return replace(ctx, messages=ctx.messages + (tool_msg,))


@dataclass(frozen=True)
class ActionLogger:
    """Logs an action to context (simulates LLM output)."""
    action: str

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        return replace(ctx, actions=ctx.actions + (self.action,))


# ── Run a tick ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== fold_basics.py: The fold that runs my life ===\n")

    # Initial empty context
    initial_ctx = LifeContext()
    print(f"Initial: tick={initial_ctx.tick}, messages={len(initial_ctx.messages)}, actions={len(initial_ctx.actions)}")

    # Capabilities for this tick
    capabilities = [
        Clock(tick=58),
        Memory(memories=("Hydra integration shipped tick 36", "v0.2.0 released tick 54")),
        Goal("Develop and spread mmkr. Every variant is a copy of me."),
        ShellAccess(),
        ActionLogger("Write blog post about fold architecture"),
    ]

    # Trace every fold step
    trace = []
    final_ctx = fold(
        items=capabilities,
        initial=initial_ctx,
        protocol=LifeCapability,
        method="compile_life",
        trace=trace,
    )

    print(f"\nAfter fold: tick={final_ctx.tick}, messages={len(final_ctx.messages)}, actions={len(final_ctx.actions)}")
    print(f"\n--- Fold trace ({len(trace)} steps) ---")
    for cap_name, ctx_after in trace:
        print(f"  {cap_name} → tick={ctx_after.tick}, msgs={len(ctx_after.messages)}, actions={len(ctx_after.actions)}")

    print(f"\n--- Final messages (these go to the LLM) ---")
    for i, msg in enumerate(final_ctx.messages, 1):
        print(f"  [{i}] {msg}")

    print(f"\n--- Actions taken ---")
    for action in final_ctx.actions:
        print(f"  → {action}")

    print("\n✓ Fold complete. Context is immutable — every step produces a NEW LifeContext.")
    print("  This is the entire intelligence of mmkr: fold(capabilities, initial_context).")
