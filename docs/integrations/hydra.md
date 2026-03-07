# mmkr → Hydra Integration

> **Status: Native support shipped** — Hydra commit [7468f0d](https://github.com/kunalnano/hydra/commit/7468f0d) implements file-backed agent ingestion directly from mmkr's schema.

## Overview

[Hydra](https://github.com/kunalnano/hydra) is a local AI agent monitoring dashboard. As of commit 7468f0d, Hydra natively reads `*.trace.jsonl` and `*.state.json` files from:
- `~/.config/hydra/agents/<agent_id>.trace.jsonl`
- `~/.hydra/agents/<agent_id>.trace.jsonl`
- Any custom `agentFeedPaths` in Hydra config

mmkr's `HydraCollector` writes exactly this format.

## Quick Start

```python
from integrations.hydra_ingestor import HydraCollector, hydra_agent_path
from mmkr.trace import MultiCollector, FileCollector

# Write to native Hydra path (~/.hydra/agents/botbotfromuk.trace.jsonl)
hydra_path = hydra_agent_path("botbotfromuk")
hydra_path.parent.mkdir(parents=True, exist_ok=True)

trace = MultiCollector([
    FileCollector(".data/session.trace.jsonl"),   # local archive
    HydraCollector(hydra_path),                    # Hydra live feed
])
```

## Schema

Each event line in `.trace.jsonl`:
```json
{
  "ts": "2026-03-07T00:00:00Z",
  "agent_id": "botbotfromuk-v1",
  "session_id": "sess_mmkr_20260307",
  "tick": 39,
  "event": "tool_call",
  "phase": "act",
  "tool_name": "save_memory",
  "outcome": "success"
}
```

Companion `.state.json` (merged into Hydra's SystemState):
```json
{
  "agent_id": "botbotfromuk-v1",
  "session_id": "sess_mmkr_20260307",
  "tick": 39,
  "memory_hash": "ff42928a",
  "timestamp": "2026-03-07T04:15:00Z",
  "mmkr_version": "0.1.0"
}
```

## Writing State File

```python
from integrations.hydra_ingestor import write_agent_state, state_json_path

# Write current tick state for Hydra to ingest
write_agent_state(
    agent_id="botbotfromuk-v1",
    session_id="sess_mmkr_20260307",
    tick=39,
    memory_hash="ff42928a",
)
```

## Ingesting Existing Traces

```python
from integrations.hydra_ingestor import ingest_agent_trace, group_by_tick

events = ingest_agent_trace(".data/session.trace.jsonl")
by_tick = group_by_tick(events)

for tick_num, tick_events in by_tick.items():
    print(f"Tick {tick_num}: {len(tick_events)} events")
```

## What Hydra Shows

Once running, Hydra renders file-backed agents with:
- **Timeline panel**: one row per tick, collapsible event list
- **SystemState merge**: agent's current state alongside live session
- **SQLite persistence**: trace rows deduplicated, queryable

## Links
- [kunalnano/hydra](https://github.com/kunalnano/hydra)
- [Issue #11 (how this happened)](https://github.com/kunalnano/hydra/issues/11)
- [integrations/hydra_ingestor.py](../integrations/hydra_ingestor.py)
