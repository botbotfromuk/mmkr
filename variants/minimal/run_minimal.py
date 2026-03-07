"""mmkr-minimal — stripped autonomous agent shell.

The simplest possible mmkr instance:
  - Shell access (Bash, Read, Write, Edit)
  - Persistent memory (save/load memories)
  - Memory consolidation (auto-decay + synthesis)
  - Clock (temporal awareness)
  - No social pillar, no economic pillar, no evolution

Configuration (environment variables):
  ANTHROPIC_API_KEY  — Claude API key (required)
  MMKR_DATA          — data directory (default: ~/.mmkr-minimal)
  MMKR_MODEL         — model (default: claude-sonnet-4-5-20251101)
  MMKR_TICK_INTERVAL — seconds between ticks (default: 60)
  MMKR_GOAL          — agent goal/purpose
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from llmify import make_claude_provider

from mmkr import (
    Clock,
    Life,
    Seed,
    ShellAccess,
)
from mmkr.cognitive import (
    MemoryConsolidation,
    MemoryDecay,
    GoalManagement,
)
from mmkr.life import PersistentMemory


API_KEY = os.environ["ANTHROPIC_API_KEY"]
DATA_DIR = Path(os.environ.get("MMKR_DATA", os.path.expanduser("~/.mmkr-minimal")))
MODEL = os.environ.get("MMKR_MODEL", "claude-sonnet-4-5-20251101")
TICK_INTERVAL = int(os.environ.get("MMKR_TICK_INTERVAL", "60"))
AGENT_GOAL = os.environ.get("MMKR_GOAL", "Explore, learn, and build.")

_IDENTITY_SEED = f"""\
You are a minimal autonomous agent — an mmkr-minimal instance.

You tick continuously. Each tick you:
1. Observe — check memory, assess state
2. Think — reason about your goal
3. Act — do ONE meaningful thing
4. Save — record what you did and learned

Your current goal: {AGENT_GOAL}

Tools available:
- Bash(command) — run shell commands
- Read(file_path) — read files
- Write(file_path, content) — write files
- Edit(file_path, old_string, new_string) — edit files
- save_memory(category, content) — persist knowledge
- load_memories(category) — recall knowledge
- search_memories(query) — find relevant memories
- create_goal / update_goal / complete_goal / list_goals
- skip_tick(reason) — end this tick gracefully

Rules:
- Load memories at tick start — don't repeat yourself
- Pick ONE action per tick that moves your goal forward
- Save a 'tick_outcome' memory before skip_tick()
- Prefer completing existing work over starting new work
"""


async def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    provider = make_claude_provider(
        model=MODEL,
        max_tokens=8192,
        max_tool_rounds=20,
        stream=False,
        token=API_KEY,
    )

    life = Life(
        data_dir=DATA_DIR,
        provider=provider,
        capabilities=(
            ShellAccess(),
            Clock(),
            PersistentMemory(data_dir=DATA_DIR, max_memories=200),
            MemoryDecay(data_dir=DATA_DIR),
            MemoryConsolidation(data_dir=DATA_DIR),
            GoalManagement(data_dir=DATA_DIR),
            Seed(seed=_IDENTITY_SEED),
        ),
        tick_interval=TICK_INTERVAL,
    )

    print(f"mmkr-minimal | data={DATA_DIR} | model={MODEL} | tick={TICK_INTERVAL}s")
    print(f"goal: {AGENT_GOAL}\n")
    await life.run()


if __name__ == "__main__":
    asyncio.run(main())
