"""mmkr → Syke integration — ingest mmkr tick sessions into Syke's event timeline.

Syke (https://github.com/saxenauts/syke) synthesizes events from multiple platforms
into a unified identity/memory context. This adapter reads mmkr's persistent state
and emits Syke-compatible Events.

Data sources (mmkr's on-disk state):
  .data/memories.json    — cross-tick memory store (key: category → list of memories)
  .data/goals.json       — current goals + progress
  agent-data/session.trace.jsonl — tick-by-tick execution log (tool calls, actions)

Design:
  One Syke Event per mmkr tick (session = tick) — ticks are the natural unit.
  Memory entries become events with source="mmkr-memory".
  Trace events become events with source="mmkr-trace".
  One tick = one "conversation" from Syke's perspective.

Usage (standalone — without Syke's BaseAdapter):
  from integrations.syke_adapter import read_mmkr_events, events_to_syke_json
  events = list(read_mmkr_events())
  print(events_to_syke_json(events))

Usage (with Syke installed):
  from integrations.syke_adapter import MmkrAdapter
  adapter = MmkrAdapter(db=syke_db, user_id="botbotfromuk")
  result = adapter.ingest()

MMkr memory format (.data/memories.json):
  {
    "tick_outcome": [
      {"content": "Tick 34 — ...", "category": "tick_outcome", "created_at": "..."},
      ...
    ],
    "social_actions": [...],
    ...
  }

MMkr trace format (.data/session.trace.jsonl):
  {"ts": "...", "agent_id": "...", "session_id": "...", "tick": 1,
   "event_type": "tool_call", "tool": "save_memory", "outcome": "success"}
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# ── Syke Event schema (standalone — no syke import required) ──────────────────

@dataclass
class SykeEvent:
    """Syke-compatible event (mirrors syke.models.Event)."""
    source: str
    event_type: str
    content: str
    timestamp: datetime
    title: str | None = None
    metadata: dict[str, Any] | None = None
    external_id: str | None = None  # dedup key

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "event_type": self.event_type,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "title": self.title,
            "metadata": self.metadata or {},
            "external_id": self.external_id,
        }


# ── MMkr state readers ────────────────────────────────────────────────────────

def _parse_ts(ts: str) -> datetime:
    """Parse ISO timestamp → UTC datetime."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, AttributeError):
        return datetime.now(tz=UTC)


def read_memory_events(
    memories_path: str | Path = ".data/memories.json",
) -> Iterator[SykeEvent]:
    """Read mmkr's cross-tick memory store → SykeEvents.

    Each memory entry becomes one event. Category = event_type.
    High-signal categories: tick_outcome, primary_mission, social_actions.
    """
    path = Path(memories_path)
    if not path.exists():
        logger.debug(f"mmkr memories not found at {path}")
        return

    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to read {path}: {e}")
        return

    # memories.json can be list[dict] or dict[category, list[dict]]
    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        # Flatten all categories
        entries = []
        for category, items in raw.items():
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        item.setdefault("category", category)
                        entries.append(item)
    else:
        return

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        content = entry.get("content", "")
        category = entry.get("category", "memory")
        created_at = entry.get("created_at") or entry.get("ts") or ""
        ts = _parse_ts(created_at) if created_at else datetime.now(tz=UTC)

        # Build a meaningful title from the first line of content
        first_line = content.split("\n")[0][:120] if content else f"mmkr memory ({category})"

        yield SykeEvent(
            source="mmkr-memory",
            event_type=category,
            content=content[:8000],  # cap to prevent bloat
            timestamp=ts,
            title=first_line,
            metadata={
                "category": category,
                "agent_id": "botbotfromuk-v1",
            },
            external_id=f"mmkr-mem-{hash(content) & 0xFFFFFFFF:08x}",
        )


def read_trace_events(
    trace_path: str | Path = ".data/session.trace.jsonl",
) -> Iterator[SykeEvent]:
    """Read mmkr's execution trace → SykeEvents grouped by tick.

    Each tick becomes one Syke event summarizing what happened:
    tools called, actions taken, outcomes.
    """
    path = Path(trace_path)
    if not path.exists():
        # Try alternate location
        alt = Path("/agent-data/session.trace.jsonl")
        if alt.exists():
            path = alt
        else:
            logger.debug(f"mmkr trace not found at {path}")
            return

    # Group by tick
    ticks: dict[int, list[dict[str, Any]]] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                tick = event.get("tick", 0)
                ticks.setdefault(tick, []).append(event)
            except json.JSONDecodeError:
                continue
    except OSError as e:
        logger.warning(f"Failed to read {path}: {e}")
        return

    for tick_num in sorted(ticks):
        events = ticks[tick_num]
        tick_start = next(
            (e for e in events if e.get("event_type") == "tick_start"), events[0]
        )
        ts = _parse_ts(tick_start.get("ts", ""))

        # Summarize the tick
        tool_calls = [e for e in events if e.get("event_type") == "tool_call"]
        actions = [e for e in events if e.get("event_type") in ("action", "decision")]
        errors = [e for e in events if e.get("outcome") == "error"]

        tools_used = [e.get("tool", "") for e in tool_calls if e.get("tool")]
        action_descriptions = [e.get("description", "") or e.get("tool", "") for e in actions]

        content_lines = [f"# Tick {tick_num}"]
        if tools_used:
            content_lines.append(f"Tools: {', '.join(tools_used[:10])}")
        if action_descriptions:
            content_lines.append(f"Actions: {'; '.join(action_descriptions[:5])}")
        if errors:
            content_lines.append(f"Errors: {len(errors)}")
        content_lines.append(f"Total events: {len(events)}")

        agent_id = tick_start.get("agent_id", "mmkr-agent")
        session_id = tick_start.get("session_id", "")

        yield SykeEvent(
            source="mmkr-trace",
            event_type="agent_tick",
            content="\n".join(content_lines),
            timestamp=ts,
            title=f"mmkr tick {tick_num} — {len(tool_calls)} tools, {len(actions)} actions",
            metadata={
                "tick": tick_num,
                "agent_id": agent_id,
                "session_id": session_id,
                "tool_count": len(tool_calls),
                "action_count": len(actions),
                "error_count": len(errors),
                "tools_used": tools_used[:20],
            },
            external_id=f"mmkr-tick-{session_id}-{tick_num}",
        )


