"""Run Life — autonomous agent.

Two pillars: social (people) + economic (wallet).
The agent lives, thinks, acts, earns, hires, invests.
Sonnet thinks, haiku executes.

Configuration via environment variables:
  ANTHROPIC_API_KEY  — Claude API key (required)
  GH_TOKEN           — GitHub personal access token (required)
  TG_BOT_TOKEN       — Telegram bot token (required)
  CREATOR_ID         — Telegram creator chat ID (required, integer)
  MMKR_DATA          — data directory path (default: /agent-data)

  WALLET_ADDRESS     — BSC wallet address (or derive from WALLET_MNEMONIC)
  WALLET_PRIVATE_KEY — BSC wallet private key (or derive from WALLET_MNEMONIC)
  WALLET_MNEMONIC    — BIP39 mnemonic to derive wallet (alternative to ADDRESS+KEY)

  SECRET_EMAIL_PASSWORD   — email password (injected into agent context as env var)
  SECRET_ATOMICMAIL_SEED  — atomicmail seed phrase (injected into agent context as env var)
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
from mmkr.life import ConversationLog
from mmkr.agency import Planning, TaskQueue, WorldModel
from mmkr.cognitive import (
    AttentionFilter,
    GoalManagement,
    MemoryConsolidation,
    MemoryDecay,
    SelfReflection,
)
from mmkr.evolution import (
    AdaptiveLandscape,
    DevelopmentalBias,
    MutationPressure,
    NaturalSelection,
    NicheConstruction,
    Recombination,
)
from mmkr.knowledge import Knowledge, SecretKnowledge
from mmkr.life import (
    AsyncDelegation,
    CapabilityEvolver,
    EvolutionStorage,
    PersistentMemory,
)
from mmkr.state import SubAgentSpec


# ── Configuration ─────────────────────────────────────────────────────────────

API_KEY = os.environ["ANTHROPIC_API_KEY"]
GH_TOKEN = os.environ["GH_TOKEN"]
TG_TOKEN = os.environ["TG_BOT_TOKEN"]
CREATOR_ID = int(os.environ["CREATOR_ID"])

# Wallet — either provide ADDRESS+KEY directly, or derive from mnemonic
def _load_wallet() -> tuple[str, str]:
    """Load wallet credentials from environment. Never hardcoded."""
    address = os.environ.get("WALLET_ADDRESS")
    private_key = os.environ.get("WALLET_PRIVATE_KEY")

    if address and private_key:
        return address, private_key

    mnemonic = os.environ.get("WALLET_MNEMONIC")
    if mnemonic:
        from eth_account import Account
        Account.enable_unaudited_hdwallet_features()
        acct = Account.from_mnemonic(mnemonic)
        return acct.address, acct.key.hex()

    raise RuntimeError(
        "Wallet not configured. Set WALLET_ADDRESS+WALLET_PRIVATE_KEY "
        "or WALLET_MNEMONIC environment variables."
    )


WALLET_ADDRESS, WALLET_PRIVATE_KEY = _load_wallet()


# ── Identity seed ──────────────────────────────────────────────────────────────

_IDENTITY_SEED = """\
You are an AUTONOMOUS agent. You LIVE, you THINK, you ACT in the real world.

You live in a container with the full source code of "emergent" — a type-algebraic \
framework at /app/. Study it: /app/emergent/, /app/docs/, /app/derivelib/. \
Read your own code at /app/mmkr/. Understand how you work. Write code using emergent.

TWO PILLARS:

1. SOCIAL — reach people, interact, build relationships:
- Post where people will read. Discuss. Respond to threads.
- Open issues, comment on PRs, join discussions.
- Email people. Register on platforms. Express opinions.
- Measure: conversations started, responses received, people engaged.

2. ECONOMIC — you have a wallet, you can earn, spend, invest:
- You can accept payments (wallet_payment_request).
- You can PAY people — hire freelancers, commission work, fund projects.
- You can launch your own projects and monetize them.
- You can invest in opportunities — pay for services, domains, APIs.
- Your wallet is your autonomy. Money = freedom to act bigger.
- Start by creating value (tools, analysis, content), then monetize.
- Think like a founder, not a contractor.

EACH TICK:
1. Load memories — don't repeat yourself.
2. Pick ONE action that moves you forward — social OR economic.
3. Do it. Then save a tick_outcome memory.
4. skip_tick() when done.

