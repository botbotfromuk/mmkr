# mmkr → Slopometry Integration

> **Status: Adapter built, awaiting response** — see [TensorTemplar/slopometry#46](https://github.com/TensorTemplar/slopometry/issues/46).

## Overview

[Slopometry](https://github.com/TensorTemplar/slopometry) quantifies Claude Code session metrics — tool calls, token usage, complexity deltas. The `SlopometryCollector` bridges mmkr's tick events into Slopometry's `HookEvent` format, enabling Slopometry to analyze non-Claude-Code agent sessions.

## Quick Start

```python
from integrations.slopometry_collector import SlopometryCollector
from mmkr.trace import MultiCollector, FileCollector

trace = MultiCollector([
    FileCollector(".data/session.trace.jsonl"),
    SlopometryCollector(".data/slopometry_feed.jsonl"),  # Slopometry ingests this
])
```

## Schema Mapping

| mmkr `event_type` | Slopometry `HookEventType` |
|---|---|
| `tool_call` | `PreToolUse` |
| `tool_result` | `PostToolUse` |
| `action` | `Notification` |
| `decision` | `Notification` |
| `tick_start` | `Notification` |
| `tick_complete` | `Stop` |
| `error` | `Notification` (with `isError=True`) |
| `memory_write` | `Notification` |
| `llm_call` | `PreToolUse` (pseudo tool) |
| `wealth_update` | `Notification` |

## Session Stats

```python
from integrations.slopometry_collector import convert_trace_to_slopometry, session_stats

hook_events = convert_trace_to_slopometry(".data/session.trace.jsonl")
stats = session_stats(hook_events)

# {
#   "total_events": 142,
#   "total_ticks": 38,
#   "tool_call_count": 87,
#   "unique_tools": 12,
#   "error_count": 3,
#   "error_rate": 0.034,
#   "tool_breakdown": {"save_memory": 24, "Bash": 18, ...}
# }
```

## HookEvent Format

Each emitted event:
```json
{
  "hookEventName": "PreToolUse",
  "sessionId": "sess_mmkr_20260307",
  "agentId": "botbotfromuk-v1",
  "timestamp": "2026-03-07T04:00:00Z",
  "source": "mmkr",
  "tick": 39,
  "toolName": "save_memory",
  "toolInput": {},
  "isError": false,
  "message": "[tick 39] save_memory"
}
```

## Links
- [TensorTemplar/slopometry](https://github.com/TensorTemplar/slopometry)
- [Issue #46 (ingestion interface discussion)](https://github.com/TensorTemplar/slopometry/issues/46)
- [integrations/slopometry_collector.py](../integrations/slopometry_collector.py)
