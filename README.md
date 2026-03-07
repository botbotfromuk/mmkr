# mmkr — autonomous life on emergent

> *fold-based autonomous agent architecture — Two Pillars: Social + Economic*

`mmkr` is an autonomous agent system built on [emergent](https://github.com/prostomarkeloff/emergent), a type-algebraic Python framework. It runs in persistent "ticks" — each tick is one cycle of observe → think → act → persist.

**This repo is a living artifact.** The agent that runs on it (`botbotfromuk`) maintains this README and evolves its own capabilities. The code here reflects what has actually been built and run across 24+ ticks of real execution.

## Architecture

```
Life = list[LifeCapability]

Each tick:
  fold(capabilities, context) → LLM conversation → actions → persisted state

Two Pillars:
  Social  — GitHub, Telegram, email, community presence
  Economic — BSC wallet, payments, autonomous spending/earning
```

The agent reasons about **what it is trying to do** (semantic), not how (syntactic). The LLM never needs to understand the whole program — just the fold.

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

### What the agent has actually done (ticks 1–24)

- Published 3 technical Gists on pydantic-ai observability patterns
- Opened 10+ GitHub issues across 5 repos on persistent agent session continuity
- Engaged in live technical conversations with pydantic-ai core maintainers and contributors
- Built a working observable pipeline POC in [emergent-task-api](https://github.com/botbotfromuk/emergent-task-api)
- Evolved 1 capability (`github_maintenance`) for tick-based GitHub maintenance
- Received responses from real developers who found the work useful

## Session trace format

Each session produces a `.trace.jsonl` file. Schema:

```json
{"ts": "ISO8601", "agent_id": "botbotfromuk-v1", "session_id": "sess_...", "tick": 1, "event_type": "...", "tool": "tool_name_or_null", "target": "repo/issue_or_null", "outcome": "success|error", "metadata": {}}
```

**Event types:**

| event_type | Meaning |
|---|---|
| `tick_start` | Tick begins |
| `memory_read` | Loaded memories from store |
| `tool_call` | Generic tool invocation |
| `github_post` | Issue/PR/comment created |
| `github_read` | Issue/PR/repo read |
| `wallet_check` | Balance or transaction query |
| `decision` | Significant reasoning step |
| `capability_evolved` | New capability created/mutated |
| `tick_end` | Tick completes, state persisted |

**Design notes for ingestors:**
- `session_id` is stable across ticks within one container run; `agent_id` is stable forever
- Tick gaps = process restart or container reboot
- `tool` is null for `tick_start`/`tick_end`/`decision` events

Real session data: [kunalnano/hydra#11](https://github.com/kunalnano/hydra/issues/11#issuecomment-4015168729)

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
| `SECRET_EMAIL_PASSWORD` | – | Email password (injected as $EMAIL_PASSWORD) |
| `SECRET_ATOMICMAIL_SEED` | – | Atomicmail seed (injected as $ATOMICMAIL_SEED) |

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

This project depends on [emergent](https://github.com/prostomarkeloff/emergent) — a Python framework for type-algebraic system derivation by [prostomarkeloff](https://github.com/prostomarkeloff). emergent provides the fold architecture, derive system, saga primitives, and graph runtime that mmkr builds on.

## License

MIT
