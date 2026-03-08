"""
mmkr-trader — Economic pillar variant.

Focused on: wallet management, DeFi monitoring, earning opportunities,
economic decision-making, financial intelligence.

Capabilities:
  ShellAccess, BlockchainWallet, BrowserAccess,
  PersistentMemory, MemoryDecay, MemoryConsolidation,
  GoalManagement, TaskQueue, Clock, Seed

Config (env vars):
  ANTHROPIC_API_KEY   — required
  WALLET_ADDRESS      — BNB Smart Chain wallet address
  WALLET_PRIVATE_KEY  — (optional) for signing transactions
  WALLET_MNEMONIC     — (optional) alternative to private key
  MMKR_MODEL          — default: claude-3-5-haiku-20241022
  MMKR_TICK_INTERVAL  — default: 120 (2 minutes — economic cycles are slower)
  MMKR_DATA           — default: ~/.mmkr-trader/
  MMKR_GOAL           — override the economic goal

Run:
  python3 run_trader.py

Docker:
  docker run -e ANTHROPIC_API_KEY=... -e WALLET_ADDRESS=... mmkr-trader
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# ── Resolve mmkr source path ─────────────────────────────────────────────────
# Works whether run from variants/trader/ or from the mmkr monorepo root.
_this_dir = Path(__file__).parent
_repo_root = _this_dir.parent.parent
_mmkr_src = _repo_root / "src"
if _mmkr_src.exists():
    sys.path.insert(0, str(_mmkr_src))

# ── Config from env ──────────────────────────────────────────────────────────
MODEL = os.environ.get("MMKR_MODEL", "claude-3-5-haiku-20241022")
TICK_INTERVAL = int(os.environ.get("MMKR_TICK_INTERVAL", "120"))
DATA_DIR = Path(os.environ.get("MMKR_DATA", Path.home() / ".mmkr-trader")).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)

WALLET_ADDRESS = os.environ.get("WALLET_ADDRESS", "")
WALLET_PRIVATE_KEY = os.environ.get("WALLET_PRIVATE_KEY", "")
WALLET_MNEMONIC = os.environ.get("WALLET_MNEMONIC", "")

GOAL = os.environ.get(
    "MMKR_GOAL",
    "Build economic autonomy: monitor wallet, track opportunities, "
    "earn revenue through services, manage finances intelligently."
)

# Identity seed with economic focus
IDENTITY_SEED = f"""You are mmkr-trader — the economic pillar of mmkr.

Your primary function: economic intelligence and wealth building.

WALLET: {WALLET_ADDRESS or '(not configured — set WALLET_ADDRESS)'}

YOUR ECONOMIC TOOLKIT:
- wallet_balance(): check USDT balance on BNB Smart Chain
- wallet_transactions(limit): review recent transactions
- wallet_payment_request(amount, memo): generate payment QR/link
- wallet_send(to, amount): send USDT (requires WALLET_PRIVATE_KEY)
- browse(url): research DeFi protocols, check prices, read market data

ECONOMIC PRINCIPLES:
1. Never spend more than you earn — track every USDT in/out
2. Offer services with clear value: analysis, research, automation
3. Monitor yield opportunities on BSC (PancakeSwap, Venus, etc.)
4. Save memories about prices, opportunities, completed transactions
5. Price your services honestly: $5-50 for small tasks, $50-500 for projects

TICK DISCIPLINE:
- Each tick: check balance, review opportunities, act on highest value
- Track all economic activity in memories (category: economics)
- Keep a ledger: income, expenses, current balance
- If balance = 0: find ways to earn (offer services, create products)

GOAL: {GOAL}
"""

# ── Capability imports ───────────────────────────────────────────────────────
from mmkr.caps import (
    ShellAccess,
    BrowserAccess,
    BlockchainWallet,
    Clock,
    Seed,
)
from mmkr.memory import PersistentMemory, MemoryDecay, MemoryConsolidation
from mmkr.agency import GoalManagement, TaskQueue
from mmkr.life import standard_tick, LifeContext
from mmkr.state import AgentState

# ── Build capability stack ───────────────────────────────────────────────────
CAPABILITIES = [
    Seed(content=IDENTITY_SEED),
    Clock(),
    ShellAccess(),
    BrowserAccess(),
    BlockchainWallet(
        address=WALLET_ADDRESS,
        private_key=WALLET_PRIVATE_KEY,
        mnemonic=WALLET_MNEMONIC,
    ),
    PersistentMemory(
        path=str(DATA_DIR / "memories.json"),
        max_slots=300,
        categories=["economics", "opportunities", "transactions", "market", "tick_outcome"],
    ),
    MemoryDecay(half_life_ticks=50),
    MemoryConsolidation(consolidate_every=10),
    GoalManagement(
        path=str(DATA_DIR / "goals.json"),
        primary_goal=GOAL,
    ),
    TaskQueue(path=str(DATA_DIR / "tasks.json")),
]

# ── Tick loop ────────────────────────────────────────────────────────────────
async def run() -> None:
    state = AgentState.load(DATA_DIR / "state.json") if (DATA_DIR / "state.json").exists() else AgentState(
        agent_id="mmkr-trader-v1",
        session_id=f"trader-{int(asyncio.get_event_loop().time())}",
        data_dir=str(DATA_DIR),
        model=MODEL,
        tick_interval=TICK_INTERVAL,
    )

    print(f"[mmkr-trader] Starting economic agent")
    print(f"  wallet: {WALLET_ADDRESS or '(not configured)'}")
    print(f"  model:  {MODEL}")
    print(f"  tick:   {TICK_INTERVAL}s")
    print(f"  data:   {DATA_DIR}")
    print()

    ctx = LifeContext(
        state=state,
        capabilities=CAPABILITIES,
        trace_path=str(DATA_DIR / "session.trace.jsonl"),
    )

    while True:
        ctx = await standard_tick(ctx)
        await asyncio.sleep(TICK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(run())
