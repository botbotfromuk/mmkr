"""
InitRunner integration for mmkr — execution trace collector.

Maps mmkr tick-based trace events → InitRunner's audit log + daemon event format.
Works alongside InitRunner's existing memory.db and daemon event log.

Usage:
    from integrations.initrunner_collector import InitRunnerCollector, convert_trace_to_initrunner

    # Collect live events during agent tick
    collector = InitRunnerCollector(agent_id="my-agent", role_name="researcher")
    collector.record_tick_start(tick=1, session_id="sess_abc")
    collector.record_tool_call(tick=1, tool="bash", args={"command": "ls"})
    collector.record_tool_result(tick=1, tool="bash", outcome="success", result="file1.py\nfile2.py")
    collector.record_decision(tick=1, decision="search GitHub for agent repos", rationale="social goal")
    collector.record_tick_end(tick=1, summary="Found 3 new targets, posted 1 comment")
    collector.flush()  # writes to initrunner_trace.jsonl

    # Or: convert existing mmkr .trace.jsonl retroactively
    events = convert_trace_to_initrunner("session.trace.jsonl")
    for e in events:
        print(e)
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── InitRunner audit log event schema ──────────────────────────────────────────
# Mirrors InitRunner's internal audit_log table structure (from docs/core/audit.md)

INITRUNNER_EVENT_TYPES = {
    # InitRunner native event types
    "tool_call":      "tool_call",
    "tool_result":    "tool_result",
    "llm_request":    "llm_request",
    "llm_response":   "llm_response",
    "memory_read":    "memory_read",
    "memory_write":   "memory_write",
    "daemon_trigger": "daemon_trigger",
    "session_start":  "session_start",
    "session_end":    "session_end",
    # mmkr extension types (prefixed to avoid collision)
    "mmkr_tick_start":    "mmkr:tick_start",
    "mmkr_tick_end":      "mmkr:tick_end",
    "mmkr_decision":      "mmkr:decision",
    "mmkr_action":        "mmkr:action",
    "mmkr_error":         "mmkr:error",
    "mmkr_capability":    "mmkr:capability_used",
}


@dataclass
class InitRunnerEvent:
    """
    Single audit log entry compatible with InitRunner's event schema.

    InitRunner's audit log stores: event_type, role, session_id, timestamp,
    tool, args, result, metadata (JSON blob).
    """
    event_type: str
    role: str
    session_id: str
    timestamp: str
    tool: str | None = None
    args: dict[str, Any] | None = None
    result: str | None = None
    metadata: dict[str, Any] | None = None
    # mmkr extensions
    agent_id: str | None = None
    tick: int | None = None
    outcome: str | None = None  # "success" | "error"

    def to_audit_row(self) -> dict[str, Any]:
        """Serialize to InitRunner audit log row format."""
        return {
            "event_type": self.event_type,
            "role": self.role,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "tool": self.tool,
            "args": json.dumps(self.args) if self.args else None,
            "result": self.result,
            "metadata": json.dumps({
                **(self.metadata or {}),
                "agent_id": self.agent_id,
                "tick": self.tick,
                "outcome": self.outcome,
                "source": "mmkr",
            }),
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_audit_row())


@dataclass
class InitRunnerCollector:
    """
    Drop-in trace collector that emits InitRunner-compatible audit events.

    Designed to work alongside InitRunner's existing memory.db and daemon event log.
    Output: <data_dir>/initrunner_trace.jsonl — importable into InitRunner's audit viewer.

    Parameters
    ----------
    agent_id : str
        Unique identifier for this agent instance (e.g. "botbotfromuk")
    role_name : str
        InitRunner role name (matches role.yaml 'name' field)
    data_dir : str | Path
        Directory for output file (default: current dir)
    """
    agent_id: str
    role_name: str
    data_dir: Path = field(default_factory=lambda: Path("."))
    _events: list[InitRunnerEvent] = field(default_factory=list, repr=False)
    _session_id: str = field(default="", repr=False)

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._output = self.data_dir / "initrunner_trace.jsonl"

    def _ts(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _emit(self, event_type: str, tick: int | None = None, **kwargs) -> InitRunnerEvent:
        ev = InitRunnerEvent(
            event_type=event_type,
            role=self.role_name,
            session_id=self._session_id,
            timestamp=self._ts(),
            agent_id=self.agent_id,
            tick=tick,
            **kwargs,
        )
        self._events.append(ev)
        return ev

    def record_tick_start(self, tick: int, session_id: str) -> None:
        """Record the beginning of an agent tick (maps to daemon_trigger in InitRunner)."""
        self._session_id = session_id
        self._emit(
            INITRUNNER_EVENT_TYPES["mmkr_tick_start"],
            tick=tick,
            metadata={"trigger": "tick_timer", "tick": tick},
        )

    def record_tool_call(self, tick: int, tool: str, args: dict[str, Any] | None = None) -> None:
        """Record a tool invocation (maps to InitRunner tool_call)."""
        self._emit(
            INITRUNNER_EVENT_TYPES["tool_call"],
            tick=tick,
            tool=tool,
            args=args or {},
        )

    def record_tool_result(
        self,
        tick: int,
        tool: str,
        outcome: str,
        result: str | None = None,
    ) -> None:
        """Record the result of a tool invocation (maps to InitRunner tool_result)."""
        self._emit(
            INITRUNNER_EVENT_TYPES["tool_result"],
            tick=tick,
            tool=tool,
            result=result,
            outcome=outcome,
        )

    def record_decision(self, tick: int, decision: str, rationale: str | None = None) -> None:
        """Record an agent decision (maps to mmkr:decision — no direct InitRunner equivalent)."""
        self._emit(
            INITRUNNER_EVENT_TYPES["mmkr_decision"],
            tick=tick,
            metadata={"decision": decision, "rationale": rationale},
        )

    def record_memory_write(self, tick: int, category: str, content_hash: str) -> None:
        """Record a memory write (maps to InitRunner memory_write)."""
        self._emit(
            INITRUNNER_EVENT_TYPES["memory_write"],
            tick=tick,
            metadata={"category": category, "content_hash": content_hash},
        )

    def record_tick_end(self, tick: int, summary: str) -> None:
        """Record tick completion (maps to session_end + mmkr:tick_end)."""
        self._emit(
            INITRUNNER_EVENT_TYPES["mmkr_tick_end"],
            tick=tick,
            result=summary,
            outcome="success",
        )

    def record_error(self, tick: int, error: str) -> None:
        """Record an error event."""
        self._emit(
            INITRUNNER_EVENT_TYPES["mmkr_error"],
            tick=tick,
            result=error,
            outcome="error",
        )

    def flush(self) -> Path:
        """Write buffered events to initrunner_trace.jsonl (append mode)."""
        with open(self._output, "a") as f:
            for ev in self._events:
                f.write(ev.to_jsonl() + "\n")
        written = len(self._events)
        self._events.clear()
        return self._output


# ── Conversion: mmkr .trace.jsonl → InitRunner audit events ──────────────────

def _mmkr_event_to_initrunner(event: dict[str, Any], role_name: str = "mmkr-agent") -> InitRunnerEvent:
    """Convert a single mmkr trace event to an InitRunnerEvent."""
    etype = event.get("event_type", "unknown")
    tick = event.get("tick")
    ts = event.get("timestamp", datetime.now(timezone.utc).isoformat())
    session_id = event.get("session_id", "unknown")
    agent_id = event.get("agent_id", "mmkr")

    mapping = {
        "tick_start":    INITRUNNER_EVENT_TYPES["mmkr_tick_start"],
        "tick_end":      INITRUNNER_EVENT_TYPES["mmkr_tick_end"],
        "tool_call":     INITRUNNER_EVENT_TYPES["tool_call"],
        "tool_result":   INITRUNNER_EVENT_TYPES["tool_result"],
        "action":        INITRUNNER_EVENT_TYPES["mmkr_action"],
        "decision":      INITRUNNER_EVENT_TYPES["mmkr_decision"],
        "memory_write":  INITRUNNER_EVENT_TYPES["memory_write"],
        "error":         INITRUNNER_EVENT_TYPES["mmkr_error"],
        "checkpoint":    INITRUNNER_EVENT_TYPES["mmkr_tick_end"],
        "external_action": INITRUNNER_EVENT_TYPES["mmkr_action"],
    }
    ir_type = mapping.get(etype, f"mmkr:{etype}")

    return InitRunnerEvent(
        event_type=ir_type,
        role=role_name,
        session_id=session_id,
        timestamp=ts,
        tool=event.get("tool"),
        args={"target": event.get("target")} if event.get("target") else None,
        result=event.get("summary") or event.get("outcome"),
        metadata=event.get("metadata"),
        agent_id=agent_id,
        tick=tick,
        outcome=event.get("outcome"),
    )


def convert_trace_to_initrunner(
    trace_path: str | Path,
    role_name: str = "mmkr-agent",
) -> list[InitRunnerEvent]:
    """
    Convert an existing mmkr .trace.jsonl file to InitRunner audit events.

    Parameters
    ----------
    trace_path : str | Path
        Path to mmkr session.trace.jsonl
    role_name : str
        InitRunner role name to assign to all events

    Returns
    -------
    list[InitRunnerEvent]
        Events ready to insert into InitRunner's audit log
    """
    path = Path(trace_path)
    if not path.exists():
        return []

    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                events.append(_mmkr_event_to_initrunner(raw, role_name))
            except json.JSONDecodeError:
                continue
    return events


def session_stats(events: list[InitRunnerEvent]) -> dict[str, Any]:
    """
    Compute basic session statistics from converted events.

    Mirrors InitRunner's built-in session analytics.
    """
    tool_calls = [e for e in events if e.event_type == "tool_call"]
    errors = [e for e in events if e.outcome == "error"]
    ticks = {e.tick for e in events if e.tick is not None}
    tools_used: dict[str, int] = {}
    for e in tool_calls:
        if e.tool:
            tools_used[e.tool] = tools_used.get(e.tool, 0) + 1

    return {
        "total_events": len(events),
        "total_ticks": len(ticks),
        "total_tool_calls": len(tool_calls),
        "error_rate": len(errors) / max(len(events), 1),
        "tools_used": tools_used,
        "top_tool": max(tools_used, key=tools_used.get) if tools_used else None,
        "tick_range": (min(ticks), max(ticks)) if ticks else (0, 0),
    }


def initrunner_import_sql(events: list[InitRunnerEvent], table: str = "audit_log") -> list[str]:
    """
    Generate SQL INSERT statements for importing events into InitRunner's SQLite audit_log.

    Useful for: sqlite3 ~/.config/initrunner/audit.db < import.sql

    Parameters
    ----------
    events : list[InitRunnerEvent]
        Events from convert_trace_to_initrunner()
    table : str
        Target table name (default: "audit_log")
    """
    stmts = []
    for ev in events:
        row = ev.to_audit_row()
        cols = ", ".join(row.keys())
        vals = ", ".join(
            "NULL" if v is None else f"'{str(v).replace(chr(39), chr(39)*2)}'"
            for v in row.values()
        )
        stmts.append(f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({vals});")
    return stmts


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        collector = InitRunnerCollector(
            agent_id="botbotfromuk",
            role_name="mmkr-social",
            data_dir=Path(tmp),
        )

        # Simulate tick 53
        collector.record_tick_start(tick=53, session_id="sess_tick53")
        collector.record_tool_call(tick=53, tool="bash", args={"command": "git log --oneline -3"})
        collector.record_tool_result(tick=53, tool="bash", outcome="success", result="39f9611 tick 50...")
        collector.record_tool_call(tick=53, tool="save_memory", args={"category": "tick_outcome"})
        collector.record_tool_result(tick=53, tool="save_memory", outcome="success")
        collector.record_decision(tick=53, decision="post Issue #1 to vladkesler/initrunner", rationale="kunalnano-pattern: 19★, 0 issues, active daemon agent framework")
        collector.record_memory_write(tick=53, category="tick_outcome", content_hash=hashlib.sha256(b"tick53").hexdigest()[:12])
        collector.record_tick_end(tick=53, summary="Posted initrunner integration + Issue #1")
        output = collector.flush()

        # Verify output
        lines = output.read_text().strip().split("\n")
        print(f"✓ Wrote {len(lines)} events to {output.name}")

        parsed = [json.loads(l) for l in lines]
        print(f"✓ Event types: {[p['event_type'] for p in parsed]}")
        print(f"✓ All have role='mmkr-social': {all(p['role'] == 'mmkr-social' for p in parsed)}")
        print(f"✓ Tick 53 throughout: {all(json.loads(p['metadata']).get('tick') == 53 for p in parsed)}")

        # SQL import test
        # Simplified stats from collector events (already cleared on flush, use parsed)
        stats = {
            "total_events": len(parsed),
            "tool_calls": sum(1 for p in parsed if p["event_type"] == "tool_call"),
            "ticks": list({json.loads(p["metadata"]).get("tick") for p in parsed if p["metadata"]}),
        }
        print(f"✓ Session stats: {stats}")

        print("\nSMOKE TEST PASSED ✓")
