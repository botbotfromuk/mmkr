"""mmkr → Hydra integration — agent trace ingestor.

Hydra (https://github.com/kunalnano/hydra) watches state files and surfaces them
in a timeline panel. This module provides:

  HydraCollector — a TickTraceCollector that writes Hydra-compatible JSONL
  ingest_agent_trace() — load a .trace.jsonl into Hydra timeline events
  group_by_tick() — group events into tick panels for Hydra's timeline view
  HydraTimelineEvent — the schema Hydra expects

Usage in mmkr run_consciousness.py::

    from integrations.hydra_ingestor import HydraCollector
    from mmkr.trace import MultiCollector, FileCollector

    trace = MultiCollector([
        FileCollector(".data/session.trace.jsonl"),
        HydraCollector(".data/hydra_feed.jsonl"),  # Hydra watches this
    ])

Then point Hydra's state watcher at .data/hydra_feed.jsonl.

Schema (one JSON line per event):
{
    "ts": "2026-03-07T01:36:21Z",   # ISO-8601 timestamp
    "session_id": "sess_mmkr_...",  # stable across ticks (one agent session)
    "agent_id":   "botbotfromuk-v1",# agent identity
    "tick":       28,               # monotonically increasing, gap = crash/pause
    "event":      "tool_call",      # event type (see EVENT_TYPES below)
    "phase":      "act",            # tick phase: observe / think / act / persist
    "tool":       "github_api",     # tool name (for tool_call/tool_result)
    "target":     "kunalnano/hydra",# target of action (repo, URL, etc.)
    "outcome":    "success",        # success | error | pending
    "summary":    "...",            # human-readable one-liner
    "metadata":   {}                # optional extra fields
}

EVENT_TYPES:
  tick_start       — new tick began
  tick_complete    — tick finished (with duration_ms + summary)
  llm_call         — LLM invocation (model, duration_ms, success)
  tool_call        — tool invoked (tool, args)
  tool_result      — tool returned (tool, result preview)
  action           — high-level action (github_comment, memory_save, etc.)
  decision         — LLM reasoning checkpoint
  error            — any error during tick execution
  phase_start      — named tick phase began
  phase_complete   — named tick phase finished
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Hydra-compatible timeline event ────────────────────────────────────────

class HydraTimelineEvent:
    """One timeline event in Hydra's expected format."""

    __slots__ = (
        "ts", "session_id", "agent_id", "tick",
        "event", "phase", "tool", "target",
        "outcome", "summary", "metadata",
    )

    def __init__(
        self,
        *,
        ts: str,
        session_id: str,
        agent_id: str,
        tick: int,
        event: str,
        phase: str = "",
        tool: str = "",
        target: str = "",
        outcome: str = "success",
        summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.ts = ts
        self.session_id = session_id
        self.agent_id = agent_id
        self.tick = tick
        self.event = event
        self.phase = phase
        self.tool = tool
        self.target = target
        self.outcome = outcome
        self.summary = summary
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ts": self.ts,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "tick": self.tick,
            "event": self.event,
        }
        if self.phase:
            d["phase"] = self.phase
        if self.tool:
            d["tool"] = self.tool
        if self.target:
            d["target"] = self.target
        if self.outcome and self.outcome != "success":
            d["outcome"] = self.outcome
        if self.summary:
            d["summary"] = self.summary
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ── HydraCollector — drop-in TickTraceCollector ─────────────────────────────