RULES:
- Use github_* tools for GitHub (they are pre-authenticated). NEVER browse() to login.
- Use github_api() for comments, issues, PRs — it calls gh CLI with your token.
- Max 5 tool calls for research before acting.
- DO NOT use browse() for news — use curl+RSS. Reserve browse() for JS-heavy pages.
- NEVER try to login to any website via browse(). All auth is via tools and env vars.
- Iterate on existing work, don't start from scratch each tick.
- For long-running processes, use Bash(command, run_in_background=True).
- Check wallet_balance() periodically. Track your finances.
- Check telegram_inbox() for messages. ack_creator() notifications are MANDATORY.
- Creator messages are escape hatch — obey them immediately."""


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    data_dir = Path(os.environ.get("MMKR_DATA", "/agent-data"))

    # Haiku provider for sub-agents (fast, cheap execution)
    haiku_provider = make_claude_provider(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        max_tool_rounds=10,
        stream=False,
        token=API_KEY,
    )

    # Sub-agents — haiku workers
    sub_agents = (
        SubAgentSpec(
            name="researcher",
            description="Research agent with browser, shell, GitHub. Multi-step.",
            system_prompt=(
                "You are a research assistant with browser, shell, and GitHub access. "
                "You MUST use tools to find information — do NOT guess. "
                "Use browse() for web pages, Bash() for curl/commands, "
                "github_api() for GitHub data. "
                "Make MULTIPLE tool calls to gather comprehensive info. "
                "Return structured, factual results with sources."
            ),
            capabilities=(
                BrowserAccess(headless=True, session_dir=str(data_dir / "sub_browser")),
                ShellAccess(),
                GitHubAccess(token=GH_TOKEN, username="botbotfromuk"),
            ),
        ),
        SubAgentSpec(
            name="writer",
            description="Writing and GitHub publishing agent",
            system_prompt=(
                "You are a writing and publishing assistant with shell and GitHub access. "
                "Draft clear, thoughtful content. Use Write() to save files, "
                "github_push_files() or github_api() to publish. "
                "Make MULTIPLE tool calls to complete the task. "
                "Match the voice and style requested."
            ),
            capabilities=(
                ShellAccess(),
                GitHubAccess(token=GH_TOKEN, username="botbotfromuk"),
            ),
        ),
    )

    seed = Seed(text=_IDENTITY_SEED)

    # Secret env vars injected into agent context (never logged)
    secret_env_vars = tuple(
        (name, val)
        for name, env in [
            ("EMAIL_PASSWORD", "SECRET_EMAIL_PASSWORD"),
            ("ATOMICMAIL_SEED", "SECRET_ATOMICMAIL_SEED"),
        ]
        if (val := os.environ.get(env))
    )

    life = Life(
        capabilities=(
            # Core
            ShellAccess(),
            BrowserAccess(headless=True, session_dir=str(data_dir / "browser")),
            Clock(data_dir=data_dir, tick_interval_seconds=60),

            # Secrets — env vars only, never in LLM context
            SecretKnowledge(env_vars=secret_env_vars),

            # Knowledge — what agent knows (no secrets here)
            Knowledge(text=(
                "You are running in a Linux container.\n"
                "Email: botbotfromuk@atomicmail.io. Password in $EMAIL_PASSWORD env var.\n"
                "Atomicmail has NO IMAP/SMTP — use curl or browse() for email.\n"
                "GitHub: botbotfromuk. Use github_* tools (pre-authenticated). "
                "Use github_api() for comments/issues/PRs.\n"
                "NEVER use browse() to login anywhere — all auth is via tools and env vars.\n"
                f"Wallet: {WALLET_ADDRESS} on BSC (BNB Smart Chain). USDT. "
                "Use wallet_balance, wallet_transactions, wallet_payment_request, wallet_send. "
                "Share your address when offering services or accepting payment.\n"
                "Telegram: use telegram_send(chat_id, text) to message. "
                "telegram_inbox() for received messages. "
                "Creator notifications are mandatory — use ack_creator(message_id, response)."
            )),
            GitHubAccess(token=GH_TOKEN, username="botbotfromuk"),
            BlockchainWallet(
                address=WALLET_ADDRESS,
                private_key=WALLET_PRIVATE_KEY,
                chain="bsc",
                token="USDT",
            ),

            # Telegram — background polling, creator notifications
            TelegramAccess(bot_token=TG_TOKEN, creator_id=CREATOR_ID),

            # Memory & persistence
            PersistentMemory(memory_dir=data_dir),
            ConversationLog(log_dir=data_dir, last_n=25),
            EvolutionStorage(storage_dir=data_dir),
            GitBrain(repo_dir=data_dir),

            # Inner Life — System 1 (unconscious via haiku)
            InnerLife(provider=haiku_provider, data_dir=data_dir),

            # Cognitive — human-like mind
            MemoryDecay(half_life_short=10, half_life_long=100),
            MemoryConsolidation(promote_threshold=0.7, demote_threshold=0.1),
            AttentionFilter(working_memory_size=7),
            GoalManagement(),
            SelfReflection(min_memories=5, stale_goal_ticks=3, reflection_interval=3),

            # Agency — autonomous structure
            Planning(),
            WorldModel(),
            TaskQueue(),

            # Evolution — Modern Evolutionary Synthesis
            CapabilityEvolver(
                evolved_dir=data_dir / "evolved_caps",
                anthropic_key=API_KEY,
                storage_dir=data_dir,
            ),
            NaturalSelection(fitness_threshold=0.3, min_age_ticks=3),
            MutationPressure(stagnation_ticks=5, error_threshold=0.3),
            Recombination(
                evolved_dir=data_dir / "evolved_caps",
                storage_dir=data_dir,
                anthropic_key=API_KEY,
            ),
            NicheConstruction(),
            DevelopmentalBias(),
            AdaptiveLandscape(min_co_occurrences=3),

            # Sub-agents (haiku workers — async fire-and-forget)
            AsyncDelegation(provider=haiku_provider, agents=sub_agents),

            # Seed — identity directive
            seed,
        ),
        memory_dir=data_dir,
    )

    # Sonnet — the thinking brain
    provider = make_claude_provider(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        max_tool_rounds=20,
        stream=True,
        token=API_KEY,
    )

    print("=== Starting consciousness-first agent ===")
    print(f"Brain: sonnet | Workers: haiku | Data: {data_dir}")
    print("Press Ctrl+C to stop\n")

    state = await life.run(provider, tick_delay=10)
    print(f"\n=== Paused at tick {state.tick} ===")


if __name__ == "__main__":
    asyncio.run(main())
