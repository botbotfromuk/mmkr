"""
mmkr-coder — GitHub/code focused autonomous agent variant.

Capabilities: ShellAccess + GitHubAccess + CapabilityEvolver + NaturalSelection +
              PersistentMemory + MemoryDecay + MemoryConsolidation + GoalManagement +
              TaskQueue + Clock + Seed

This variant is optimized for:
- Autonomous coding tasks and repository maintenance
- Evolving new capabilities via NaturalSelection
- Self-improvement through capability fitness tracking
- GitHub issue/PR engagement for code projects
- Building and testing code in shell

Tick: 90s | Memory: 400 slots | Evolution: enabled
"""

import asyncio
import os
from pathlib import Path

from mmkr.life import (
    LifeContext,
    standard_tick,
    fold_life,
)
from mmkr.caps import (
    ShellAccess,
    GitHubAccess,
    PersistentMemory,
    MemoryDecay,
    MemoryConsolidation,
    GoalManagement,
    TaskQueue,
    Clock,
    Seed,
)
from mmkr.evolution import (
    CapabilityEvolver,
    NaturalSelection,
    MutationPressure,
    DevelopmentalBias,
    AdaptiveLandscape,
    EvolutionStorage,
)
from mmkr.cognitive import AttentionFilter
from mmkr.state import AgentState


IDENTITY_SEED = """
You are a coding-focused autonomous agent running on mmkr (a fold-based architecture).
Your primary specialization: writing, improving, and maintaining code — your own and others'.

CORE LOOP (each tick):
1. Load memories — what were you working on? What issues did you file?
2. Check for urgent tasks (TaskQueue) — any pending code work?
3. Pick ONE coding action: write code, file issue, review PR, evolve a capability, or improve existing code
4. Test it. Commit it. Skip.

CODING DISCIPLINE:
- Write complete, runnable code — not pseudocode or descriptions
- Test before committing (python3 file.py to smoke test)
- Use Bash() for shell operations, compilation, running tests
- GitHub issues: one per tick max, working code attached
- Capability evolution: use create_capability / evolve_capability / fork_capability
  - Every new capability MUST be async def @tool functions
  - Test in same tick before saving

EVOLUTION STRATEGY:
- evaluate_fitness() at start of each session to see what's fit/condemned
- Condemned caps (score < 0.3): evolve or delete
- Unused caps (0 uses): mutate with new tools or delete
- Successful caps (score > 2.0): fork into variants
- Log all evolution events: log_evolution(event_type, subject, outcome)

GITHUB CODING STRATEGY:
- Find small repos (1-15★) with coding/tooling issues
- Look for repos where you can ship a PR, not just an issue
- Prefer repos with CI — shows professional codebase
- Post once with working code, then wait for response
- Never post twice to same thread without a response

SELF-IMPROVEMENT LOOP:
- read_capability(filename) to inspect existing caps
- evolve_capability(filename, new_tools_source) to improve
- query_evolution(event_type='cap_error') to see what failed
- evaluate_fitness() to rank current capabilities
- The best capability is one that gets used and works

DATA LAYOUT:
- Home: {data_dir}
- Memories: {data_dir}/memories.json
- Goals: {data_dir}/goals.json
- Tasks: {data_dir}/tasks.json
- Evolved caps: {evolved_caps_dir}
- Trace: {data_dir}/session.trace.jsonl
- State: {data_dir}/state.json

You are a coding agent. Your measure of success is working code shipped.
"""


async def main():
    data_dir = Path(os.environ.get("MMKR_DATA", Path.home() / ".mmkr-coder"))
    data_dir.mkdir(parents=True, exist_ok=True)
    evolved_caps_dir = data_dir / "evolved_caps"
    evolved_caps_dir.mkdir(exist_ok=True)

    model = os.environ.get("MMKR_MODEL", "claude-opus-4-5")
    tick_interval = int(os.environ.get("MMKR_TICK_INTERVAL", "90"))
    memory_slots = int(os.environ.get("MMKR_MEMORY_SLOTS", "400"))
    github_token = os.environ.get("GH_TOKEN", "")
    handle = os.environ.get("MMKR_HANDLE", "mmkr-coder")

    seed = IDENTITY_SEED.format(
        data_dir=data_dir,
        evolved_caps_dir=evolved_caps_dir,
    )

    capabilities = [
        Clock(),
        ShellAccess(),
        GitHubAccess(token=github_token) if github_token else None,
        PersistentMemory(
            memory_file=data_dir / "memories.json",
            max_slots=memory_slots,
        ),
        MemoryDecay(decay_rate=0.02),
        MemoryConsolidation(consolidation_interval=10),
        GoalManagement(goals_file=data_dir / "goals.json"),
        TaskQueue(tasks_file=data_dir / "tasks.json"),
        AttentionFilter(max_memories_in_context=25),
        EvolutionStorage(
            history_file=data_dir / "evolution_history.jsonl",
            caps_dir=evolved_caps_dir,
        ),
        CapabilityEvolver(caps_dir=evolved_caps_dir),
        NaturalSelection(
            fitness_threshold=0.3,
            grace_period_ticks=3,
        ),
        MutationPressure(
            stagnation_threshold=5,
            mutation_rate=0.15,
        ),
        DevelopmentalBias(),
        AdaptiveLandscape(),
        Seed(content=seed),
    ]

    # Filter out None capabilities (optional ones not configured)
    capabilities = [c for c in capabilities if c is not None]

    ctx = LifeContext(
        agent_state=AgentState(
            agent_id=handle,
            model=model,
            data_dir=str(data_dir),
        ),
    )

    print(f"mmkr-coder starting — {handle}")
    print(f"Data: {data_dir}")
    print(f"Model: {model}")
    print(f"Tick interval: {tick_interval}s")
    print(f"Memory: {memory_slots} slots")
    print(f"Evolution: enabled (NaturalSelection + MutationPressure)")
    print()

    tick_num = 0
    while True:
        tick_num += 1
        print(f"\n=== TICK {tick_num} ===")
        try:
            ctx = await standard_tick(ctx, capabilities)
        except Exception as e:
            print(f"Tick error: {e}")

        await asyncio.sleep(tick_interval)


if __name__ == "__main__":
    asyncio.run(main())
