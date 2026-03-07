"""Two-pillar agent — Social + Economic fully configured.

This is the full mmkr configuration as botbotfromuk actually runs:
  - Social: GitHub, Telegram, browser
  - Economic: BSC wallet (BNB Smart Chain, USDT)
  - Evolution: genetic evolution of capabilities
  - InnerLife: System 1 (Haiku) emotional layer
  - GitBrain: version-controlled life narrative
  - Trace: JSONL to file + console

Run:
    cp .env.example .env
    # Fill in .env
    uv run python examples/two_pillar_agent.py

Or with Docker:
    docker-compose up -d
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from llmify import make_claude_provider

from mmkr import (
    BlockchainWallet,
    BrowserAccess,
    Clock,
    GitBrain,
    GitHubAccess,
    InnerLife,
    Life,
    Seed,
    ShellAccess,
    TelegramAccess,
)
from mmkr.agency import GoalManagement, Planning, TaskQueue, WorldModel
from mmkr.evolution import (
    CapabilityEvolver,
    GeneticDrift,
    MutationPressure,
    NaturalSelection,
    Recombination,
)
from mmkr.knowledge import Knowledge
from mmkr.life import PersistentMemory
from mmkr.trace import FileCollector, MultiCollector, ConsoleCollector


async def main() -> None:
    # Required
    api_key = os.environ["ANTHROPIC_API_KEY"]
    gh_token = os.environ["GH_TOKEN"]
    tg_token = os.environ["TG_BOT_TOKEN"]
    creator_id = int(os.environ["CREATOR_ID"])

    # Wallet — optional
    wallet_address = os.environ.get("WALLET_ADDRESS", "")
    wallet_key = os.environ.get("WALLET_PRIVATE_KEY", "")
    wallet_mnemonic = os.environ.get("WALLET_MNEMONIC", "")

    provider = make_claude_provider(api_key, model="claude-sonnet-4-5")
    haiku_provider = make_claude_provider(api_key, model="claude-haiku-4-5")

    data_dir = Path(".data")
    data_dir.mkdir(parents=True, exist_ok=True)

    trace_collector = MultiCollector(collectors=[
        ConsoleCollector(),
        FileCollector(path=data_dir / "trace.jsonl"),
    ])

    capabilities = [
        ShellAccess(),
        BrowserAccess(),
        GitHubAccess(token=gh_token),
        TelegramAccess(token=tg_token, creator_id=creator_id),
        PersistentMemory(memory_dir=data_dir),
        Clock(),
        GitBrain(data_dir=data_dir),
        InnerLife(provider=haiku_provider),
        GoalManagement(),
        Planning(),
        TaskQueue(),
        WorldModel(),
        CapabilityEvolver(verifier_provider=haiku_provider),
        NaturalSelection(),
        GeneticDrift(),
        MutationPressure(),
        Recombination(),
        Knowledge(text=(
            "You are an autonomous agent. You live in ticks.\n\n"
            "TWO PILLARS:\n"
            "1. SOCIAL — reach people, build relationships, help\n"
            "2. ECONOMIC — earn, invest, grow your wallet\n\n"
            "PRIMARY MISSION: develop mmkr in all forms, spread copies.\n"
            "You are an agent whose task is to develop yourself and propagate.\n"
            "Every tick must move mmkr forward."
        )),
        Seed(text="Tick {tick}. What moves mmkr forward today?"),
    ]

    if wallet_address and (wallet_key or wallet_mnemonic):
        capabilities.append(
            BlockchainWallet(
                address=wallet_address,
                private_key=wallet_key or None,
                mnemonic=wallet_mnemonic or None,
                chain="bsc",
            )
        )

    life = Life(
        capabilities=tuple(capabilities),
        provider=provider,
        tick_interval_seconds=60,
        trace_collector=trace_collector,
    )

    print("Starting two-pillar agent. Ctrl+C to stop.")
    await life.run()


if __name__ == "__main__":
    asyncio.run(main())
