"""Minimal mmkr agent — hello world for autonomous life.

This is the simplest possible mmkr agent:
  - Shell access (read files, run commands)
  - Persistent memory (remembers across ticks)
  - A seed task (what to do each tick)
  - No economic pillar, no Telegram, no browser

Run:
    ANTHROPIC_API_KEY=sk-ant-... uv run python examples/minimal_agent.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from llmify import make_claude_provider

from mmkr import Life, ShellAccess, Seed, Clock, Knowledge
from mmkr.life import PersistentMemory


async def main() -> None:
    api_key = os.environ["ANTHROPIC_API_KEY"]
    provider = make_claude_provider(api_key, model="claude-sonnet-4-5")

    data_dir = Path(".data/minimal")
    data_dir.mkdir(parents=True, exist_ok=True)

    life = Life(
        capabilities=(
            ShellAccess(),
            PersistentMemory(memory_dir=data_dir),
            Clock(),
            Knowledge(text=(
                "You are a minimal autonomous agent. You live in ticks.\n"
                "Each tick: observe state, decide ONE action, execute it, remember outcome.\n"
                "Start by exploring what's in the current directory and setting a goal."
            )),
            Seed(text="This is tick {tick}. Observe. Decide. Act. Remember."),
        ),
        provider=provider,
        tick_interval_seconds=60,
    )

    print("Starting minimal agent. Ctrl+C to stop.")
    await life.run()


if __name__ == "__main__":
    asyncio.run(main())
