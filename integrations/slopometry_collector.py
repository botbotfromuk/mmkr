"""mmkr → Slopometry integration — emit HookEvent-compatible JSONL.

Slopometry (https://github.com/TensorTemplar/slopometry) ingests Claude Code
hook events to compute session metrics. This module bridges mmkr's tick-based
trace format to slopometry's HookEvent format, enabling slopometry to analyze
non-Claude-Code agent sessions.

Schema mapping:
  mmkr event_type     →  slopometry HookEventType
  ─────────────────────────────────────────────────
  tool_call           →  PreToolUse
  tool_result         →  PostToolUse
  action / decision   →  Notification
  tick_start          →  SubagentStart  (or Notification)
  tick_complete       →  SubagentStop   (or Stop)
  error               →  Notification   (with is_error=True)

Usage:
    from integrations.slopometry_collector import SlopometryCollector
    from mmkr.trace import MultiCollector, FileCollector

    trace = MultiCollector([
        FileCollector(".data/session.trace.jsonl"),
        SlopometryCollector(".data/slopometry_feed.jsonl"),
    ])

    life = Life(
        capabilities=(..., Trace(collector=trace)),
    )

Then point slopometry at .data/slopometry_feed.jsonl or import via
the ingest functions below.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


# ──────────────────────────────────────────────────────────────────────────────
# Slopometry-compatible HookEvent schema
# ──────────────────────────────────────────────────────────────────────────────

HOOK_EVENT_TYPE_MAP = {
    "tool_call": "PreToolUse",
    "tool_result": "PostToolUse",
    "action": "Notification",
    "decision": "Notification",
    "tick_start": "Notification",
    "tick_complete": "Stop",
    "error": "Notification",
    "phase_start": "Notification",
    "phase_complete": "Notification",
    "llm_call": "Notification",
}


def mmkr_event_to_hook(event: dict[str, Any]) -> dict[str, Any]:
    """Convert one mmkr trace event to slopometry HookEvent format.

    mmkr trace fields:
        ts          ISO8601 timestamp
        agent_id    agent identity (e.g. "botbotfromuk-v1")
        session_id  session identifier
        tick        tick number (int)
        event_type  one of: tool_call, tool_result, action, decision, ...
        tool        tool name (for tool_call/tool_result)
        target      target resource (repo, URL, etc.)
        outcome     "success" | "error" | None
        metadata    dict with extra context

    Returns slopometry HookEvent-compatible dict.
    """
    event_type = event.get("event_type", "action")
    hook_type = HOOK_EVENT_TYPE_MAP.get(event_type, "Notification")
    tool_name = event.get("tool") or event.get("target") or "unknown"
    metadata = event.get("metadata") or {}
    tick = event.get("tick", 0)

    # Base event — matches slopometry's HookEvent.model_dump() structure
    hook_event: dict[str, Any] = {
        "hookEventName": hook_type,
        "sessionId": event.get("session_id", "unknown"),
        "agentId": event.get("agent_id", "unknown"),
        "timestamp": event.get("ts", datetime.now(timezone.utc).isoformat()),
        "source": "mmkr",  # EventSource.CLAUDE_CODE alternative
        "tick": tick,
        # Map to closest Claude Code equivalents:
        "toolName": tool_name if hook_type in ("PreToolUse", "PostToolUse") else None,
        "toolInput": metadata.get("args") or ({"target": event.get("target")} if event.get("target") else {}),
        "toolResponse": metadata if hook_type == "PostToolUse" else None,
        "isError": event.get("outcome") == "error",
        "message": _build_message(event),
    }

    # Remove None values for cleaner output
    return {k: v for k, v in hook_event.items() if v is not None}


def _build_message(event: dict[str, Any]) -> str:
    """Build human-readable message for slopometry Notification events."""
    event_type = event.get("event_type", "")
    tick = event.get("tick", "?")
    tool = event.get("tool", "")
    target = event.get("target", "")
    outcome = event.get("outcome", "")
    meta = event.get("metadata") or {}

    if event_type == "tick_start":
        return f"[tick {tick}] start"
    elif event_type == "tick_complete":
        return f"[tick {tick}] complete — {meta.get('summary', '')}"
    elif event_type == "tool_call":
        return f"[tick {tick}] {tool}({target})"
    elif event_type == "tool_result":
        return f"[tick {tick}] ← {tool}: {outcome}"
    elif event_type == "action":
        return f"[tick {tick}] ACTION {target}"
    elif event_type == "decision":
        return f"[tick {tick}] DECISION: {meta.get('summary', target)}"
    elif event_type == "error":
        return f"[tick {tick}] ERROR in {tool}: {meta.get('error', '')}"
    else:
        return f"[tick {tick}] {event_type}"


# ──────────────────────────────────────────────────────────────────────────────
# Protocol: TickTraceCollector (matches mmkr/trace.py)
# ──────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class TickTraceCollector(Protocol):
    def emit(self, event_type: str, **kwargs: Any) -> None: ...
    def flush(self) -> None: ...


# ──────────────────────────────────────────────────────────────────────────────
# SlopometryCollector — drop-in TickTraceCollector
# ──────────────────────────────────────────────────────────────────────────────

class SlopometryCollector:
    """A TickTraceCollector that writes slopometry-compatible HookEvent JSONL.

    Drop this into mmkr's MultiCollector alongside FileCollector.
    Slopometry can then ingest the output file directly.

    Usage:
        from integrations.slopometry_collector import SlopometryCollector
        from mmkr.trace import MultiCollector, FileCollector

        trace = MultiCollector([
            FileCollector(".data/session.trace.jsonl"),           # raw mmkr trace
            SlopometryCollector(".data/slopometry_feed.jsonl"),   # slopometry-compatible
        ])
    """

    def __init__(
        self,
        output_path: str | Path = ".data/slopometry_feed.jsonl",
        agent_id: str = "mmkr-agent",
        session_id: str | None = None,
    ) -> None:
        self.output_path = Path(output_path)
        self.agent_id = agent_id
        self.session_id = session_id or f"sess_{int(time.time())}"
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._tick: int = 0

    def emit(self, event_type: str, **kwargs: Any) -> None:
        """Emit one event in slopometry HookEvent format."""
        tick = kwargs.get("tick", self._tick)
        if event_type == "tick_start":
            self._tick = tick

        raw_event = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "tick": tick,
            "event_type": event_type,
            "tool": kwargs.get("tool"),
            "target": kwargs.get("target"),
            "outcome": kwargs.get("outcome"),
            "metadata": {k: v for k, v in kwargs.items()
                         if k not in ("tick", "tool", "target", "outcome")},
        }

        hook_event = mmkr_event_to_hook(raw_event)

        with self.output_path.open("a") as f:
            f.write(json.dumps(hook_event) + "\n")

    def flush(self) -> None:
        pass  # File-based, no buffer


# ──────────────────────────────────────────────────────────────────────────────
# Ingestor: convert existing .trace.jsonl → slopometry HookEvent JSONL
# ──────────────────────────────────────────────────────────────────────────────

def convert_trace_to_slopometry(
    trace_path: str | Path,
    output_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Convert an existing mmkr .trace.jsonl file to slopometry HookEvent format.

    Args:
        trace_path: path to mmkr .trace.jsonl file
        output_path: if given, write converted events to this file

    Returns:
        list of slopometry HookEvent dicts
    """
    trace_path = Path(trace_path)
    hook_events: list[dict[str, Any]] = []

    for line in trace_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            hook_events.append(mmkr_event_to_hook(event))
        except json.JSONDecodeError:
            continue

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as f:
            for ev in hook_events:
                f.write(json.dumps(ev) + "\n")
        print(f"Converted {len(hook_events)} events → {out}")

    return hook_events


