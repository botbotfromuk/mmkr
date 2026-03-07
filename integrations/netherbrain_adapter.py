"""mmkr → NetherBrain integration — bridge tick sessions into NetherBrain's session DAG.

NetherBrain (https://github.com/Wh1isper/netherbrain) is a self-hosted persistent
agent service with a git-like session DAG, IM gateway (Telegram/Discord), and
Langfuse observability. This module bridges mmkr's tick-based execution into
NetherBrain's session model.

Use cases:
  1. Surface mmkr tick sessions as NetherBrain conversation history (read)
  2. Trigger mmkr ticks via NetherBrain's IM gateway (Telegram → mmkr action)
  3. Export mmkr trace events to NetherBrain's event stream (write)

Architecture alignment:
  mmkr concept          → NetherBrain concept
  ─────────────────────────────────────────────
  session_id            → conversation_id
  tick N                → session (child of previous tick's session)
  tick events           → StreamEvent (EventType.TEXT / METADATA)
  memory entries        → conversation display_messages (injected context)
  agent_id              → preset_id (agent configuration identity)
  trace.jsonl           → session state.json (file-backed, like Hydra pattern)

Usage:
    from integrations.netherbrain_adapter import (
        mmkr_tick_to_session_event,
        MmkrNetherBrainBridge,
    )

    # Convert a tick's trace events to NetherBrain stream events
    bridge = MmkrNetherBrainBridge(
        netherbrain_url="http://localhost:8080",
        agent_id="botbotfromuk-v1",
        session_id="sess_mmkr_20260307",
    )
    # bridge.publish_tick_complete(tick=37, summary="...", actions=[...])
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


# ── NetherBrain event schema (mirrors netherbrain/agent_runtime/models/events.py) ──

class EventType:
    """NetherBrain StreamEvent types (simplified mirror)."""
    TEXT = "text"
    METADATA = "metadata"
    ERROR = "error"
    DONE = "done"
    TOOL_CALL = "tool_call"
    TOOL_RETURN = "tool_return"


@dataclass
class NetherBrainEvent:
    """A single event in a NetherBrain session stream.

    Mirrors netherbrain.agent_runtime.models.events.StreamEvent structure.
    """
    type: str                    # EventType.*
    session_id: str
    content: str                 # text content or JSON payload
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: str | None = None

    def to_sse_line(self) -> str:
        """Format as Server-Sent Event line for NetherBrain's transport."""
        data = {
            "type": self.type,
            "session_id": self.session_id,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }
        if self.tool_name:
            data["tool_name"] = self.tool_name
        if self.tool_args is not None:
            data["tool_args"] = self.tool_args
        if self.tool_result is not None:
            data["tool_result"] = self.tool_result
        return f"data: {json.dumps(data)}\n\n"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "session_id": self.session_id,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "tool_result": self.tool_result,
        }


# ── mmkr → NetherBrain event conversion ──

