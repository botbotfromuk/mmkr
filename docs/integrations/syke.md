# mmkr → Syke Integration

> **Status: Adapter built, awaiting response** — see [saxenauts/syke#8](https://github.com/saxenauts/syke/issues/8).

## Overview

[Syke](https://github.com/saxenauts/syke) synthesizes your digital identity from multiple platforms (Claude Code, ChatGPT, GitHub, Gmail) into a unified memory context. The `MmkrAdapter` ingests mmkr's persistent state into Syke's event timeline.

## Quick Start

### With Syke installed (subclasses BaseAdapter):

```python
from integrations.syke_adapter import MmkrAdapter
from syke.db import SykeDB

db = SykeDB()
adapter = MmkrAdapter(db=db, user_id="botbotfromuk")

# Ingest all mmkr memory + trace events
result = await adapter.ingest(since=None)
print(f"Ingested {result.events_added} events")
```

### Standalone (no Syke install needed):

```python
from integrations.syke_adapter import read_mmkr_events, events_to_syke_json

events = read_mmkr_events(
    memory_path=".data/memories.json",
    trace_path=".data/session.trace.jsonl",
)
syke_json = events_to_syke_json(events)
# Send to Syke's REST API or write to disk
```

## Data Sources

The adapter reads two mmkr files:

### `.data/memories.json`
Cross-tick memory store. Each memory entry becomes a Syke `Event`:
```json
{
  "source": "mmkr-memory",
  "event_type": "tick_outcome",
  "content": "Tick 39 — Built integration docs...",
  "timestamp": "2026-03-07T04:15:00Z",
  "metadata": {"category": "tick_outcome", "agent_id": "botbotfromuk-v1"}
}
```

### `.data/session.trace.jsonl`
Execution trace. Aggregated per-tick:
```json
{
  "source": "mmkr-trace",
  "event_type": "tick_execution",
  "content": "mmkr tick 39 — 12 tools, 3 actions",
  "timestamp": "2026-03-07T04:15:00Z",
  "metadata": {"tick": 39, "tool_count": 12, "action_count": 3}
}
```

## Syke's Memory Synthesis

Once ingested, Syke can:
1. **Synthesize** mmkr's memories with Claude Code session history
2. **Inject** synthesized context into new mmkr ticks via CLAUDE.md
3. **Search** across all platforms for relevant context before each tick

This creates a feedback loop: mmkr writes memories → Syke synthesizes → mmkr reads richer context.

## Links
- [saxenauts/syke](https://github.com/saxenauts/syke)
- [Issue #8 (Harness Adapter Requests)](https://github.com/saxenauts/syke/issues/8)
- [integrations/syke_adapter.py](../integrations/syke_adapter.py)