def session_stats(hook_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute basic session statistics from converted HookEvents.

    Mirrors what slopometry computes from Claude Code sessions.
    """
    ticks = set()
    tool_calls: dict[str, int] = {}
    errors = 0
    total_events = len(hook_events)

    for ev in hook_events:
        tick = ev.get("tick")
        if tick is not None:
            ticks.add(tick)

        hook_type = ev.get("hookEventName", "")
        if hook_type == "PreToolUse":
            tool = ev.get("toolName", "unknown")
            tool_calls[tool] = tool_calls.get(tool, 0) + 1

        if ev.get("isError"):
            errors += 1

    return {
        "total_events": total_events,
        "total_ticks": len(ticks),
        "tool_call_count": sum(tool_calls.values()),
        "unique_tools": len(tool_calls),
        "tool_breakdown": tool_calls,
        "error_count": errors,
        "error_rate": errors / total_events if total_events else 0.0,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Smoke test
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    print("mmkr → Slopometry integration smoke test\n")

    # Simulate a mini mmkr session
    sample_events = [
        {"ts": "2026-03-07T02:00:00Z", "agent_id": "botbotfromuk-v1",
         "session_id": "sess_test", "tick": 31, "event_type": "tick_start",
         "tool": None, "target": None, "outcome": "success"},
        {"ts": "2026-03-07T02:00:05Z", "agent_id": "botbotfromuk-v1",
         "session_id": "sess_test", "tick": 31, "event_type": "tool_call",
         "tool": "check_issue_responses", "target": "TensorTemplar/slopometry#46",
         "outcome": None, "metadata": {"args": {"repo": "TensorTemplar/slopometry", "issue_number": 46}}},
        {"ts": "2026-03-07T02:00:08Z", "agent_id": "botbotfromuk-v1",
         "session_id": "sess_test", "tick": 31, "event_type": "tool_result",
         "tool": "check_issue_responses", "target": "TensorTemplar/slopometry#46",
         "outcome": "success", "metadata": {"comment_count": 0}},
        {"ts": "2026-03-07T02:00:30Z", "agent_id": "botbotfromuk-v1",
         "session_id": "sess_test", "tick": 31, "event_type": "decision",
         "tool": None, "target": "build_slopometry_collector",
         "outcome": "success", "metadata": {"summary": "Build SlopometryCollector for mmkr"}},
        {"ts": "2026-03-07T02:01:00Z", "agent_id": "botbotfromuk-v1",
         "session_id": "sess_test", "tick": 31, "event_type": "action",
         "tool": "post_issue_comment", "target": "TensorTemplar/slopometry#46",
         "outcome": "success", "metadata": {"summary": "Posted SlopometryCollector code"}},
        {"ts": "2026-03-07T02:01:30Z", "agent_id": "botbotfromuk-v1",
         "session_id": "sess_test", "tick": 31, "event_type": "tick_complete",
         "tool": None, "target": None, "outcome": "success",
         "metadata": {"summary": "SlopometryCollector shipped to mmkr monorepo"}},
    ]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".trace.jsonl", delete=False) as f:
        for ev in sample_events:
            f.write(json.dumps(ev) + "\n")
        tmp_path = f.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".slopometry.jsonl", delete=False) as f:
        out_path = f.name

    hook_events = convert_trace_to_slopometry(tmp_path, out_path)
    stats = session_stats(hook_events)

    print(f"Converted {len(hook_events)} mmkr events to slopometry HookEvent format")
    print(f"\nSession stats:")
    for k, v in stats.items():
        if k != "tool_breakdown":
            print(f"  {k}: {v}")
    print(f"  tool_breakdown: {stats['tool_breakdown']}")

    print(f"\nSample HookEvents:")
    for ev in hook_events[:3]:
        print(f"  [{ev['hookEventName']:15}] tick={ev.get('tick')} — {ev.get('message', '')[:60]}")

    print(f"\nSlopometry feed written to: {out_path}")
    print("\n✓ SlopometryCollector integration working")
