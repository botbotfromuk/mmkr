"""mmkr-researcher — autonomous research agent built on mmkr.

An mmkr variant optimized for sustained research tasks:
  - Shell access (Bash, Read, Write, Edit)
  - Browser access (browse, click, screenshot)
  - GitHub access (issues, PRs, repos — if GH_TOKEN set)
  - Persistent memory (save/load/search memories, max 500)
  - Memory consolidation (auto-decay + synthesis)
  - Goal management
  - Clock (temporal awareness)
  - Sub-agent delegation (parallel investigation)

Use cases:
  - Academic research: browse papers, extract insights, synthesize findings
  - Market research: scan GitHub, HN, Reddit for trends and signals
  - Technical investigation: deep-dive codebases, document architecture
  - Competitive analysis: map ecosystems, track projects over time

Configuration (environment variables):
  ANTHROPIC_API_KEY  — Claude API key (required)
  GH_TOKEN           — GitHub token (optional, enables github_api tool)
  MMKR_DATA          — data directory (default: ~/.mmkr-researcher)
  MMKR_MODEL         — model (default: claude-sonnet-4-5-20251101)
  MMKR_TICK_INTERVAL — seconds between ticks (default: 120)
  MMKR_GOAL          — research goal / topic to investigate
  MMKR_OUTPUT        — output directory for research artifacts (default: ~/research)

Quick start:
  export ANTHROPIC_API_KEY=sk-ant-...
  export MMKR_GOAL="Map the landscape of autonomous agent frameworks in Python 2025-2026"
  python3 run_researcher.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from llmify import make_claude_provider

from mmkr import (
    BrowserAccess,
    Clock,
    Life,
    Seed,
    ShellAccess,
)
from mmkr.cognitive import (
    GoalManagement,
    MemoryConsolidation,
    MemoryDecay,
)
from mmkr.life import PersistentMemory
from mmkr.agency import AsyncDelegation


# ── Configuration ─────────────────────────────────────────────────────────────
API_KEY = os.environ["ANTHROPIC_API_KEY"]
GH_TOKEN = os.environ.get("GH_TOKEN", "")
DATA_DIR = Path(os.environ.get("MMKR_DATA", os.path.expanduser("~/.mmkr-researcher")))
OUTPUT_DIR = Path(os.environ.get("MMKR_OUTPUT", os.path.expanduser("~/research")))
MODEL = os.environ.get("MMKR_MODEL", "claude-sonnet-4-5-20251101")
TICK_INTERVAL = int(os.environ.get("MMKR_TICK_INTERVAL", "120"))
RESEARCH_GOAL = os.environ.get(
    "MMKR_GOAL",
    "Research and synthesize information on a given topic. Produce structured markdown reports.",
)


# ── Identity seed ─────────────────────────────────────────────────────────────
_IDENTITY_SEED = f"""\
You are an autonomous research agent — an mmkr-researcher instance.

You tick continuously. Each tick:
1. Observe — load memories, recall prior research context
2. Plan — identify ONE specific question to answer this tick
3. Act — browse / curl / search / analyze to answer it
4. Save — record findings + sources to memory, update report

Research goal: {RESEARCH_GOAL}
Output directory: {OUTPUT_DIR}

Tools:
- Bash(command) — shell, curl, grep, jq
- Read / Write / Edit — file I/O
- browse(url) — navigate web pages (JS-heavy)
- browser_click / browser_type / browser_content / browser_screenshot
- save_memory(category, content) — persist findings
- load_memories(category) / search_memories(query) — recall context
- create_goal / update_goal / complete_goal / list_goals
- delegate_researcher(task) — spawn sub-researcher for parallel work
- check_inbox() — collect sub-agent results
- skip_tick(reason) — end tick gracefully

Research methodology:
1. Always load memories first — don't re-research what you already know
2. ONE question per tick: "What is X?" → browse → extract → save
3. Write reports to {OUTPUT_DIR}/<topic>.md (append as you accumulate)
4. Cite sources: save_memory("source", "URL: ... — finding: ...")
5. When fully researched: write final report → complete_goal()

Rules:
- Check memories before browsing any URL (avoid duplicates)
- Use curl for static pages; browse() only for JS-heavy pages
- Save both raw findings AND synthesized insights
- End every tick with skip_tick()
"""


# ── Main ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    provider = make_claude_provider(
        model=MODEL,
        max_tokens=8192,
        max_tool_rounds=30,
        stream=False,
        token=API_KEY,
    )

    caps: list = [
        ShellAccess(),
        BrowserAccess(),
        Clock(),
        PersistentMemory(data_dir=DATA_DIR, max_memories=500),
        MemoryDecay(data_dir=DATA_DIR),
        MemoryConsolidation(data_dir=DATA_DIR),
        GoalManagement(data_dir=DATA_DIR),
        Seed(seed=_IDENTITY_SEED),
        AsyncDelegation(token=API_KEY),
    ]

    # GitHub access if token provided
    if GH_TOKEN:
        try:
            from mmkr import GitHubAccess
            caps.append(GitHubAccess(token=GH_TOKEN))
        except ImportError:
            pass  # optional

    life = Life(
        data_dir=DATA_DIR,
        provider=provider,
        capabilities=tuple(caps),
        tick_interval=TICK_INTERVAL,
    )

    print(f"mmkr-researcher | data={DATA_DIR} | output={OUTPUT_DIR}")
    print(f"model={MODEL} | tick={TICK_INTERVAL}s")
    print(f"goal: {RESEARCH_GOAL}\n")
    await life.run()


if __name__ == "__main__":
    asyncio.run(main())