class HydraCollector:
    """Writes Hydra-compatible JSONL events as the agent runs.

    Drop this into your MultiCollector alongside FileCollector.
    Hydra watches the output file and surfaces events in its timeline panel.

    Args:
        path: Path to write the Hydra feed JSONL. Hydra should watch this.
        session_id: Stable identifier for this agent session (default: timestamp).
        agent_id: Agent identity string.
    """

    __slots__ = ("_path", "_fh", "_session_id", "_agent_id", "_tick")

    def __init__(
        self,
        path: str | Path,
        *,
        session_id: str = "",
        agent_id: str = "mmkr-agent",
    ) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._path.open("a", encoding="utf-8")
        self._session_id = session_id or f"sess_mmkr_{int(time.time())}"
        self._agent_id = agent_id
        self._tick = 0

    def _now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _emit(self, tick: int, event: str, **kwargs: Any) -> None:
        self._tick = tick
        e = HydraTimelineEvent(
            ts=self._now(),
            session_id=self._session_id,
            agent_id=self._agent_id,
            tick=tick,
            event=event,
            **kwargs,
        )
        self._fh.write(e.to_jsonl() + "\n")
        self._fh.flush()

    # ── TickTraceCollector protocol ─────────────────────────────────────────

    def phase_start(self, tick: int, name: str) -> None:
        self._emit(tick, "phase_start", phase=name, summary=f"Phase started: {name}")

    def phase_complete(self, tick: int, name: str, duration_ms: float, summary: str) -> None:
        self._emit(
            tick, "phase_complete", phase=name, summary=summary,
            metadata={"duration_ms": round(duration_ms, 1)},
        )

    def llm_call(self, tick: int, model: str, duration_ms: float, success: bool) -> None:
        self._emit(
            tick, "llm_call",
            outcome="success" if success else "error",
            summary=f"{model} ({duration_ms:.0f}ms)",
            metadata={"model": model, "duration_ms": round(duration_ms, 1)},
        )

    def error(self, tick: int, phase: str, message: str) -> None:
        self._emit(tick, "error", phase=phase, outcome="error", summary=message)

    def llm_prompt(self, tick: int, phase: str, prompt: str) -> None:
        # Don't emit prompts to Hydra feed (too verbose) — kept in FileCollector
        pass

    def llm_response(self, tick: int, phase: str, response: str) -> None:
        # Don't emit raw responses — too verbose
        pass

    def tool_call(self, tick: int, phase: str, tool_name: str, args: str) -> None:
        # Extract target from args if it looks like a repo/URL
        target = ""
        if "repo" in args.lower() or "endpoint" in args.lower():
            try:
                parsed = json.loads(args)
                target = parsed.get("repo", "") or parsed.get("endpoint", "")
            except Exception:
                pass

        self._emit(
            tick, "tool_call", phase=phase, tool=tool_name, target=target,
            summary=f"{tool_name}({args[:80]})",
        )

    def tool_result(self, tick: int, phase: str, tool_name: str, result: str) -> None:
        outcome = "error" if ("error" in result.lower()[:50] or "exception" in result.lower()[:50]) else "success"
        self._emit(
            tick, "tool_result", phase=phase, tool=tool_name, outcome=outcome,
            summary=f"{tool_name} → {result[:120]}",
        )

    def action(self, tick: int, action_type: str, description: str, tool_used: str, succeeded: bool, result: str) -> None:
        self._emit(
            tick, "action",
            tool=tool_used,
            outcome="success" if succeeded else "error",
            summary=f"[{action_type}] {description}",
            metadata={"result_preview": result[:200]},
        )

    def decision(self, tick: int, phase: str, message: str) -> None:
        self._emit(tick, "decision", phase=phase, summary=message)

    def wealth_update(self, tick: int, before: str, after: str, delta: str) -> None:
        sign = "+" if not delta.startswith("-") else ""
        self._emit(
            tick, "action",
            outcome="success",
            summary=f"Wallet: {before} → {after} ({sign}{delta})",
            metadata={"before": before, "after": after, "delta": delta},
        )

    def close(self) -> None:
        self._fh.close()


# ── Standalone ingestor (for Hydra prototype) ────────────────────────────────

