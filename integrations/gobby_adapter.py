"""
mmkr -> GobbyAI/gobby Integration
https://github.com/GobbyAI/gobby

Maps mmkr tick events to gobby session format:
- tick_start    -> session start marker
- tool_call     -> gobby hook PreToolUse event
- tool_result   -> gobby hook PostToolUse event
- decision      -> session annotation
- tick_complete -> session handoff context

GobbyAI roadmap items this targets:
- OpenTelemetry integration (planned): session timeline view, tool call tracing
- Additional memory adapters (future): Stable Memory API

Usage:
    from integrations.gobby_adapter import GobbyAdapter, convert_trace_to_gobby

    # Live collection during a tick
    adapter = GobbyAdapter(session_id="abc123", agent_id="mmkr-01")
    adapter.record({"event": "tool_call", "tool": "check_issue_responses", "tick": 42})
    adapter.flush()  # writes to ~/.gobby/agents/<agent_id>.session.jsonl

    # Retroactive conversion from existing trace
    events = convert_trace_to_gobby(".data/session.trace.jsonl")
    for e in events:
        print(e.to_gobby_json())
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


# -- GobbyAI event schema ------------------------------------------------------

GOBBY_EVENT_TYPES = {
    "tick_start": "session_start",
    "tick_complete": "session_handoff",
    "tool_call": "hook_pre_tool_use",
    "tool_result": "hook_post_tool_use",
    "decision": "session_annotation",
    "action": "session_annotation",
    "error": "hook_error",
    "memory_write": "session_annotation",
}


@dataclass
class GobbySessionEvent:
    """Mirrors GobbyAI session event format for the planned OTel integration."""
    event_type: str           # gobby event type (see GOBBY_EVENT_TYPES)
    ts: str                   # ISO8601 timestamp
    session_id: str           # gobby session ID
    agent_id: str             # mmkr agent identifier
    tick: int                 # mmkr tick number (groups events into thought cycles)
    tool_name: str = ""       # for hook_pre/post_tool_use events
    content: str = ""         # event description / annotation text
    duration_ms: int = 0      # for hook_post_tool_use events
    metadata: dict = field(default_factory=dict)

    def to_gobby_json(self) -> str:
        """Serialize to gobby session JSONL format."""
        obj = {
            "type": self.event_type,
            "ts": self.ts,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "tick": self.tick,
        }
        if self.tool_name:
            obj["tool"] = self.tool_name
        if self.content:
            obj["content"] = self.content
        if self.duration_ms:
            obj["duration_ms"] = self.duration_ms
        if self.metadata:
            obj["metadata"] = self.metadata
        return json.dumps(obj)

    def to_otel_span(self) -> dict:
        """Map to OpenTelemetry span format (gobby planned OTel integration)."""
        span = {
            "name": f"mmkr.{self.event_type}",
            "start_time": self.ts,
            "attributes": {
                "mmkr.agent_id": self.agent_id,
                "mmkr.session_id": self.session_id,
                "mmkr.tick": self.tick,
                "gobby.event_type": self.event_type,
            },
        }
        if self.tool_name:
            span["attributes"]["tool.name"] = self.tool_name
        if self.duration_ms:
            span["duration_ms"] = self.duration_ms
        if self.content:
            span["attributes"]["mmkr.content"] = self.content[:200]
        return span


# -- Core conversion functions -------------------------------------------------

def mmkr_event_to_gobby(
    event: dict,
    session_id: str,
    agent_id: str,
) -> "GobbySessionEvent | None":
    """Convert a single mmkr trace event to a GobbySessionEvent."""
    evt_type = event.get("event", "")
    gobby_type = GOBBY_EVENT_TYPES.get(evt_type)
    if not gobby_type:
        return None  # skip unknown events

    ts = event.get("ts", event.get("timestamp", datetime.utcnow().isoformat() + "Z"))
    tick = event.get("tick", 0)

    if evt_type == "tool_call":
        return GobbySessionEvent(
            event_type=gobby_type,
            ts=ts,
            session_id=session_id,
            agent_id=agent_id,
            tick=tick,
            tool_name=event.get("tool", event.get("tool_name", "")),
            content=json.dumps(event.get("args", event.get("input", {})))[:200],
            metadata={"mmkr_event": "tool_call"},
        )
    elif evt_type == "tool_result":
        return GobbySessionEvent(
            event_type=gobby_type,
            ts=ts,
            session_id=session_id,
            agent_id=agent_id,
            tick=tick,
            tool_name=event.get("tool", event.get("tool_name", "")),
            content=str(event.get("result_summary", event.get("result", "")))[:300],
            duration_ms=event.get("duration_ms", 0),
            metadata={"mmkr_event": "tool_result"},
        )
    elif evt_type in ("decision", "action", "memory_write"):
        return GobbySessionEvent(
            event_type=gobby_type,
            ts=ts,
            session_id=session_id,
            agent_id=agent_id,
            tick=tick,
            content=str(event.get("reasoning", event.get("content", event.get("value", ""))))[:400],
            metadata={"mmkr_event": evt_type},
        )
    elif evt_type == "tick_start":
        return GobbySessionEvent(
            event_type=gobby_type,
            ts=ts,
            session_id=session_id,
            agent_id=agent_id,
            tick=tick,
            content=f"Tick {tick} started",
            metadata={"mmkr_event": "tick_start"},
        )
    elif evt_type == "tick_complete":
        return GobbySessionEvent(
            event_type=gobby_type,
            ts=ts,
            session_id=session_id,
            agent_id=agent_id,
            tick=tick,
            duration_ms=event.get("duration_ms", 0),
            content=f"Tick {tick} complete -- {event.get('tools_called', '?')} tools called",
            metadata={
                "mmkr_event": "tick_complete",
                "tools_called": event.get("tools_called", 0),
                "handoff_summary": f"Agent completed tick {tick}. Ready for next session.",
            },
        )
    elif evt_type == "error":
        return GobbySessionEvent(
            event_type=gobby_type,
            ts=ts,
            session_id=session_id,
            agent_id=agent_id,
            tick=tick,
            content=str(event.get("error", event.get("message", "unknown error")))[:200],
            metadata={"mmkr_event": "error"},
        )
    return None


def convert_trace_to_gobby(
    trace_path: "str | Path" = ".data/session.trace.jsonl",
    session_id: str = "mmkr-session",
    agent_id: str = "mmkr-01",
) -> list:
    """Convert an existing mmkr .trace.jsonl file to list of GobbySessionEvent."""
    trace_path = Path(trace_path)
    if not trace_path.exists():
        return []

    events = []
    with open(trace_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                ge = mmkr_event_to_gobby(event, session_id, agent_id)
                if ge:
                    events.append(ge)
            except json.JSONDecodeError:
                continue
    return events


def group_by_tick(events: list) -> dict:
    """Group gobby events by mmkr tick number."""
    grouped: dict = {}
    for e in events:
        grouped.setdefault(e.tick, []).append(e)
    return dict(sorted(grouped.items()))


# -- GobbyAdapter: live collection --------------------------------------------

class GobbyAdapter:
    """
    Drop-in adapter for live mmkr sessions.

    Writes gobby-compatible session JSONL and OTel spans to disk.
    Uses ~/.gobby/agents/<agent_id>.session.jsonl (future gobby native path).
    """

    def __init__(
        self,
        session_id: str = "mmkr-session",
        agent_id: str = "mmkr-01",
        gobby_agents_dir: "str | Path | None" = None,
    ) -> None:
        self.session_id = session_id
        self.agent_id = agent_id
        self._buffer: list = []

        if gobby_agents_dir is None:
            gobby_agents_dir = Path.home() / ".gobby" / "agents"
        self.agents_dir = Path(gobby_agents_dir)
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self.session_path = self.agents_dir / f"{agent_id}.session.jsonl"
        self.otel_path = self.agents_dir / f"{agent_id}.otel.jsonl"

    def record(self, event: dict) -> None:
        """Convert and buffer a raw mmkr event."""
        ge = mmkr_event_to_gobby(event, self.session_id, self.agent_id)
        if ge:
            self._buffer.append(ge)

    def flush(self) -> int:
        """Write buffered events to disk. Returns count written."""
        if not self._buffer:
            return 0
        with open(self.session_path, "a") as sf, open(self.otel_path, "a") as of:
            for ge in self._buffer:
                sf.write(ge.to_gobby_json() + "\n")
                of.write(json.dumps(ge.to_otel_span()) + "\n")
        written = len(self._buffer)
        self._buffer.clear()
        return written

    def write_handoff_context(self, state: dict) -> Path:
        """Write gobby-compatible session handoff context."""
        handoff_path = self.agents_dir / f"{self.agent_id}.handoff.json"
        handoff = {
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "tick": state.get("tick", 0),
            "ts": datetime.utcnow().isoformat() + "Z",
            "summary": state.get("summary", f"mmkr agent at tick {state.get('tick', 0)}"),
            "goals": state.get("goals", []),
            "memory_count": state.get("memory_count", 0),
            "gobby_session_format": "mmkr-v1",
        }
        handoff_path.write_text(json.dumps(handoff, indent=2))
        return handoff_path


def session_stats(events: list) -> dict:
    """Compute basic session statistics from gobby events."""
    tool_calls = [e for e in events if e.event_type == "hook_pre_tool_use"]
    errors = [e for e in events if e.event_type == "hook_error"]
    ticks = {e.tick for e in events}

    tool_breakdown: dict = {}
    for e in tool_calls:
        tool_breakdown[e.tool_name] = tool_breakdown.get(e.tool_name, 0) + 1

    return {
        "tick_count": len(ticks),
        "tool_calls": len(tool_calls),
        "errors": len(errors),
        "error_rate": round(len(errors) / max(len(tool_calls), 1), 3),
        "tool_breakdown": dict(sorted(tool_breakdown.items(), key=lambda x: -x[1])[:10]),
        "ticks": sorted(ticks),
    }


if __name__ == "__main__":
    import tempfile

    sample_events = [
        {"event": "tick_start", "tick": 42, "session_id": "sess-abc", "agent_id": "mmkr-01",
         "ts": "2026-03-07T09:17:00Z"},
        {"event": "tool_call", "tool": "check_issue_responses",
         "args": {"repo": "GobbyAI/gobby", "issue_number": 8},
         "tick": 42, "ts": "2026-03-07T09:17:01Z"},
        {"event": "tool_result", "tool": "check_issue_responses",
         "result_summary": "0 responses yet", "duration_ms": 340,
         "tick": 42, "ts": "2026-03-07T09:17:02Z"},
        {"event": "decision", "reasoning": "GobbyAI is a good target -- session handoff alignment",
         "tick": 42, "ts": "2026-03-07T09:17:03Z"},
        {"event": "tick_complete", "tick": 42, "duration_ms": 4200, "tools_called": 8,
         "ts": "2026-03-07T09:17:05Z"},
    ]

    print("=== GobbyAdapter smoke test ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        adapter = GobbyAdapter(
            session_id="sess-abc",
            agent_id="mmkr-01",
            gobby_agents_dir=tmpdir,
        )
        for ev in sample_events:
            adapter.record(ev)
        written = adapter.flush()
        print(f"Flushed {written} events")

        session_file = Path(tmpdir) / "mmkr-01.session.jsonl"
        lines = session_file.read_text().strip().splitlines()
        print(f"\nSession events ({len(lines)} lines):")
        for line in lines:
            obj = json.loads(line)
            print(f"  [{obj['type']}] tick={obj['tick']} tool={obj.get('tool', '')} content={obj.get('content', '')[:50]}")

    print("\n=== OTel spans ===")
    for ev in sample_events:
        ge = mmkr_event_to_gobby(ev, "sess-abc", "mmkr-01")
        if ge:
            span = ge.to_otel_span()
            print(f"  span: {span['name']} attrs={list(span['attributes'].keys())}")

    print("\nSmoke test PASSED")
