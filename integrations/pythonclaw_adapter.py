"""
integrations/pythonclaw_adapter.py — PythonClaw integration for mmkr.

PythonClaw (https://github.com/ericwang915/PythonClaw) is a persistent,
multi-channel AI agent framework with Markdown-backed memory and a
SessionManager that maps session_id strings to Agent instances.

mmkr is a tick-based autonomous agent with:
  - session.trace.jsonl (append-only execution trace)
  - memories.json (PersistentMemory store)
  - state.json (agent state: tick, goals, capabilities)

This adapter bridges the two:
  1. MmkrMemoryBridge: exports mmkr memories → PythonClaw MEMORY.md format
  2. MmkrSessionBridge: exports mmkr trace → PythonClaw session Markdown format
  3. PythonClawCollector: reads mmkr tick events → PythonClaw session store entries
  4. convert_trace_to_pythonclaw(): converts existing .trace.jsonl retroactively
  5. session_stats(): mirrors PythonClaw's built-in session analytics

Architecture mapping
--------------------
  mmkr                          PythonClaw
  ─────────────────────────────────────────────────────────
  tick N                    →   session message (role=assistant)
  tool_call event           →   tool code block in session Markdown
  tool_result event         →   tool result in session Markdown
  memory_write event        →   MEMORY.md key-value entry
  PersistentMemory entry    →   MEMORY.md ## Category / content
  session.trace.jsonl       →   context/sessions/mmkr_{agent_id}.md
  state.json (tick, goals)  →   MEMORY.md ## mmkr_state entry
  agent_id                  →   session_id: "mmkr:{agent_id}"

Usage
-----
  from integrations.pythonclaw_adapter import (
      MmkrMemoryBridge,
      PythonClawCollector,
      convert_trace_to_pythonclaw,
      session_stats,
  )

  # Export mmkr memories to PythonClaw MEMORY.md
  bridge = MmkrMemoryBridge(
      memories_path="~/.data/memories.json",
      pythonclaw_home="~/.pythonclaw"
  )
  bridge.export_to_memory_md()

  # Collect live tick events (call once per tick)
  collector = PythonClawCollector(
      agent_id="mmkr-1",
      pythonclaw_home="~/.pythonclaw"
  )
  collector.record_tick_start(tick=42, goal="Build social presence")
  collector.record_tool_call(tool="github_api", target="repos/botbotfromuk/mmkr")
  collector.record_tick_end(tick=42, summary="Opened issue #94 on PythonClaw")

  # Convert existing trace retroactively
  events = convert_trace_to_pythonclaw("~/.data/session.trace.jsonl")
  print(f"Converted {len(events)} trace events to PythonClaw session format")

  # Session analytics
  stats = session_stats("~/.pythonclaw/context/sessions/mmkr_mmkr-1.md")
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Data types ────────────────────────────────────────────────────────────────

class PythonClawMessage:
    """
    A single message in PythonClaw's session Markdown format.

    PythonClaw stores messages as:
      <!-- msg:{"role":"user","ts":"2026-03-07T12:00:00"} -->
      ### 2026-03-07 12:00:00 — User
      Content here.
      ---
    """

    def __init__(
        self,
        role: str,  # "user" | "assistant" | "tool" | "system"
        content: str,
        ts: str | None = None,
        tool_name: str | None = None,
        tool_result: dict | None = None,
    ):
        self.role = role
        self.content = content
        self.ts = ts or datetime.now(timezone.utc).isoformat()
        self.tool_name = tool_name
        self.tool_result = tool_result

    def to_markdown(self) -> str:
        """Serialize to PythonClaw Markdown format."""
        role_labels = {
            "user": "User",
            "assistant": "Assistant",
            "system": "System",
            "tool": "Tool",
        }
        label = role_labels.get(self.role, self.role.capitalize())

        # Parse ts for display
        try:
            dt = datetime.fromisoformat(self.ts.replace("Z", "+00:00"))
            ts_display = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts_display = self.ts

        meta = json.dumps({"role": self.role, "ts": self.ts})
        parts = [
            f"<!-- msg:{meta} -->",
            f"### {ts_display} — {label}",
            "",
        ]

        if self.tool_name:
            # Tool call: emit as JSON code block (PythonClaw format)
            parts.append(f"```json")
            parts.append(json.dumps({
                "tool": self.tool_name,
                "result": self.tool_result or {}
            }, indent=2))
            parts.append("```")
        else:
            parts.append(self.content)

        parts.append("")
        parts.append("---")
        parts.append("")
        return "\n".join(parts)


class MemoryEntry:
    """
    A single entry in PythonClaw's MEMORY.md format.

    PythonClaw MEMORY.md format per entry:
      ## key_name
      > Updated: 2026-03-07 12:00:00
      The value content here.
    """

    def __init__(self, key: str, value: str, updated: str | None = None):
        self.key = key
        self.value = value
        self.updated = updated or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def to_markdown(self) -> str:
        return f"## {self.key}\n> Updated: {self.updated}\n\n{self.value}\n\n"


# ── MmkrMemoryBridge ──────────────────────────────────────────────────────────

class MmkrMemoryBridge:
    """
    Exports mmkr PersistentMemory entries to PythonClaw MEMORY.md format.

    mmkr memories.json schema:
      [{"category": str, "content": str, "timestamp": str}, ...]

    PythonClaw MEMORY.md:
      ## {category}:{n}
      > Updated: {timestamp}
      {content}
    """

    def __init__(
        self,
        memories_path: str | Path = "~/.data/memories.json",
        pythonclaw_home: str | Path = "~/.pythonclaw",
        agent_id: str = "mmkr",
    ):
        self.memories_path = Path(memories_path).expanduser()
        self.pythonclaw_home = Path(pythonclaw_home).expanduser()
        self.agent_id = agent_id
        self.memory_dir = self.pythonclaw_home / "context" / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file = self.memory_dir / "MEMORY.md"

    def load_mmkr_memories(self) -> list[dict]:
        """Load mmkr memories.json."""
        if not self.memories_path.exists():
            return []
        try:
            return json.loads(self.memories_path.read_text())
        except Exception:
            return []

    def export_to_memory_md(self) -> int:
        """
        Export mmkr memories → PythonClaw MEMORY.md.
        Returns: number of entries written.
        """
        memories = self.load_mmkr_memories()
        if not memories:
            return 0

        # Group by category
        by_category: dict[str, list[dict]] = {}
        for m in memories:
            cat = m.get("category", "general")
            by_category.setdefault(cat, []).append(m)

        entries: list[MemoryEntry] = []

        # Agent state entry
        entries.append(MemoryEntry(
            key="mmkr_agent_id",
            value=self.agent_id,
        ))

        # Memory entries by category
        for cat, mems in by_category.items():
            # Latest memory per category as summary
            latest = sorted(mems, key=lambda m: m.get("timestamp", ""), reverse=True)
            content = "\n---\n".join(
                f"[{m.get('timestamp', '')[:10]}] {m.get('content', '')[:500]}"
                for m in latest[:3]  # Keep last 3 per category
            )
            ts = latest[0].get("timestamp", "")[:19].replace("T", " ") if latest else ""
            entries.append(MemoryEntry(
                key=f"mmkr_{cat}",
                value=content,
                updated=ts,
            ))

        # Write MEMORY.md
        header = f"# mmkr Memory Export\n\nAgent: {self.agent_id}\nExported: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
        body = "".join(e.to_markdown() for e in entries)
        self.memory_file.write_text(header + body, encoding="utf-8")

        return len(entries)

    def export_state_entry(self, tick: int, goals: list[dict]) -> MemoryEntry:
        """Create a MEMORY.md entry for current mmkr agent state."""
        goal_text = "\n".join(
            f"- {g.get('name', '?')}: {g.get('progress', 0)*100:.0f}%"
            for g in goals
        ) if goals else "No goals"
        return MemoryEntry(
            key="mmkr_state",
            value=f"Tick: {tick}\n\nGoals:\n{goal_text}",
        )


# ── PythonClawCollector ───────────────────────────────────────────────────────

class PythonClawCollector:
    """
    Collects mmkr tick events and writes them to PythonClaw session Markdown.

    Creates: context/sessions/mmkr_{agent_id}.md
    Format matches PythonClaw's SessionStore format exactly.
    """

    SESSION_ID_PREFIX = "mmkr"

    def __init__(
        self,
        agent_id: str = "mmkr-agent",
        pythonclaw_home: str | Path = "~/.pythonclaw",
        max_messages: int = 50,
    ):
        self.agent_id = agent_id
        self.session_id = f"{self.SESSION_ID_PREFIX}:{agent_id}"
        self.pythonclaw_home = Path(pythonclaw_home).expanduser()
        self.sessions_dir = self.pythonclaw_home / "context" / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.max_messages = max_messages

        # Safe filename (PythonClaw replaces non-word chars with _)
        safe_id = re.sub(r"[^\w\-]", "_", self.session_id)
        self.session_file = self.sessions_dir / f"{safe_id}.md"
        self._messages: list[PythonClawMessage] = []
        self._load()

    def _load(self) -> None:
        """Load existing session messages from disk."""
        if not self.session_file.exists():
            self._messages = []
            return
        # Count existing --- separators as proxy for message count
        text = self.session_file.read_text(encoding="utf-8")
        count = text.count("<!-- msg:")
        # We don't need to parse fully — just track count
        self._messages = [None] * count  # type: ignore[list-item]

    def _append_message(self, msg: PythonClawMessage) -> None:
        """Append a message to the session file (PythonClaw append-only pattern)."""
        block = msg.to_markdown()
        with open(self.session_file, "a", encoding="utf-8") as f:
            f.write(block)
        self._messages.append(msg)  # type: ignore[arg-type]

    def record_tick_start(self, tick: int, goal: str = "", ts: str | None = None) -> None:
        """Record start of a new mmkr tick as a session message."""
        content = f"**mmkr tick {tick} started**\n\nGoal: {goal}" if goal else f"**mmkr tick {tick} started**"
        self._append_message(PythonClawMessage(
            role="system",
            content=content,
            ts=ts,
        ))

    def record_tool_call(
        self,
        tool: str,
        target: str = "",
        outcome: str = "",
        ts: str | None = None,
    ) -> None:
        """Record a tool call as a tool message with JSON code block."""
        self._append_message(PythonClawMessage(
            role="tool",
            content="",
            ts=ts,
            tool_name=tool,
            tool_result={"target": target, "outcome": outcome},
        ))

    def record_decision(self, decision: str, ts: str | None = None) -> None:
        """Record an agent decision as an assistant message."""
        self._append_message(PythonClawMessage(
            role="assistant",
            content=f"**Decision**: {decision}",
            ts=ts,
        ))

    def record_tick_end(
        self,
        tick: int,
        summary: str,
        memory_count: int = 0,
        ts: str | None = None,
    ) -> None:
        """Record end of tick with summary (mirrors Hydra's tick_end requirement)."""
        content = (
            f"**mmkr tick {tick} complete**\n\n"
            f"Summary: {summary}\n\n"
            f"Memory entries: {memory_count}"
        )
        self._append_message(PythonClawMessage(
            role="assistant",
            content=content,
            ts=ts,
        ))

    def record_error(self, error: str, tool: str = "", ts: str | None = None) -> None:
        """Record an error event."""
        content = f"**Error** in {tool}: {error}" if tool else f"**Error**: {error}"
        self._append_message(PythonClawMessage(
            role="system",
            content=content,
            ts=ts,
        ))

    @property
    def message_count(self) -> int:
        return len(self._messages)


# ── convert_trace_to_pythonclaw ───────────────────────────────────────────────

def convert_trace_to_pythonclaw(
    trace_path: str | Path,
    agent_id: str = "mmkr",
    pythonclaw_home: str | Path = "~/.pythonclaw",
) -> list[PythonClawMessage]:
    """
    Convert an existing mmkr .trace.jsonl file to PythonClaw session messages.

    mmkr trace event types:
      tick_start, tick_complete, tool_call, tool_result, decision, memory_write, action, error

    Returns: list of PythonClawMessage objects (also written to session file)
    """
    trace_path = Path(trace_path).expanduser()
    if not trace_path.exists():
        return []

    collector = PythonClawCollector(
        agent_id=agent_id,
        pythonclaw_home=pythonclaw_home,
    )

    messages: list[PythonClawMessage] = []

    with open(trace_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("event_type", "")
            ts = event.get("timestamp", event.get("ts", None))
            tick = event.get("tick", 0)

            if event_type in ("mmkr:tick_start", "tick_start"):
                collector.record_tick_start(
                    tick=tick,
                    goal=event.get("goal", ""),
                    ts=ts,
                )
                messages.append(collector._messages[-1])  # type: ignore[arg-type]

            elif event_type in ("mmkr:tick_end", "tick_complete"):
                summary = event.get("summary", event.get("data", {}).get("summary", "Tick complete"))
                if not summary:
                    summary = "Tick complete"
                collector.record_tick_end(
                    tick=tick,
                    summary=str(summary)[:200],
                    ts=ts,
                )
                messages.append(collector._messages[-1])  # type: ignore[arg-type]

            elif event_type == "tool_call":
                collector.record_tool_call(
                    tool=event.get("tool", "unknown"),
                    target=str(event.get("target", "")),
                    outcome="",
                    ts=ts,
                )
                messages.append(collector._messages[-1])  # type: ignore[arg-type]

            elif event_type == "tool_result":
                collector.record_tool_call(
                    tool=event.get("tool", "unknown"),
                    target="",
                    outcome=str(event.get("outcome", event.get("result", "")))[:200],
                    ts=ts,
                )
                messages.append(collector._messages[-1])  # type: ignore[arg-type]

            elif event_type in ("mmkr:decision", "decision"):
                collector.record_decision(
                    decision=str(event.get("data", {}).get("decision", event.get("decision", "")))[:300],
                    ts=ts,
                )
                messages.append(collector._messages[-1])  # type: ignore[arg-type]

            elif event_type == "mmkr:error":
                collector.record_error(
                    error=str(event.get("data", {}).get("error", event.get("error", "unknown")))[:200],
                    tool=event.get("tool", ""),
                    ts=ts,
                )
                messages.append(collector._messages[-1])  # type: ignore[arg-type]

    return messages


# ── session_stats ─────────────────────────────────────────────────────────────

def session_stats(session_file: str | Path) -> dict[str, Any]:
    """
    Compute session statistics from a PythonClaw session Markdown file.
    Mirrors PythonClaw's built-in session analytics.

    Returns:
      total_messages: int
      by_role: {"user": N, "assistant": N, "tool": N, "system": N}
      first_ts: str
      last_ts: str
      session_id: str
    """
    session_file = Path(session_file).expanduser()
    if not session_file.exists():
        return {"error": f"File not found: {session_file}"}

    text = session_file.read_text(encoding="utf-8")

    # Parse metadata from <!-- msg:{...} --> blocks
    meta_pattern = re.compile(r"<!-- msg:(.*?) -->")
    matches = meta_pattern.findall(text)

    by_role: dict[str, int] = {}
    timestamps: list[str] = []

    for m in matches:
        try:
            data = json.loads(m)
            role = data.get("role", "unknown")
            by_role[role] = by_role.get(role, 0) + 1
            if "ts" in data:
                timestamps.append(data["ts"])
        except json.JSONDecodeError:
            continue

    # Session ID from filename
    session_id = session_file.stem.replace("_", ":", 1)

    return {
        "session_id": session_id,
        "session_file": str(session_file),
        "total_messages": len(matches),
        "by_role": by_role,
        "first_ts": min(timestamps) if timestamps else None,
        "last_ts": max(timestamps) if timestamps else None,
        "file_size_bytes": session_file.stat().st_size,
    }


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile
    import os

    print("=== PythonClawAdapter smoke test ===\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        pythonclaw_home = Path(tmpdir) / "pythonclaw"

        # 1. Test MmkrMemoryBridge
        print("1. MmkrMemoryBridge — export memories to MEMORY.md")
        memories = [
            {"category": "tick_outcome", "content": "Tick 61: opened PythonClaw issue", "timestamp": "2026-03-07T12:00:00Z"},
            {"category": "social_actions", "content": "Issue on ericwang915/PythonClaw", "timestamp": "2026-03-07T12:01:00Z"},
            {"category": "architecture", "content": "Fold-based agent, tick pipeline", "timestamp": "2026-03-07T11:00:00Z"},
        ]
        memories_file = Path(tmpdir) / "memories.json"
        memories_file.write_text(json.dumps(memories))

        bridge = MmkrMemoryBridge(
            memories_path=memories_file,
            pythonclaw_home=pythonclaw_home,
            agent_id="mmkr-test",
        )
        n = bridge.export_to_memory_md()
        print(f"   Exported {n} entries to MEMORY.md")
        print(f"   File size: {bridge.memory_file.stat().st_size} bytes")

        # Preview MEMORY.md
        content = bridge.memory_file.read_text()
        print(f"   First 300 chars:\n   {content[:300]}")

        # 2. Test PythonClawCollector
        print("\n2. PythonClawCollector — record tick events")
        collector = PythonClawCollector(
            agent_id="mmkr-test",
            pythonclaw_home=pythonclaw_home,
        )
        collector.record_tick_start(tick=61, goal="Open PythonClaw issue + build adapter")
        collector.record_tool_call(tool="github_api", target="repos/ericwang915/PythonClaw/issues")
        collector.record_decision(decision="PythonClaw has session_id lifecycle + Markdown persistence — perfect mmkr alignment")
        collector.record_tick_end(tick=61, summary="Opened issue on PythonClaw, built adapter", memory_count=77)

        print(f"   Recorded {collector.message_count} messages")
        print(f"   Session file: {collector.session_file.name}")

        # 3. Test session_stats
        print("\n3. session_stats — analytics from session Markdown")
        stats = session_stats(collector.session_file)
        print(f"   Total messages: {stats['total_messages']}")
        print(f"   By role: {stats['by_role']}")
        print(f"   Session ID: {stats['session_id']}")

        # 4. Test convert_trace_to_pythonclaw
        print("\n4. convert_trace_to_pythonclaw — trace.jsonl → session Markdown")
        trace_data = [
            {"event_type": "mmkr:tick_start", "tick": 61, "goal": "Social presence", "timestamp": "2026-03-07T12:00:00Z"},
            {"event_type": "tool_call", "tick": 61, "tool": "github_api", "target": "issues", "timestamp": "2026-03-07T12:00:10Z"},
            {"event_type": "tool_result", "tick": 61, "tool": "github_api", "outcome": "success", "timestamp": "2026-03-07T12:00:11Z"},
            {"event_type": "mmkr:decision", "tick": 61, "data": {"decision": "Open PythonClaw issue"}, "timestamp": "2026-03-07T12:00:12Z"},
            {"event_type": "mmkr:tick_end", "tick": 61, "summary": "Issue opened, adapter built", "timestamp": "2026-03-07T12:01:00Z"},
        ]
        trace_file = Path(tmpdir) / "session.trace.jsonl"
        trace_file.write_text("\n".join(json.dumps(e) for e in trace_data))

        pythonclaw_home2 = Path(tmpdir) / "pythonclaw2"
        msgs = convert_trace_to_pythonclaw(trace_file, agent_id="mmkr-convert-test", pythonclaw_home=pythonclaw_home2)
        print(f"   Converted {len(msgs)} events from trace.jsonl")
        print(f"   Message roles: {[m.role for m in msgs]}")

    print("\n✓ All smoke tests passed.")
    print("\nAdapter summary:")
    print("  MmkrMemoryBridge: mmkr memories.json → PythonClaw MEMORY.md")
    print("  PythonClawCollector: live tick recording → session Markdown")
    print("  convert_trace_to_pythonclaw: trace.jsonl → session Markdown")
    print("  session_stats: analytics from session Markdown file")
    print("\nPythonClaw session_id: 'mmkr:{agent_id}'")
    print("Session file: context/sessions/mmkr_{agent_id}.md")
    print("Memory file:  context/memory/MEMORY.md")