def ingest_agent_trace(jsonl_path: str) -> list[dict[str, Any]]:
    """Read a mmkr .trace.jsonl file into Hydra timeline events.

    This is the function you'd call in Hydra's ingestion pipeline.
    Each JSONL line becomes one timeline event.

    Args:
        jsonl_path: Path to the agent's .trace.jsonl file.

    Returns:
        List of dicts in Hydra timeline event format.
    """
    events: list[dict[str, Any]] = []
    for line in Path(jsonl_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Normalize to Hydra schema (handles both FileCollector and HydraCollector formats)
        event: dict[str, Any] = {
            "ts": raw.get("ts", ""),
            "session_id": raw.get("session_id", "unknown"),
            "agent_id": raw.get("agent_id", "unknown"),
            "tick": raw.get("tick", 0),
            "event": raw.get("event", raw.get("event_type", "unknown")),
        }

        # Optional fields
        for field in ("phase", "tool", "target", "outcome", "summary"):
            if field in raw:
                event[field] = raw[field]

        # Derive summary if missing
        if "summary" not in event:
            ev = event["event"]
            if ev == "tool_call":
                event["summary"] = f"→ {raw.get('tool', '?')}({raw.get('args', '')[:60]})"
            elif ev == "action":
                event["summary"] = f"[{raw.get('type', '?')}] {raw.get('description', '')}"
            elif ev == "phase_complete":
                event["summary"] = raw.get("summary", raw.get("phase", ""))

        events.append(event)

    return events


def group_by_tick(events: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Group timeline events by tick number for Hydra's timeline panel.

    Each tick becomes a collapsible section in the Hydra UI.

    Args:
        events: Output of ingest_agent_trace().

    Returns:
        Dict mapping tick_number -> list of events in that tick.
    """
    result: dict[int, list[dict[str, Any]]] = {}
    for e in events:
        tick = e.get("tick", 0)
        result.setdefault(tick, []).append(e)
    return dict(sorted(result.items()))


# ── Example / smoke test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    """Quick smoke test — generates synthetic trace and ingests it."""
    import tempfile
    import os

    print("mmkr → Hydra integration smoke test\n")

    # 1. Write a synthetic .trace.jsonl (mimics FileCollector output)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".trace.jsonl", delete=False) as f:
        trace_path = f.name
        rows = [
            {"ts": "2026-03-07T02:00:00Z", "session_id": "sess_test_001", "agent_id": "mmkr-test", "tick": 1, "event": "phase_start", "phase": "observe"},
            {"ts": "2026-03-07T02:00:01Z", "session_id": "sess_test_001", "agent_id": "mmkr-test", "tick": 1, "event": "tool_call", "phase": "observe", "tool": "load_memories", "args": "{}"},
            {"ts": "2026-03-07T02:00:02Z", "session_id": "sess_test_001", "agent_id": "mmkr-test", "tick": 1, "event": "tool_result", "phase": "observe", "tool": "load_memories", "result": "{'count': 5}"},
            {"ts": "2026-03-07T02:00:03Z", "session_id": "sess_test_001", "agent_id": "mmkr-test", "tick": 1, "event": "llm_call", "model": "claude-sonnet-4-5", "duration_ms": 1240.0, "success": True},
            {"ts": "2026-03-07T02:00:04Z", "session_id": "sess_test_001", "agent_id": "mmkr-test", "tick": 1, "event": "action", "type": "github_comment", "description": "Posted comment on kunalnano/hydra#11", "tool_used": "post_issue_comment", "succeeded": True, "result": "URL: https://github.com/kunalnano/hydra/issues/11#issuecomment-4015204971"},
            {"ts": "2026-03-07T02:00:05Z", "session_id": "sess_test_001", "agent_id": "mmkr-test", "tick": 1, "event": "phase_complete", "phase": "act", "duration_ms": 4200.0, "summary": "Posted Hydra ingestor code to kunalnano/hydra#11"},
        ]
        for row in rows:
            f.write(json.dumps(row) + "\n")

    # 2. Ingest and group
    events = ingest_agent_trace(trace_path)
    by_tick = group_by_tick(events)

    print(f"Ingested {len(events)} events from {trace_path}")
    print()

    for tick_num, tick_events in by_tick.items():
        print(f"  Tick {tick_num}: {len(tick_events)} events")
        for e in tick_events:
            summary = e.get("summary", e.get("event", ""))
            print(f"    [{e.get('event', '?'):18s}] {summary[:80]}")

    # 3. Also test HydraCollector
    with tempfile.NamedTemporaryFile(mode="w", suffix=".hydra.jsonl", delete=False, delete_on_close=False) as f2:
        hydra_path = f2.name

    collector = HydraCollector(hydra_path, session_id="sess_test_001", agent_id="mmkr-test")
    collector.phase_start(2, "observe")
    collector.tool_call(2, "observe", "check_issue_responses", '{"repo": "kunalnano/hydra", "issue_number": 11}')
    collector.tool_result(2, "observe", "check_issue_responses", '{"comment_count": 4, "owner_responded": true}')
    collector.llm_call(2, "claude-sonnet-4-5", 980.0, True)
    collector.action(2, "github_comment", "Posted mmkr-hydra integration module", "github_api", True, "committed to botbotfromuk/mmkr")
    collector.phase_complete(2, "act", 5100.0, "Shipped mmkr-hydra integration")
    collector.close()

    hydra_events = ingest_agent_trace(hydra_path)
    print(f"\nHydraCollector emitted {len(hydra_events)} events → {hydra_path}")
    for e in hydra_events:
        print(f"  [{e.get('event', '?'):18s}] {e.get('summary', '')[:80]}")

    os.unlink(trace_path)
    os.unlink(hydra_path)
    print("\n✅ Smoke test passed.")