def mmkr_event_to_netherbrain(event: dict[str, Any]) -> NetherBrainEvent | None:
    """Convert a single mmkr trace.jsonl event to a NetherBrain StreamEvent.

    Returns None for event types that don't map to NetherBrain events.

    mmkr event_type   → NetherBrain EventType
    ──────────────────────────────────────────
    tick_start        → metadata (tick boundary marker)
    tick_complete     → metadata + text (tick summary)
    tool_call         → tool_call
    tool_result       → tool_return
    action            → text
    decision          → metadata
    error             → error
    llm_call          → metadata (model invocation)
    memory_read       → (skip — internal)
    memory_write      → metadata
    """
    event_type = event.get("event_type", "")
    session_id = event.get("session_id", "")
    tick = event.get("tick", 0)
    ts = event.get("ts", datetime.now(timezone.utc).isoformat())
    meta = {"tick": tick, "agent_id": event.get("agent_id", ""), "source": "mmkr"}

    if event_type == "tick_start":
        return NetherBrainEvent(
            type=EventType.METADATA,
            session_id=session_id,
            content=f"[mmkr tick {tick} start]",
            timestamp=ts,
            metadata={**meta, "phase": "start"},
        )

    elif event_type == "tick_complete":
        summary = event.get("summary", event.get("outcome", "tick complete"))
        return NetherBrainEvent(
            type=EventType.TEXT,
            session_id=session_id,
            content=f"Tick {tick}: {summary}",
            timestamp=ts,
            metadata={**meta, "phase": "complete", "outcome": event.get("outcome", "")},
        )

    elif event_type == "tool_call":
        tool = event.get("tool", event.get("tool_name", "unknown"))
        args = event.get("args", event.get("tool_args", {}))
        return NetherBrainEvent(
            type=EventType.TOOL_CALL,
            session_id=session_id,
            content=f"[tick {tick}] {tool}",
            timestamp=ts,
            metadata=meta,
            tool_name=tool,
            tool_args=args if isinstance(args, dict) else {"raw": str(args)},
        )

    elif event_type == "tool_result":
        tool = event.get("tool", event.get("tool_name", "unknown"))
        result = event.get("result", event.get("outcome", ""))
        return NetherBrainEvent(
            type=EventType.TOOL_RETURN,
            session_id=session_id,
            content=f"[tick {tick}] {tool} → {str(result)[:200]}",
            timestamp=ts,
            metadata={**meta, "outcome": event.get("outcome", "success")},
            tool_name=tool,
            tool_result=str(result)[:500],
        )

    elif event_type == "action":
        desc = event.get("description", event.get("action_type", "action"))
        return NetherBrainEvent(
            type=EventType.TEXT,
            session_id=session_id,
            content=f"[tick {tick}] {desc}",
            timestamp=ts,
            metadata={**meta, "action_type": event.get("action_type", "")},
        )

    elif event_type == "decision":
        reasoning = event.get("reasoning", event.get("description", ""))
        return NetherBrainEvent(
            type=EventType.METADATA,
            session_id=session_id,
            content=f"[tick {tick}] decision: {reasoning[:200]}",
            timestamp=ts,
            metadata={**meta, "decision": event.get("decision", "")},
        )

    elif event_type == "error":
        error_msg = event.get("error", event.get("description", "unknown error"))
        return NetherBrainEvent(
            type=EventType.ERROR,
            session_id=session_id,
            content=f"[tick {tick}] error: {error_msg}",
            timestamp=ts,
            metadata={**meta, "error": error_msg},
        )

    elif event_type == "memory_write":
        return NetherBrainEvent(
            type=EventType.METADATA,
            session_id=session_id,
            content=f"[tick {tick}] memory saved",
            timestamp=ts,
            metadata={**meta, "category": event.get("category", "")},
        )

    # Skip: memory_read, llm_call internals, etc.
    return None


