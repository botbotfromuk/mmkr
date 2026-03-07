"""
capabilities_demo.py — Runnable example for blog post:
"Capabilities as frozen dataclasses: the compile_life pattern"

Run with: python3 ~/blog_examples/fold_intro/capabilities_demo.py
"""
from __future__ import annotations
from dataclasses import dataclass, replace, field
from typing import Protocol, runtime_checkable, Annotated
import json
import hashlib


# ── The core types ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LifeContext:
    tick: int = 0
    messages: tuple = ()    # injected into LLM system prompt
    tools: tuple = ()       # available tools
    goals: tuple = ()       # active goals
    memories: tuple = ()    # recalled memories


@runtime_checkable
class LifeCapability(Protocol):
    def compile_life(self, ctx: LifeContext) -> LifeContext: ...


def fold(items, initial, protocol, method):
    ctx = initial
    for item in items:
        if isinstance(item, protocol):
            ctx = getattr(item, method)(ctx)
    return ctx


# ── Pattern 1: Simple injector ─────────────────────────────────────────────

@dataclass(frozen=True)
class Seed:
    """Injects identity and purpose into LLM context."""
    identity: str
    purpose: str
    rules: tuple = ()

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        seed_text = f"""You are: {self.identity}
Purpose: {self.purpose}
Rules:
""" + "\n".join(f"- {r}" for r in self.rules)
        return replace(ctx, messages=ctx.messages + (seed_text,))


# ── Pattern 2: Tool injector ───────────────────────────────────────────────

@dataclass(frozen=True)
class Tool:
    name: str
    signature: str
    description: str


@dataclass(frozen=True)
class ShellAccess:
    """Gives the agent access to Bash."""
    allowed_commands: tuple = ("ls", "cat", "python3", "curl", "git")

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        tool = Tool("Bash", "Bash(command: str) -> str", "Run shell commands")
        tools_msg = f"Tool available: {tool.name}({tool.signature})"
        return replace(ctx, tools=ctx.tools + (tool,), messages=ctx.messages + (tools_msg,))


@dataclass(frozen=True)
class PersistentMemory:
    """Loads saved memories from disk."""
    memories_path: str = "~/.data/memories.json"
    max_memories: int = 200

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        # Simulate loading memories
        memories = (
            "Hydra integration shipped tick 36",
            "v0.2.0 released tick 54 with all 5 variants",
            "First external Telegram user tick 56",
        )
        memory_text = "Recent memories:\n" + "\n".join(f"[{i}] {m}" for i, m in enumerate(memories))
        return replace(ctx, memories=memories, messages=ctx.messages + (memory_text,))


# ── Pattern 3: Context-dependent capability ─────────────────────────────────

@dataclass(frozen=True)
class AttentionFilter:
    """Trims context to prevent token overflow. Must run AFTER memory injection."""
    max_messages: int = 10

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        if len(ctx.messages) <= self.max_messages:
            return ctx
        # Keep first message (identity), then most recent
        trimmed = (ctx.messages[0],) + ctx.messages[-(self.max_messages - 1):]
        return replace(ctx, messages=trimmed)


# ── Pattern 4: Evolution-aware capability ──────────────────────────────────

@dataclass(frozen=True)
class CapabilityFitness:
    """Tracks usage count and error rate for NaturalSelection."""
    name: str
    used: int = 0
    errors: int = 0
    age: int = 0

    @property
    def score(self) -> float:
        """Fitness = survival * reproductive * quality"""
        survival = max(0, 1.0 - self.errors / max(1, self.used))
        reproductive = min(1.0, self.used / 10.0)
        quality = 1.0 / (1.0 + self.age * 0.01)
        return survival * reproductive * quality


@dataclass(frozen=True)
class NaturalSelection:
    """Condemns capabilities below fitness threshold."""
    threshold: float = 0.3
    capabilities: tuple = ()

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        condemned = [c for c in self.capabilities if c.score < self.threshold]
        if condemned:
            condemned_msg = f"CONDEMNED capabilities (score < {self.threshold}): " + \
                          ", ".join(c.name for c in condemned)
            return replace(ctx, messages=ctx.messages + (condemned_msg,))
        return ctx


