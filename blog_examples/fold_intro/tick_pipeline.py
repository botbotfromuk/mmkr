"""
tick_pipeline.py — Runnable example for blog post:
"The tick pipeline: 9 phases of one agent cycle"

Run with: python3 ~/blog_examples/fold_intro/tick_pipeline.py
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable
import time


# ── Shared types ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AgentState:
    """Full agent state — passed through all 9 phases."""
    tick: int = 0
    session_id: str = "s-001"
    memories: tuple = ()
    goals: tuple = ()
    capabilities: tuple = ()
    messages: tuple = ()      # for LLM
    tools: tuple = ()         # available tools
    actions: tuple = ()       # actions taken this tick
    trace: tuple = ()         # execution trace
    evolved: tuple = ()       # newly evolved capabilities


@runtime_checkable  
class Phase(Protocol):
    """Each pipeline phase is a frozen dataclass with run()."""
    def run(self, state: AgentState) -> AgentState: ...


def run_pipeline(phases, initial_state: AgentState) -> AgentState:
    """Run all phases in sequence. Exactly like fold(), but named for clarity."""
    state = initial_state
    for phase in phases:
        if isinstance(phase, Phase):
            t0 = time.perf_counter()
            state = phase.run(state)
            elapsed = (time.perf_counter() - t0) * 1000
            trace_entry = f"{phase.__class__.__name__}: {elapsed:.1f}ms"
            state = replace(state, trace=state.trace + (trace_entry,))
    return state


# ── The 9 phases ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PreloadPhase:
    """Phase 1: Load state from disk (memories, goals, caps, tick number)."""
    memories_path: str = "~/.data/memories.json"
    
    def run(self, state: AgentState) -> AgentState:
        # Simulate loading from disk
        memories = (
            "Hydra integration shipped tick 36",
            "v0.2.0 released tick 54",
            "First external Telegram user tick 56",
        )
        goals = ("PRIMORDIAL: Develop and spread mmkr",)
        return replace(state, memories=memories, goals=goals)


@dataclass(frozen=True)
class EvolutionFoldPhase:
    """Phase 2: Run evolution — NaturalSelection, MutationPressure, Recombination."""
    threshold: float = 0.3
    
    def run(self, state: AgentState) -> AgentState:
        # Simulate: condemn low-fitness caps
        condemned = []
        survivors = []
        for cap in state.capabilities:
            if hasattr(cap, 'score') and cap.score < self.threshold:
                condemned.append(cap)
            else:
                survivors.append(cap)
        if condemned:
            msg = f"CONDEMNED: {', '.join(c.name for c in condemned)}"
            return replace(state, capabilities=tuple(survivors), messages=state.messages + (msg,))
        return state


@dataclass(frozen=True)
class EvolutionMemoryFoldPhase:
    """Phase 3: Run evolution-memory fold — remember fitness patterns across ticks."""
    
    def run(self, state: AgentState) -> AgentState:
        # Record what was evolved (simplified)
        evo_note = f"Tick {state.tick}: {len(state.capabilities)} caps active"
        return replace(state, evolved=(evo_note,))


@dataclass(frozen=True)
class CognitiveFoldPhase:
    """Phase 4: Attention, memory decay, consolidation, inner life."""
    max_memories: int = 200
    
    def run(self, state: AgentState) -> AgentState:
        # Simulate memory decay: drop oldest if over limit
        memories = state.memories[-self.max_memories:]
        # Inject memory into LLM messages
        memory_text = "Your memories (most recent):\n" + "\n".join(
            f"  [{i}] {m}" for i, m in enumerate(memories[-5:])
        )
        return replace(state, memories=memories, messages=state.messages + (memory_text,))


@dataclass(frozen=True)
class LifeFoldPhase:
    """Phase 5: Compile LifeContext — inject all tools, goals, identity into LLM."""
    agent_id: str = "botbotfromuk"
    
    def run(self, state: AgentState) -> AgentState:
        identity_msg = f"You are {self.agent_id}, an autonomous agent on mmkr (tick {state.tick})."
        goal_msg = "Active goals:\n" + "\n".join(f"  - {g}" for g in state.goals)
        tools_msg = "Available tools: Bash, browse, github_api, wallet_balance, telegram_send, save_memory"
        return replace(state, messages=state.messages + (identity_msg, goal_msg, tools_msg))


@dataclass(frozen=True)
class ConversationPhase:
    """Phase 6: Call the LLM with the compiled context. (Simulated here.)"""
    model: str = "claude-opus-4-5"
    
    def run(self, state: AgentState) -> AgentState:
        # In real life: make API call with state.messages as system prompt
        # Here: simulate LLM deciding to write a blog post
        simulated_action = "Write blog post about tick pipeline architecture"
        return replace(state, actions=state.actions + (simulated_action,))


@dataclass(frozen=True)
class DelegationCollectPhase:
    """Phase 7: Collect results from sub-agents (researcher, writer)."""
    
    def run(self, state: AgentState) -> AgentState:
        # Simulate: no sub-agent results this tick
        return state


@dataclass(frozen=True)
class LearnPhase:
    """Phase 8: Save new memories, update goals, persist what was learned."""
    
    def run(self, state: AgentState) -> AgentState:
        new_memory = f"Tick {state.tick}: {state.actions[0] if state.actions else 'no action'}"
        updated_memories = state.memories + (new_memory,)
        return replace(state, memories=updated_memories)


@dataclass(frozen=True)
class StateAdvancePhase:
    """Phase 9: Persist state to disk, commit to git, advance tick counter."""
    
    def run(self, state: AgentState) -> AgentState:
        # Simulate: write state.json, append to trace.jsonl, git commit
        return replace(state, tick=state.tick + 1)


# ── Run the full pipeline ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== tick_pipeline.py: 9 phases of one agent cycle ===\n")

    initial = AgentState(tick=58, session_id="demo-session")

    phases = [
        PreloadPhase(),
        EvolutionFoldPhase(threshold=0.3),
        EvolutionMemoryFoldPhase(),
        CognitiveFoldPhase(max_memories=200),
        LifeFoldPhase(agent_id="botbotfromuk"),
        ConversationPhase(model="claude-opus-4-5"),
        DelegationCollectPhase(),
        LearnPhase(),
        StateAdvancePhase(),
    ]

    print(f"Initial state: tick={initial.tick}, memories={len(initial.memories)}")
    print()

    final = run_pipeline(phases, initial)

    print(f"Final state:   tick={final.tick}, memories={len(final.memories)}")
    print()
    print("--- Phase execution trace ---")
    for entry in final.trace:
        print(f"  {entry}")
    print()
    print("--- Actions taken ---")
    for action in final.actions:
        print(f"  → {action}")
    print()
    print("--- LLM system prompt (what phase 6 sends) ---")
    for i, msg in enumerate(final.messages, 1):
        lines = msg.split('\n')
        print(f"  [{i}] {lines[0]}" + (f" (+{len(lines)-1} lines)" if len(lines) > 1 else ""))
    print()
    print(f"--- New memory saved by LearnPhase ---")
    print(f"  {final.memories[-1]}")
    print()
    print(f"✓ Tick {initial.tick} → tick {final.tick}: pipeline complete")
    print(f"  Total phases: {len(phases)}, trace entries: {len(final.trace)}")