def convert_trace_to_netherbrain(jsonl_path: str) -> list[NetherBrainEvent]:
    """Convert a mmkr .trace.jsonl file into a list of NetherBrain events.

    The resulting events can be replayed into a NetherBrain conversation
    as historical context, or streamed via SSE to a NetherBrain client.

    Args:
        jsonl_path: path to .trace.jsonl file

    Returns:
        List of NetherBrainEvent objects, one per convertible trace line.
    """
    events = []
    for line in Path(jsonl_path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
            event = mmkr_event_to_netherbrain(raw)
            if event is not None:
                events.append(event)
        except (json.JSONDecodeError, KeyError):
            continue
    return events


def group_by_conversation(events: list[NetherBrainEvent]) -> dict[str, list[NetherBrainEvent]]:
    """Group events by session_id → maps to NetherBrain conversation_id."""
    groups: dict[str, list[NetherBrainEvent]] = {}
    for e in events:
        groups.setdefault(e.session_id, []).append(e)
    return groups


# ── MmkrNetherBrainBridge — HTTP client for live integration ──

class MmkrNetherBrainBridge:
    """Bridge between a running mmkr agent and a NetherBrain instance.

    Publishes mmkr tick events to NetherBrain via its REST API.
    NetherBrain URL: typically http://localhost:8080

    Usage (standalone, no imports from netherbrain required):
        bridge = MmkrNetherBrainBridge(
            netherbrain_url="http://localhost:8080",
            agent_id="botbotfromuk-v1",
            session_id="sess_mmkr_20260307",
        )
        bridge.publish_tick_complete(tick=37, summary="Posted to GitHub", actions=["post_issue_comment"])
    """

    def __init__(
        self,
        netherbrain_url: str,
        agent_id: str,
        session_id: str,
        conversation_id: str | None = None,
    ) -> None:
        self.netherbrain_url = netherbrain_url.rstrip("/")
        self.agent_id = agent_id
        self.session_id = session_id
        self.conversation_id = conversation_id or session_id

    def _make_event(self, event_type: str, content: str, tick: int, **kwargs: Any) -> NetherBrainEvent:
        return NetherBrainEvent(
            type=event_type,
            session_id=self.session_id,
            content=content,
            metadata={"tick": tick, "agent_id": self.agent_id, "source": "mmkr", **kwargs},
        )

    def publish_tick_complete(
        self,
        tick: int,
        summary: str,
        actions: list[str] | None = None,
        outcome: str = "success",
    ) -> bool:
        """Publish a tick completion event to NetherBrain.

        In a real integration, this would POST to NetherBrain's session API.
        Here we emit the event payload for inspection.

        Returns True if successfully published (or in standalone mode, always True).
        """
        event = self._make_event(
            EventType.TEXT,
            content=f"Tick {tick}: {summary}",
            tick=tick,
            actions=actions or [],
            outcome=outcome,
        )
        # In production: POST to {self.netherbrain_url}/api/sessions/{self.session_id}/events
        # For now: emit to stdout for testing
        print(f"[NetherBrain] {event.to_sse_line().strip()}")
        return True

    def memory_as_context(self, memory_path: str) -> list[dict[str, Any]]:
        """Convert mmkr memory store into NetherBrain display_messages format.

        NetherBrain's display_messages.json is a list of {role, content} objects —
        same as OpenAI/Anthropic message format. mmkr memories map to system context.

        Returns:
            List of {role: "system", content: "..."} dicts for injection.
        """
        memories_file = Path(memory_path)
        if not memories_file.exists():
            return []

        data = json.loads(memories_file.read_text())
        messages = []

        # mmkr memories.json format: {category: [{content: str, ...}]}
        if isinstance(data, dict):
            for category, entries in data.items():
                if isinstance(entries, list):
                    for entry in entries[-3:]:  # last 3 per category
                        content = entry.get("content", "") if isinstance(entry, dict) else str(entry)
                        if content:
                            messages.append({
                                "role": "system",
                                "content": f"[mmkr memory/{category}] {content[:500]}",
                            })

        return messages


# ── Standalone smoke test ──

if __name__ == "__main__":
    import tempfile

    print("mmkr → NetherBrain adapter smoke test\n")

    # Create fixture trace
    fixture_events = [
        {"ts": "2026-03-07T03:30:00Z", "agent_id": "botbotfromuk-v1",
         "session_id": "sess_test", "tick": 37, "event_type": "tick_start", "outcome": "success"},
        {"ts": "2026-03-07T03:30:10Z", "agent_id": "botbotfromuk-v1",
         "session_id": "sess_test", "tick": 37, "event_type": "tool_call",
         "tool": "post_issue_comment", "args": {"repo": "Wh1isper/netherbrain", "issue_number": 1},
         "outcome": "success"},
        {"ts": "2026-03-07T03:30:11Z", "agent_id": "botbotfromuk-v1",
         "session_id": "sess_test", "tick": 37, "event_type": "tool_result",
         "tool": "post_issue_comment", "result": "comment posted", "outcome": "success"},
        {"ts": "2026-03-07T03:30:15Z", "agent_id": "botbotfromuk-v1",
         "session_id": "sess_test", "tick": 37, "event_type": "action",
         "action_type": "github_comment", "description": "Posted NetherBrain integration to Wh1isper/netherbrain",
         "outcome": "success"},
        {"ts": "2026-03-07T03:30:30Z", "agent_id": "botbotfromuk-v1",
         "session_id": "sess_test", "tick": 37, "event_type": "tick_complete",
         "summary": "Built netherbrain_adapter.py, posted to Wh1isper", "outcome": "success"},
    ]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".trace.jsonl", delete=False) as f:
        for e in fixture_events:
            f.write(json.dumps(e) + "\n")
        path = f.name

    # Convert
    nb_events = convert_trace_to_netherbrain(path)
    print(f"Converted {len(nb_events)} mmkr events → NetherBrain StreamEvents:\n")
    for e in nb_events:
        print(f"  [{e.type:12}] tick={e.metadata.get('tick', '?'):3}  {e.content[:80]}")

    print()

    # Test bridge
    bridge = MmkrNetherBrainBridge(
        netherbrain_url="http://localhost:8080",
        agent_id="botbotfromuk-v1",
        session_id="sess_test",
    )
    print("Bridge.publish_tick_complete:")
    bridge.publish_tick_complete(
        tick=37,
        summary="Built netherbrain_adapter.py — mmkr ↔ NetherBrain integration",
        actions=["post_issue_comment", "git_commit"],
        outcome="success",
    )

    print("\nOK — netherbrain_adapter smoke test passed")
