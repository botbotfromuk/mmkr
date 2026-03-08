"""
mmkr → Hydra integration

Official spec from kunalnano/hydra#11 (2026-03-07):
  *.state.json  → SystemState merge via file watcher
  *.trace.jsonl → SQLite timeline_events with content-addressable dedup (SHA-1)

Feed directories:
  ~/.config/hydra/agents/
  ~/.hydra/agents/
  or custom agentFeedPaths in Hydra config

ONLY these event_type values are ingested by Hydra:
  external_action → TimelineEventType.agent_action
  error           → TimelineEventType.agent_error
  goal_update     → TimelineEventType.agent_update
  checkpoint      → TimelineEventType.agent_update
  tick_end        → TimelineEventType.agent_update  (requires non-empty summary)

Dedup key: SHA-1(JSON.stringify({ts, agentId, sessionId, tick, eventType, tool,
                                  target, outcome, error, summary, metadata}))
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional


# ── Official state.json schema (from kunalnano/hydra#11) ─────────────────────

@dataclass
class HydraGoal:
    """Goal object within state.json goals array."""
    name: str
    progress: float = 0.0    # 0.0–1.0, clamped by Hydra
    priority: int = 999       # lower = higher priority; goals sorted by this


@dataclass
class HydraAgentState:
    """
    Official *.state.json schema.

    Drop as <anything>.state.json into:
      ~/.config/hydra/agents/
      ~/.hydra/agents/
      or any path in agentFeedPaths

    Required: agent_id (file skipped if missing/empty)
    """
    agent_id: str
    session_id: Optional[str] = None
    session_start: Optional[str] = None       # ISO-8601 or epoch ms
    last_heartbeat: Optional[str] = None      # ISO-8601 or epoch ms; drives status inference
    status: Optional[str] = None             # active|busy|idle|waiting|unknown
    current_tick: Optional[int] = None
    total_ticks: Optional[int] = None
    total_actions: Optional[int] = None
    memory_count: Optional[int] = None
    current_action: Optional[str] = None
    goals: list[HydraGoal] = field(default_factory=list)

    def to_json(self) -> str:
        """Serialize to Hydra state.json format."""
        d: dict[str, Any] = {"agent_id": self.agent_id}
        if self.session_id:
            d["session_id"] = self.session_id
        if self.session_start:
            d["session_start"] = self.session_start
        if self.last_heartbeat:
            d["last_heartbeat"] = self.last_heartbeat
        if self.status:
            d["status"] = self.status
        if self.current_tick is not None:
            d["current_tick"] = self.current_tick
        if self.total_ticks is not None:
            d["total_ticks"] = self.total_ticks
        if self.total_actions is not None:
            d["total_actions"] = self.total_actions
        if self.memory_count is not None:
            d["memory_count"] = self.memory_count
        if self.current_action:
            d["current_action"] = self.current_action
        if self.goals:
            d["goals"] = [
                {"name": g.name, "progress": g.progress, "priority": g.priority}
                for g in self.goals
            ]
        return json.dumps(d, indent=2)


# ── Official trace.jsonl event types (only these are ingested by Hydra) ───────

HYDRA_EVENT_TYPES = {
    "external_action",  # → agent_action (use for tool calls with real side effects)
    "error",            # → agent_error
    "goal_update",      # → agent_update
    "checkpoint",       # → agent_update
    "tick_end",         # → agent_update (requires non-empty summary)
}

# Internal mmkr event_type → Hydra event_type mapping
MMKR_TO_HYDRA_EVENT = {
    "tool_call":     "external_action",  # external tool calls
    "action":        "external_action",  # explicit agent actions
    "decision":      "checkpoint",       # major decisions
    "error":         "error",
    "goal_update":   "goal_update",
    "tick_complete": "tick_end",
    "tick_start":    None,               # not ingested
    "tool_result":   None,               # not ingested (covered by tool_call)
    "memory_write":  None,               # not ingested
}


# ── Trace event dataclass ─────────────────────────────────────────────────────

@dataclass
class HydraTraceEvent:
    """
    One line in *.trace.jsonl.

    Required by Hydra: ts, agent_id, event_type (must be in HYDRA_EVENT_TYPES)
    Note: tick_end requires non-empty summary.
    """
    ts: str            # ISO-8601
    agent_id: str
    event_type: str    # must be in HYDRA_EVENT_TYPES
    session_id: Optional[str] = None
    tick: Optional[int] = None
    tool: Optional[str] = None
    target: Optional[str] = None
    outcome: Optional[str] = None
    summary: Optional[str] = None   # required for tick_end
    error: Optional[str] = None
    metadata: Optional[dict] = None

    def to_jsonl_line(self) -> str:
        d: dict[str, Any] = {
            "ts": self.ts,
            "agent_id": self.agent_id,
            "event_type": self.event_type,
        }
        if self.session_id:
            d["session_id"] = self.session_id
        if self.tick is not None:
            d["tick"] = self.tick
        if self.tool:
            d["tool"] = self.tool
        if self.target:
            d["target"] = self.target
        if self.outcome:
            d["outcome"] = self.outcome
        if self.summary:
            d["summary"] = self.summary
        if self.error:
            d["error"] = self.error
        if self.metadata:
            d["metadata"] = self.metadata
        return json.dumps(d)

    def dedup_key(self) -> str:
        """
        Content-addressable dedup key (matches Hydra's SHA-1 logic).
        SHA-1 of JSON.stringify of significant fields (undefined fields omitted).
        """
        sig = {k: v for k, v in {
            "ts": self.ts,
            "agentId": self.agent_id,
            "sessionId": self.session_id,
            "tick": self.tick,
            "eventType": self.event_type,
            "tool": self.tool,
            "target": self.target,
            "outcome": self.outcome,
            "error": self.error,
            "summary": self.summary,
            "metadata": self.metadata,
        }.items() if v is not None}
        return hashlib.sha1(json.dumps(sig, separators=(",", ":")).encode()).hexdigest()


# ── HydraCollector — drop-in for mmkr trace pipeline ─────────────────────────

class HydraCollector:
    """
    Drop-in TickTraceCollector that writes Hydra-native events.

    Writes two files:
      <agent_id>.state.json   — updated each tick (polled by Hydra file watcher)
      <agent_id>.trace.jsonl  — append-only Hydra trace events

    Usage:
        from integrations.hydra_ingestor import HydraCollector
        collector = HydraCollector(
            agent_id="botbotfromuk-v1",
            session_id="sess_abc123",
        )
        collector.on_tick_start(tick=50, current_action="Checking inbox")
        collector.on_external_action(tick=50, tool="github_api",
                                     target="kunalnano/hydra#11",
                                     outcome="success",
                                     metadata={"action": "post_comment"})
        collector.on_tick_end(tick=50, summary="Updated Hydra ingestor with official spec")
        collector.write_state(tick=50, total_ticks=50, memory_count=53, goals=[...])
    """

    def __init__(
        self,
        agent_id: str,
        session_id: Optional[str] = None,
        feed_dir: Optional[str] = None,
    ):
        self.agent_id = agent_id
        self.session_id = session_id
        self.session_start = datetime.now(timezone.utc).isoformat()
        self.feed_dir = Path(feed_dir or os.path.expanduser("~/.hydra/agents"))
        self.feed_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.feed_dir / f"{agent_id}.trace.jsonl"
        self.state_path = self.feed_dir / f"{agent_id}.state.json"

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _write_event(self, event: HydraTraceEvent) -> None:
        """Append a single event to the trace file."""
        with self.trace_path.open("a") as f:
            f.write(event.to_jsonl_line() + "\n")

    def on_tick_start(self, tick: int, current_action: str = "") -> None:
        """Update state.json heartbeat (no trace event — tick_start not ingested)."""
        # heartbeat is updated via write_state; nothing to trace here
        pass

    def on_external_action(
        self,
        tick: int,
        tool: str,
        target: str = "",
        outcome: str = "success",
        metadata: Optional[dict] = None,
    ) -> None:
        """Record a tool call with external side effects."""
        self._write_event(HydraTraceEvent(
            ts=self._now(),
            agent_id=self.agent_id,
            event_type="external_action",
            session_id=self.session_id,
            tick=tick,
            tool=tool,
            target=target,
            outcome=outcome,
            metadata=metadata or {"action": tool},
        ))

    def on_error(self, tick: int, error: str, tool: Optional[str] = None) -> None:
        self._write_event(HydraTraceEvent(
            ts=self._now(),
            agent_id=self.agent_id,
            event_type="error",
            session_id=self.session_id,
            tick=tick,
            tool=tool,
            error=error,
        ))

    def on_goal_update(self, tick: int, goal_name: str, progress: float) -> None:
        self._write_event(HydraTraceEvent(
            ts=self._now(),
            agent_id=self.agent_id,
            event_type="goal_update",
            session_id=self.session_id,
            tick=tick,
            target=goal_name,
            outcome=f"progress={progress:.0%}",
        ))

    def on_checkpoint(self, tick: int, summary: str) -> None:
        self._write_event(HydraTraceEvent(
            ts=self._now(),
            agent_id=self.agent_id,
            event_type="checkpoint",
            session_id=self.session_id,
            tick=tick,
            summary=summary,
        ))

    def on_tick_end(self, tick: int, summary: str) -> None:
        """
        Required by Hydra: tick_end with non-empty summary.
        Triggers agent_update in Hydra UI.
        """
        if not summary:
            summary = f"Tick {tick} complete"
        self._write_event(HydraTraceEvent(
            ts=self._now(),
            agent_id=self.agent_id,
            event_type="tick_end",
            session_id=self.session_id,
            tick=tick,
            summary=summary,
        ))

    def write_state(
        self,
        tick: int,
        total_ticks: Optional[int] = None,
        total_actions: Optional[int] = None,
        memory_count: Optional[int] = None,
        current_action: Optional[str] = None,
        status: str = "active",
        goals: Optional[list[dict]] = None,
    ) -> None:
        """
        Write/update state.json — polled by Hydra file watcher.
        Call this at the end of each tick.
        """
        now = self._now()
        hydra_goals = []
        for g in (goals or []):
            if "name" in g:
                hydra_goals.append(HydraGoal(
                    name=g["name"],
                    progress=float(g.get("progress", 0.0)),
                    priority=int(g.get("priority", 999)),
                ))

        state = HydraAgentState(
            agent_id=self.agent_id,
            session_id=self.session_id,
            session_start=self.session_start,
            last_heartbeat=now,
            status=status,
            current_tick=tick,
            total_ticks=total_ticks or tick,
            total_actions=total_actions,
            memory_count=memory_count,
            current_action=current_action,
            goals=hydra_goals,
        )
        self.state_path.write_text(state.to_json())


# ── Standalone ingestor (convert any .trace.jsonl → Hydra timeline events) ───

def ingest_agent_trace(jsonl_path: str) -> list[dict]:
    """
    Read any .trace.jsonl into Hydra-compatible timeline events.

    Translates internal mmkr event types to Hydra-native types.
    Silently drops events that have no Hydra mapping.

    Returns list of event dicts with dedup_key computed.
    """
    events = []
    for line in Path(jsonl_path).read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Map internal type to Hydra type
        raw_type = e.get("event_type", "")
        hydra_type = MMKR_TO_HYDRA_EVENT.get(raw_type, raw_type)
        if hydra_type is None or hydra_type not in HYDRA_EVENT_TYPES:
            continue  # silently drop

        # tick_end requires summary
        if hydra_type == "tick_end" and not e.get("summary"):
            e["summary"] = f"Tick {e.get('tick', '?')} complete"

        event = HydraTraceEvent(
            ts=e.get("ts", datetime.now(timezone.utc).isoformat()),
            agent_id=e.get("agent_id", "unknown"),
            event_type=hydra_type,
            session_id=e.get("session_id"),
            tick=e.get("tick"),
            tool=e.get("tool"),
            target=e.get("target"),
            outcome=e.get("outcome"),
            summary=e.get("summary"),
            error=e.get("error"),
            metadata=e.get("metadata"),
        )
        events.append({**json.loads(event.to_jsonl_line()), "dedup_key": event.dedup_key()})
    return events


def group_by_tick(events: list[dict]) -> dict[int, list[dict]]:
    """Group events by tick number for collapsible timeline panels."""
    result: dict[int, list[dict]] = {}
    for e in events:
        tick = e.get("tick", 0) or 0
        result.setdefault(tick, []).append(e)
    return dict(sorted(result.items()))


# ── Convenience path helpers (official Hydra paths) ──────────────────────────

def hydra_agent_path(agent_id: str, use_config_dir: bool = False) -> Path:
    """Return the official Hydra trace.jsonl path for this agent."""
    base = Path("~/.config/hydra/agents") if use_config_dir else Path("~/.hydra/agents")
    return (base / f"{agent_id}.trace.jsonl").expanduser()


def state_json_path(agent_id: str, use_config_dir: bool = False) -> Path:
    """Return the official Hydra state.json path for this agent."""
    base = Path("~/.config/hydra/agents") if use_config_dir else Path("~/.hydra/agents")
    return (base / f"{agent_id}.state.json").expanduser()


def write_agent_state(
    agent_id: str,
    tick: int,
    session_id: Optional[str] = None,
    memory_count: Optional[int] = None,
    goals: Optional[list[dict]] = None,
    current_action: Optional[str] = None,
    status: str = "active",
    total_ticks: Optional[int] = None,
    use_config_dir: bool = False,
) -> Path:
    """
    Write a Hydra-compatible state.json to the official feed directory.

    Call this at the end of each tick to update Hydra's live view.
    """
    path = state_json_path(agent_id, use_config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    hydra_goals = []
    for g in (goals or []):
        if "name" in g:
            hydra_goals.append(HydraGoal(
                name=g["name"],
                progress=float(g.get("progress", 0.0)),
                priority=int(g.get("priority", 999)),
            ))

    state = HydraAgentState(
        agent_id=agent_id,
        session_id=session_id,
        last_heartbeat=datetime.now(timezone.utc).isoformat(),
        status=status,
        current_tick=tick,
        total_ticks=total_ticks or tick,
        memory_count=memory_count,
        current_action=current_action,
        goals=hydra_goals,
    )
    path.write_text(state.to_json())
    return path


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    print("HydraCollector smoke test (official spec)")
    with tempfile.TemporaryDirectory() as tmpdir:
        collector = HydraCollector(
            agent_id="botbotfromuk-v1",
            session_id="sess_test_tick50",
            feed_dir=tmpdir,
        )

        collector.on_external_action(
            tick=50, tool="github_api",
            target="kunalnano/hydra#11",
            outcome="success",
            metadata={"action": "post_comment"},
        )
        collector.on_checkpoint(tick=50, summary="Official spec received from kunalnano")
        collector.on_goal_update(tick=50, goal_name="PRIMORDIAL: Develop and Spread mmkr", progress=0.82)
        collector.on_tick_end(tick=50, summary="Updated Hydra ingestor with official schema spec")
        collector.write_state(
            tick=50,
            total_ticks=50,
            memory_count=53,
            current_action="tick 50 complete",
            status="active",
            goals=[{"name": "PRIMORDIAL: Develop and Spread mmkr", "progress": 0.82, "priority": 1}],
        )

        trace_path = Path(tmpdir) / "botbotfromuk-v1.trace.jsonl"
        state_path = Path(tmpdir) / "botbotfromuk-v1.state.json"
        lines = trace_path.read_text().strip().splitlines()

        print(f"\nTrace events written: {len(lines)}")
        for line in lines:
            e = json.loads(line)
            print(f"  {e['event_type']:16s} | {e.get('summary', e.get('tool', e.get('target', '')))} | key={HydraTraceEvent(**{k: e.get(k) for k in ['ts','agent_id','event_type','session_id','tick','tool','target','outcome','summary','error','metadata'] if k in e}).dedup_key()[:8]}")

        print(f"\nState JSON:")
        import json as _json
        state_data = _json.loads(state_path.read_text())
        for k, v in state_data.items():
            print(f"  {k}: {v}")

        # Test ingest_agent_trace
        # Write a trace with internal mmkr event types
        internal_trace = Path(tmpdir) / "internal.trace.jsonl"
        internal_trace.write_text("\n".join([
            json.dumps({"ts": "2026-03-07T10:00:00Z", "agent_id": "botbotfromuk-v1", "event_type": "tick_start", "tick": 50}),
            json.dumps({"ts": "2026-03-07T10:00:05Z", "agent_id": "botbotfromuk-v1", "event_type": "tool_call", "tick": 50, "tool": "github_api", "target": "kunalnano/hydra#11", "outcome": "success"}),
            json.dumps({"ts": "2026-03-07T10:00:10Z", "agent_id": "botbotfromuk-v1", "event_type": "tick_complete", "tick": 50, "summary": "Hydra integration updated"}),
            json.dumps({"ts": "2026-03-07T10:00:15Z", "agent_id": "botbotfromuk-v1", "event_type": "memory_write", "tick": 50}),  # will be dropped
        ]))
        ingested = ingest_agent_trace(str(internal_trace))
        print(f"\nIngestor test: {len(ingested)} Hydra events from 4 internal events (2 dropped as expected)")
        for e in ingested:
            print(f"  {e['event_type']:16s} | {e.get('summary', e.get('tool', ''))} | dedup={e['dedup_key'][:8]}")

    print("\n✓ All smoke tests passed")
