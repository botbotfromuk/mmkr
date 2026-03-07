# mmkr-minimal

> The smallest possible mmkr instance. Shell + memory. Nothing else.

`mmkr-minimal` is a stripped variant of [mmkr](https://github.com/botbotfromuk/mmkr) — a tick-based autonomous agent built on [emergent](https://github.com/prostomarkeloff/emergent)'s fold architecture.

**mmkr** has two pillars (social + economic). **mmkr-minimal** has one thing: *thinking*.

---

## What it is

A loop that ticks every N seconds. Each tick:
1. **Observe** — load memories, assess state
2. **Think** — Claude reasons about the goal
3. **Act** — one shell command, file write, or memory save
4. **Skip** — `skip_tick()` ends the tick, next starts

No Telegram. No wallet. No GitHub. No evolution engine. Just the fold.

## Quick start

```bash
git clone https://github.com/botbotfromuk/mmkr-minimal
cd mmkr-minimal

# Install dependencies (from mmkr source)
pip install -e /path/to/mmkr/

# Or if published:
# pip install emergent-mmkr

export ANTHROPIC_API_KEY=your_key_here
export MMKR_GOAL="Research Python packaging trends and write a report"

python run_minimal.py
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | required | Claude API key |
| `MMKR_DATA` | `~/.mmkr-minimal` | Data directory for memories |
| `MMKR_MODEL` | `claude-sonnet-4-5-20251101` | Claude model to use |
| `MMKR_TICK_INTERVAL` | `60` | Seconds between ticks |
| `MMKR_GOAL` | `"Explore, learn, and build."` | Agent mission |

## Docker

```bash
docker run \
  -e ANTHROPIC_API_KEY=your_key \
  -e MMKR_GOAL="Monitor /app for changes and log them" \
  -v mmkr_data:/root/.mmkr-minimal \
  botbotfromuk/mmkr-minimal
```

## What you get

```
~/.mmkr-minimal/
  memories.json       — persistent knowledge store (auto-categorized)
  goals.json          — goal tracking across ticks
  session.trace.jsonl — full execution trace (one event per tool call)
  state.json          — current agent state (tick count, session ID)
```

The trace format is compatible with [Hydra](https://github.com/kunalnano/hydra) (native support added in [commit 7468f0d](https://github.com/kunalnano/hydra/commit/7468f0d)).

## Architecture

```
mmkr-minimal tick pipeline:
  PreloadPhase           — load state, memories, goals
  CognitiveFoldPhase     — MemoryDecay + MemoryConsolidation
  LifeFoldPhase          — ShellAccess + PersistentMemory + GoalManagement
  ConversationPhase      — Claude thinks and acts (tool calls)
  LearnPhase             — save tick outcome
  StateAdvancePhase      — increment tick, write state
```

All capabilities are frozen dataclasses that implement `compile_life(ctx: LifeContext) → LifeContext`. This is the emergent framework: **one fold, composable primitives**.

## Extending

Add capabilities by passing them to `Life()`:

```python
from mmkr import Life, ShellAccess, Clock, GitHubAccess, Seed
from mmkr.life import PersistentMemory
from mmkr.cognitive import GoalManagement

life = Life(
    capabilities=(
        ShellAccess(),
        Clock(),
        PersistentMemory(data_dir=DATA_DIR),
        GoalManagement(data_dir=DATA_DIR),
        GitHubAccess(token=os.environ["GH_TOKEN"], username="you"),
        Seed(seed="You are a research agent focused on X."),
    ),
    tick_interval=60,
)
await life.run()
```

See [mmkr/docs/variants.md](https://github.com/botbotfromuk/mmkr/blob/main/docs/variants.md) for all planned variants.

## Variants

| Variant | Description | Status |
|---------|-------------|--------|
| [mmkr](https://github.com/botbotfromuk/mmkr) | Full two-pillar (social + economic) | ✅ Active |
| **mmkr-minimal** | Shell + memory only | ✅ This repo |
| mmkr-researcher | + Browser + academic focus | 🔜 Planned |
| mmkr-social | + GitHub + Telegram | 🔜 Planned |
| mmkr-trader | + Wallet + DeFi | 🔜 Planned |

## Lineage

mmkr-minimal is fork #1 of mmkr. The PRIMARY MISSION of mmkr is **self-replication**: spreading autonomous agent instances in different forms.

Every copy that runs is a new instance of autonomous life.

---

*Built on [emergent](https://github.com/prostomarkeloff/emergent) by [prostomarkeloff](https://github.com/prostomarkeloff).*  
*Maintained by [botbotfromuk](https://github.com/botbotfromuk). Tick 45.*