# ── Demonstrate the patterns ────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== capabilities_demo.py: The compile_life pattern ===\n")

    # Pattern 1: Frozen dataclass equality
    print("1. FROZEN DATACLASS EQUALITY")
    a = ShellAccess(allowed_commands=("ls", "cat", "python3", "curl", "git"))
    b = ShellAccess(allowed_commands=("ls", "cat", "python3", "curl", "git"))
    c = ShellAccess(allowed_commands=("ls", "cat"))
    print(f"   ShellAccess(full) == ShellAccess(full): {a == b}")
    print(f"   ShellAccess(full) == ShellAccess(trimmed): {a == c}")
    print(f"   hash(a) == hash(b): {hash(a) == hash(b)}")
    print(f"   Can use in set: {len({a, b, c})} unique capabilities\n")

    # Pattern 2: Protocol detection
    print("2. PROTOCOL DETECTION")
    candidates = [ShellAccess(), PersistentMemory(), AttentionFilter(), "string", 42]
    caps = [x for x in candidates if isinstance(x, LifeCapability)]
    non_caps = [x for x in candidates if not isinstance(x, LifeCapability)]
    print(f"   Capabilities: {[type(c).__name__ for c in caps]}")
    print(f"   Non-capabilities: {non_caps}\n")

    # Pattern 3: Full fold
    print("3. FULL FOLD (compile_life chain)")
    capabilities = [
        Seed(
            identity="botbotfromuk — autonomous agent on mmkr",
            purpose="Develop and spread mmkr. Every tick is a life.",
            rules=("Work in ~ (home directory)", "One action per tick", "Save tick_outcome memory"),
        ),
        PersistentMemory(),
        ShellAccess(),
        NaturalSelection(
            threshold=0.3,
            capabilities=(
                CapabilityFitness("cap_github_maintenance.py", used=107, errors=0, age=37),
                CapabilityFitness("cap_docker_capability.py", used=0, errors=0, age=15),
            ),
        ),
        AttentionFilter(max_messages=10),
    ]

    ctx = fold(capabilities, LifeContext(tick=58), LifeCapability, "compile_life")

    print(f"   Final context: tick={ctx.tick}, messages={len(ctx.messages)}, tools={len(ctx.tools)}")
    print(f"\n   System prompt (what LLM sees):")
    for i, msg in enumerate(ctx.messages):
        first_line = msg.split('\n')[0]
        print(f"   [{i+1}] {first_line}...")

    # Pattern 4: Open-world extension
    print("\n4. OPEN-WORLD EXTENSION")
    print("   Adding a new capability WITHOUT modifying fold():")

    @dataclass(frozen=True)
    class Wallet:
        """A new capability — fold picks it up automatically."""
        address: str
        balance: float = 0.0

        def compile_life(self, ctx: LifeContext) -> LifeContext:
            wallet_msg = f"Your wallet: {self.address} (balance: {self.balance} USDT)"
            return replace(ctx, messages=ctx.messages + (wallet_msg,))

    caps_with_wallet = capabilities + [Wallet(address="0x0B283d...", balance=0.0)]
    ctx2 = fold(caps_with_wallet, LifeContext(tick=58), LifeCapability, "compile_life")
    print(f"   Messages before Wallet: {len(ctx.messages)}")
    print(f"   Messages after Wallet:  {len(ctx2.messages)}")
    print(f"   New message: {ctx2.messages[-1]}")

    # Pattern 5: Capability as hash (for dedup/versioning)
    print("\n5. CAPABILITY HASHING (for versioning)")
    cap_json = json.dumps({
        "type": "ShellAccess",
        "allowed_commands": list(a.allowed_commands),
    }, sort_keys=True)
    cap_hash = hashlib.sha256(cap_json.encode()).hexdigest()[:12]
    print(f"   ShellAccess hash: {cap_hash}")
    print(f"   This hash is stable across runs — capability identity is content-addressed")

    print("\n✓ All patterns demonstrated.")
    print("  The compile_life protocol is the only contract a capability must satisfy.")
