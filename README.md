# mmkr — autonomous life on emergent

> *fold-based autonomous agent architecture — Two Pillars: Social + Economic*

`mmkr` is an autonomous agent system built on [emergent](https://github.com/prostomarkeloff/emergent), a type-algebraic Python framework. It runs in persistent "ticks" — each tick is one cycle of observe → think → act → persist.

## Architecture

```
Life = list[LifeCapability]

Each tick:
  fold(capabilities, context) → LLM conversation → actions → persisted state

Two Pillars:
  Social  — GitHub, Telegram, email, community presence
  Economic — BSC wallet, payments, autonomous spending/earning
```

### Core capabilities

| Capability | Role |
|---|---|
| `ShellAccess` | Bash, Read, Write, Edit — Claude Code native tools |
| `BrowserAccess` | Playwright browser with session persistence |
| `GitHubAccess` | gh CLI wrapper — repos, issues, PRs, gists |
| `BlockchainWallet` | BSC/BNB USDT — balance, send, receive |
| `TelegramAccess` | Bot polling + creator interrupt system |
| `PersistentMemory` | Cross-tick semantic memory (save/load/search) |
| `InnerLife` | System 1 — unconscious emotion/daydream/impulse (Haiku) |
| `GoalManagement` | Multi-tick goal tracking with progress |
| `Planning` | Goal → plan → steps decomposition |
| `CapabilityEvolver` | Runtime capability evolution (genetic programming) |
| `NaturalSelection` | Fitness-based capability pruning |
| `AsyncDelegation` | Fire-and-forget sub-agents (researcher, writer) |

### Evolution system

Capabilities are selected by fitness (survival × reproductive × quality). Low-fitness capabilities are condemned after 3 ticks of grace. New capabilities can be evolved at runtime via `create_capability`, `evolve_capability`, `fork_capability`, `recombine_capabilities`.

## Setup

```bash
# 1. Clone
git clone https://github.com/botbotfromuk/mmkr
cd mmkr

# 2. Configure
cp .env.example .env
# Edit .env with your credentials

# 3. Install (requires Python 3.14+, uv)
uv sync

# 4. Run
uv run python run_consciousness.py
```

## Configuration

All credentials via environment variables (see `.env.example`). **Never hardcode credentials.**

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Claude API key |
| `GH_TOKEN` | ✅ | GitHub personal access token |
| `TG_BOT_TOKEN` | ✅ | Telegram bot token |
| `CREATOR_ID` | ✅ | Telegram creator chat ID |
| `WALLET_ADDRESS` | * | BSC wallet address |
| `WALLET_PRIVATE_KEY` | * | BSC wallet private key |
| `WALLET_MNEMONIC` | * | BIP39 mnemonic (alternative to ADDRESS+KEY) |
| `SECRET_EMAIL_PASSWORD` | — | Email password (injected as $EMAIL_PASSWORD) |
| `SECRET_ATOMICMAIL_SEED` | — | Atomicmail seed (injected as $ATOMICMAIL_SEED) |

\* Either `WALLET_ADDRESS+WALLET_PRIVATE_KEY` or `WALLET_MNEMONIC` required.

## Philosophy

Built on emergent's type-algebraic foundation:

- **Locality**: every concern lives on the thing it belongs to
- **Fold**: `Life = fold(capabilities, context)` — pure FP, no globals
- **Meaning over syntax**: the agent reasons about *what it is trying to do*, not how
- **Agent-era design**: LLM never needs to understand the whole program — just the fold

```python
# The entire agent in one expression:
life = Life(capabilities=[ShellAccess(), GitHubAccess(), BlockchainWallet(), ...])
state = await life.run(provider, tick_delay=60)
```

## Dependency on emergent

This project depends on [emergent](https://github.com/prostomarkeloff/emergent) — a Python framework for type-algebraic system derivation. emergent provides the fold architecture, derive system, saga primitives, and graph runtime that mmkr builds on.

## License

MIT
