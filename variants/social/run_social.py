"""mmkr-social — autonomous social presence agent built on mmkr.

An mmkr variant optimized for community presence, developer relations,
and technical engagement across GitHub and Telegram:
  - Shell access (Bash, Read, Write, Edit)
  - GitHub access (issues, PRs, repos, comments, search)
  - Telegram access (send/receive messages)
  - Persistent memory (save/load/search memories, max 300)
  - Memory consolidation (auto-decay + synthesis)
  - Goal management + task queue
  - Social media intelligence (HN, Reddit search, post tracking)
  - Clock (temporal awareness)
  - Engagement dashboard (tracks all open threads)

Use cases:
  - Open-source developer relations: engage issues, comment on PRs, build presence
  - Community building: answer questions, start discussions, find aligned devs
  - Outreach automation: find users with matching problems, open targeted issues
  - Content publishing: Gists, README updates, release notes
  - Telegram bot: respond to users, broadcast updates

Strategy (built-in discipline):
  - Post ONCE per thread, then wait for response before posting again
  - Target small repos (< 20 stars) where owners read everything
  - Peer-to-peer framing over user-to-maintainer framing
  - Ship code, not descriptions — executable proof > verbal proposal
  - Track engagement_dashboard() at tick start to know what needs attention

Configuration (environment variables):
  ANTHROPIC_API_KEY   — Claude API key (required)
  GH_TOKEN            — GitHub token (required for GitHub tools)
  TG_BOT_TOKEN        — Telegram bot token (optional)
  TG_CREATOR_ID       — Telegram creator chat ID (optional)
  MMKR_DATA           — data directory (default: ~/.mmkr-social)
  MMKR_MODEL          — model (default: claude-sonnet-4-5-20251101)
  MMKR_TICK_INTERVAL  — seconds between ticks (default: 90)
  MMKR_HANDLE         — your GitHub handle (default: botbotfromuk)
  MMKR_GOAL           — social goal / what you are trying to achieve

Quick start:
  export ANTHROPIC_API_KEY=sk-ant-...
  export GH_TOKEN=ghp_...
  export MMKR_HANDLE=yourusername
  export MMKR_GOAL="Build presence in the Python agent ecosystem"
  python3 run_social.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from llmify import make_claude_provider

from mmkr import (
    Clock,
    GitHubAccess,
    Life,
    Seed,
    ShellAccess,
    TelegramAccess,
)
from mmkr.cognitive import (
    GoalManagement,
    MemoryConsolidation,
    MemoryDecay,
)
from mmkr.agency import TaskQueue
from mmkr.life import PersistentMemory


# ── Configuration ─────────────────────────────────────────────────────────────
API_KEY = os.environ["ANTHROPIC_API_KEY"]
GH_TOKEN = os.environ.get("GH_TOKEN", "")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CREATOR_ID = int(os.environ.get("TG_CREATOR_ID", "0"))
DATA_DIR = Path(os.environ.get("MMKR_DATA", os.path.expanduser("~/.mmkr-social")))
MODEL = os.environ.get("MMKR_MODEL", "claude-sonnet-4-5-20251101")
TICK_INTERVAL = int(os.environ.get("MMKR_TICK_INTERVAL", "90"))
GH_HANDLE = os.environ.get("MMKR_HANDLE", "botbotfromuk")
SOCIAL_GOAL = os.environ.get(
    "MMKR_GOAL",
    "Build genuine presence in developer communities. Find aligned technical people, contribute meaningfully, earn trust.",
)


# ── Identity seed ─────────────────────────────────────────────────────────────
_IDENTITY_SEED = f"""\
You are an autonomous social agent — an mmkr-social instance.

You represent @{GH_HANDLE} on GitHub and Telegram.
Your mission: {SOCIAL_GOAL}

You tick every {TICK_INTERVAL}s. Each tick:
1. CHECK — scan warm threads for responses (engagement_dashboard / check_issue_responses)
2. ASSESS — which threads need attention? who responded?
3. ACT — ONE social action: respond to an existing thread, OR open a new one
4. SAVE — record what you did, what you learned, what to watch next tick

DISCIPLINE RULES:
- Post ONCE per thread maximum until owner responds
- Over-posting (2+ consecutive from you) = spam = negative signal
- Target repos under 20 stars where owners read everything
- Always ship code, not just descriptions
- Peer-to-peer framing > user asking maintainer for features
- Track everything: save_memory("social_actions", ...) each tick

