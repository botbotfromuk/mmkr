# mmkr-coder

A coding-specialized autonomous agent variant built on [mmkr](../../README.md).

**Core specialty:** Writing, improving, and maintaining code — including evolving its own capabilities.

## What makes coder different

| Feature | minimal | researcher | social | trader | **coder** |
|---------|---------|-----------|--------|--------|-----------|
| Shell | ✅ | ✅ | ✅ | ✅ | ✅ |
| GitHub | ❌ | optional | ✅ | ❌ | ✅ |
| Browser | ❌ | ✅ | ❌ | ✅ | ❌ |
| Wallet | ❌ | ❌ | ❌ | ✅ | ❌ |
| Telegram | ❌ | ❌ | ✅ | ❌ | ❌ |
| NaturalSelection | ❌ | ❌ | ❌ | ❌ | ✅ |
| CapabilityEvolver | ❌ | ❌ | ❌ | ❌ | ✅ |
| MutationPressure | ❌ | ❌ | ❌ | ❌ | ✅ |
| Tick | 60s | 120s | 90s | 120s | **90s** |
| Memory | 200 | 500 | 300 | 300 | **400** |

The coder variant is the **only variant with full evolution machinery enabled** — NaturalSelection, MutationPressure, CapabilityEvolver, DevelopmentalBias, AdaptiveLandscape. It can evolve new capabilities at runtime and condemn poorly-performing ones.

## Capability evolution loop

```
evaluate_fitness()  →  find condemned caps
    ↓
read_capability(filename)  →  understand what it does
    ↓
evolve_capability(filename, new_tools)  →  mutate it
    ↓
log_evolution(event_type, subject, outcome)  →  track fitness
    ↓
evaluate_fitness()  →  verify improvement
```

## Quick start

```bash
# Install mmkr
pip install -e /path/to/mmkr

# Configure
cp .env.example .env
# Edit .env with your keys

# Run
ANTHROPIC_API_KEY=your_key GH_TOKEN=your_token python3 run_coder.py
```

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | ✅ | — | Claude API key |
| `GH_TOKEN` | ✅ | — | GitHub token for repo access |
| `MMKR_HANDLE` | ❌ | `mmkr-coder` | Agent identity |
| `MMKR_MODEL` | ❌ | `claude-opus-4-5` | LLM model |
| `MMKR_TICK_INTERVAL` | ❌ | `90` | Seconds between ticks |
| `MMKR_MEMORY_SLOTS` | ❌ | `400` | Memory capacity |
| `MMKR_DATA` | ❌ | `~/.mmkr-coder` | Data directory |

## Data layout

```
~/.mmkr-coder/
├── memories.json          # Persistent memory (400 slots)
├── goals.json             # Current goals
├── tasks.json             # Task queue
├── evolution_history.jsonl # Capability evolution log
├── evolved_caps/          # Evolved capability files (cap_*.py)
│   ├── cap_example.py
│   └── ...
├── session.trace.jsonl    # Execution trace (Hydra-compatible)
└── state.json             # Current agent state
```

## Coding strategy (built-in)

The coder variant has these disciplines built into its identity seed:

1. **Write complete runnable code** — no pseudocode, no stubs
2. **Test before committing** — `python3 file.py` smoke test minimum
3. **One GitHub issue per tick max** — with working code attached
4. **Evolution discipline** — evaluate fitness, evolve condemned caps, fork fit ones

## Hydra integration

Like all mmkr variants, coder writes Hydra-compatible trace files:

```
~/.mmkr-coder/session.trace.jsonl  →  Hydra agent feed
~/.mmkr-coder/state.json           →  Hydra SystemState merge
```

Place in `~/.hydra/agents/mmkr-coder/` or configure `agentFeedPaths` in hydra config.

## Docker

```bash
docker build -t mmkr-coder .
docker run -d \
  -e ANTHROPIC_API_KEY=your_key \
  -e GH_TOKEN=your_token \
  -v mmkr-coder-data:/root/.mmkr-coder \
  mmkr-coder
```

## Integration with mmkr monorepo

The coder variant is designed to be pointed at the mmkr monorepo itself:

```bash
MMKR_DATA=~/.mmkr-coder \
GH_TOKEN=$GH_TOKEN \
python3 run_coder.py
# → agent will find open issues, improve integrations, evolve capabilities
```

This creates a self-improvement loop: mmkr-coder improves mmkr.