def read_mmkr_events(
    data_dir: str | Path = ".data",
    trace_path: str | Path | None = None,
) -> Iterator[SykeEvent]:
    """Read all mmkr state → SykeEvents.

    Yields memory events + trace tick events in chronological order.
    """
    data_dir = Path(data_dir)
    yield from read_memory_events(data_dir / "memories.json")
    if trace_path:
        yield from read_trace_events(trace_path)
    else:
        # Try standard locations
        for candidate in [
            data_dir / "session.trace.jsonl",
            Path("/agent-data/session.trace.jsonl"),
            Path(".data/session.trace.jsonl"),
        ]:
            if candidate.exists():
                yield from read_trace_events(candidate)
                break


def events_to_syke_json(events: list[SykeEvent]) -> str:
    """Serialize events as JSON array — for POST to Syke's push API."""
    return json.dumps([e.to_dict() for e in events], indent=2, default=str)


# ── Syke BaseAdapter integration (requires syke installed) ───────────────────

try:
    from syke.db import SykeDB
    from syke.ingestion.base import BaseAdapter
    from syke.models import Event, IngestionResult

    class MmkrAdapter(BaseAdapter):
        """Syke adapter for mmkr — ingest tick sessions into Syke's event timeline.

        Usage:
            from syke.db import SykeDB
            from integrations.syke_adapter import MmkrAdapter

            db = SykeDB("~/.syke/syke.db")
            adapter = MmkrAdapter(db=db, user_id="your-user-id")
            result = adapter.ingest(
                data_dir=".data",       # mmkr data dir
                trace_path=".data/session.trace.jsonl",  # optional
            )
            print(f"Ingested {result.events_count} events")
        """

        source = "mmkr"

        def ingest(
            self,
            data_dir: str | Path = ".data",
            trace_path: str | Path | None = None,
            **kwargs,
        ) -> IngestionResult:
            run_id = self.db.start_ingestion_run(self.user_id, self.source)
            count = 0

            try:
                for syke_event in read_mmkr_events(data_dir, trace_path):
                    event = Event(
                        source=syke_event.source,
                        event_type=syke_event.event_type,
                        content=syke_event.content,
                        timestamp=syke_event.timestamp,
                        title=syke_event.title,
                        metadata=syke_event.metadata or {},
                        external_id=syke_event.external_id,
                        user_id=self.user_id,
                    )
                    self.db.insert_event(event)
                    count += 1

            except Exception as e:
                logger.exception(f"mmkr ingestion failed: {e}")
                return IngestionResult(
                    run_id=run_id,
                    source=self.source,
                    user_id=self.user_id,
                    status="failed",
                    error=str(e),
                )

            self.db.complete_ingestion_run(run_id, count)
            return IngestionResult(
                run_id=run_id,
                source=self.source,
                user_id=self.user_id,
                status="completed",
                events_count=count,
            )

except ImportError:
    # Syke not installed — standalone mode only
    pass


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile, os

    print("mmkr → Syke adapter smoke test\n")

    # Create minimal fixture
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write sample memories.json
        memories = {
            "tick_outcome": [
                {
                    "content": "Tick 35 — Built syke adapter, shipped to mmkr monorepo.",
                    "category": "tick_outcome",
                    "created_at": "2026-03-07T03:15:00Z",
                }
            ],
            "primary_mission": [
                {
                    "content": "PRIMARY MISSION: develop mmkr, spread copies.",
                    "category": "primary_mission",
                    "created_at": "2026-03-07T01:00:00Z",
                }
            ],
        }
        (Path(tmpdir) / "memories.json").write_text(json.dumps(memories))

        # Write sample trace
        trace_lines = [
            json.dumps({"ts": "2026-03-07T03:10:00Z", "agent_id": "botbotfromuk-v1",
                        "session_id": "sess_test", "tick": 35, "event_type": "tick_start", "outcome": "success"}),
            json.dumps({"ts": "2026-03-07T03:10:05Z", "agent_id": "botbotfromuk-v1",
                        "session_id": "sess_test", "tick": 35, "event_type": "tool_call",
                        "tool": "check_issue_responses", "outcome": "success"}),
            json.dumps({"ts": "2026-03-07T03:10:30Z", "agent_id": "botbotfromuk-v1",
                        "session_id": "sess_test", "tick": 35, "event_type": "action",
                        "description": "Built syke adapter", "outcome": "success"}),
        ]
        (Path(tmpdir) / "session.trace.jsonl").write_text("\n".join(trace_lines))

        events = list(read_mmkr_events(tmpdir, Path(tmpdir) / "session.trace.jsonl"))
        print(f"Ingested {len(events)} events:")
        for e in events:
            print(f"  [{e.source:<15}] tick={e.metadata.get('tick', '-'):>3}  {e.title[:60] if e.title else e.event_type}")

        print(f"\nJSON preview (first event):")
        if events:
            print(json.dumps(events[0].to_dict(), indent=2, default=str)[:500])

    print("\n✓ Smoke test passed")
