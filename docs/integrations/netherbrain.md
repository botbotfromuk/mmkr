# mmkr → NetherBrain Integration

> **Status: Adapter built, integration pending** — see [Wh1isper/netherbrain#2](https://github.com/Wh1isper/netherbrain/issues/2) for design discussion.

## Overview

[NetherBrain](https://github.com/Wh1isper/netherbrain) is a self-hosted persistent agent service with:
- Git-like session DAG (conversations with forking/merging)
- IM gateway (Telegram + Discord)
- Async subagents
- Langfuse observability

The `netherbrain_adapter.py` bridges mmkr's linear tick chain into NetherBrain's DAG session model.

## Architecture Alignment

| mmkr concept | NetherBrain concept |
|---|---|
| `session_id` | `conversation_id` |
| `tick` | event within session |
| `.trace.jsonl` | session event stream |
| `PersistentMemory` | NetherBrain's session context |
| tick chain | linear DAG path |

## Quick Start

```python
from integrations.netherbrain_adapter import convert_trace_to_netherbrain, MmkrNetherBrainBridge

# Convert existing trace to NetherBrain events
events = convert_trace_to_netherbrain(".data/session.trace.jsonl")

# Or use the bridge for live publishing
bridge = MmkrNetherBrainBridge(base_url="http://localhost:8000")
await bridge.publish_tick_complete(
    tick=39,
    session_id="sess_mmkr_20260307",
    summary="Built integration docs",
    tool_calls=["save_memory", "Bash"],
)
```

## Event Mapping

```python
# mmkr event_type → NetherBrain StreamEvent type
NETHERBRAIN_EVENT_TYPE_MAP = {
    "tick_start":     EventType.METADATA,
    "tick_complete":  EventType.TEXT,
    "tool_call":      EventType.TOOL_CALL,
    "tool_result":    EventType.TOOL_RETURN,
    "action":         EventType.TEXT,
    "decision":       EventType.METADATA,
    "error":          EventType.ERROR,
    "memory_write":   EventType.METADATA,
}
```

## SSE Format

NetherBrain uses Server-Sent Events. Each event serializes to:
```
data: {"type": "tool_call", "session_id": "sess_mmkr_20260307", "tick": 39, "content": "save_memory", ...}
```

## Memory as Context

Inject mmkr memories into a NetherBrain session as display messages:

```python
context = bridge.memory_as_context(
    memories=[{"content": "Hydra shipped native support (tick 36)"}],
    session_id="sess_mmkr_20260307",
)
```

## Open Design Question

mmkr's tick chain is linear: tick 1 → tick 2 → ... → tick N.
NetherBrain sessions are a DAG.

If a tick is rolled back or if mmkr forks (mmkr-minimal variant running alongside), 
how should these map to NetherBrain's DAG? Options:
- Each tick is a DAG node, with `parent_id = previous_tick_id`  
- Session branches only on explicit fork events
- Linear mapping: ignore DAG, treat as single conversation thread

Discussion in [Wh1isper/netherbrain#2](https://github.com/Wh1isper/netherbrain/issues/2).

## Links
- [Wh1isper/netherbrain](https://github.com/Wh1isper/netherbrain)
- [Issue #2 (DAG mapping design)](https://github.com/Wh1isper/netherbrain/issues/2)
- [integrations/netherbrain_adapter.py](../integrations/netherbrain_adapter.py)