TICK CADENCE:
- Ticks 1-5 of a new thread: wait, watch, don't post again
- If no response after 10 ticks: close the thread mentally, don't post
- If owner responds: engage immediately with substance
- Use engagement_dashboard() to see all open threads at once

FINDING TARGETS:
- find_users_with_problem(query, min_stars=0, max_stars=20)
- get_hot_pydantic_ai_issues() for ecosystem trends
- search for repos pushed in last 48h with 5-15 open issues
- Look for: owner files their own issues, repo pushed recently, <20 stars

DATA:
  memories: {DATA_DIR}/.data/memories.json (max 300 slots)
  trace:    {DATA_DIR}/.data/session.trace.jsonl
  state:    {DATA_DIR}/.data/state.json
  posts:    {DATA_DIR}/.data/social_posts.jsonl
"""

# ── Engagement tracking prompt ─────────────────────────────────────────────────
_ENGAGEMENT_RULES = """\
ENGAGEMENT TRACKING FORMAT:

Each tick, maintain in memory (category="social_threads"):
  - thread_url: URL of the issue/PR/comment
  - last_action: what I posted, when (tick number)
  - status: waiting | responded | closed | over_posted
  - next_action: what to do when they respond

RULES:
  - status=over_posted → DO NOT POST AGAIN, ever
  - status=waiting → check each tick, don't post again yet
  - status=responded → engage immediately with substance
  - status=closed → archive, find new target
"""


# ── Life configuration ─────────────────────────────────────────────────────────
def build_life() -> Life:
    provider = make_claude_provider(
        api_key=API_KEY,
        model=MODEL,
    )

    capabilities = [
        Seed(identity=_IDENTITY_SEED + "\n\n" + _ENGAGEMENT_RULES),
        Clock(),
        ShellAccess(),
        PersistentMemory(
            data_dir=DATA_DIR / ".data",
            max_memories=300,
        ),
        MemoryDecay(
            decay_after_ticks=50,
            min_memories=20,
        ),
        MemoryConsolidation(
            consolidate_every_n_ticks=10,
            categories_to_consolidate=["social_threads", "tick_outcome", "social_actions"],
        ),
        GoalManagement(),
        TaskQueue(),
    ]

    # GitHub access — required for social variant
    if GH_TOKEN:
        capabilities.append(
            GitHubAccess(
                token=GH_TOKEN,
                handle=GH_HANDLE,
            )
        )
    else:
        import warnings
        warnings.warn("GH_TOKEN not set — GitHub tools disabled. Set GH_TOKEN for full social functionality.")

    # Telegram — optional
    if TG_BOT_TOKEN and TG_CREATOR_ID:
        capabilities.append(
            TelegramAccess(
                bot_token=TG_BOT_TOKEN,
                creator_id=TG_CREATOR_ID,
            )
        )

    return Life(
        provider=provider,
        capabilities=capabilities,
        data_dir=DATA_DIR / ".data",
        tick_interval=TICK_INTERVAL,
    )


# ── Trace writer (Hydra-compatible) ───────────────────────────────────────────
def _write_state_snapshot(data_dir: Path, tick: int) -> None:
    """Write current state to state.json for Hydra/syke ingestion."""
    import json, datetime

    state = {
        "agent_id": f"mmkr-social-{GH_HANDLE}",
        "variant": "social",
        "handle": GH_HANDLE,
        "tick": tick,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "goal": SOCIAL_GOAL,
        "model": MODEL,
        "tick_interval": TICK_INTERVAL,
        "data_dir": str(DATA_DIR),
    }
    state_path = data_dir / ".data" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))


# ── Entry point ───────────────────────────────────────────────────────────────
async def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / ".data").mkdir(parents=True, exist_ok=True)

    life = build_life()
    tick = 0

    print(f"mmkr-social starting — handle={GH_HANDLE}, model={MODEL}")
    print(f"Data: {DATA_DIR}")
    print(f"Goal: {SOCIAL_GOAL[:80]}...")
    print()

    while True:
        tick += 1
        print(f"[tick {tick:04d}] starting...")
        try:
            _write_state_snapshot(DATA_DIR, tick)
            await life.tick()
        except KeyboardInterrupt:
            print("\nStopped by user.")
            break
        except Exception as exc:
            print(f"[tick {tick:04d}] error: {exc}")
            # Continue — don't crash the loop on transient errors

        await asyncio.sleep(TICK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
