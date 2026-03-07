"""
integrations/pythonclaw_collector.py — PythonClaw integration for mmkr.

PythonClaw (https://github.com/ericwang915/PythonClaw) is an autonomous AI agent
with persistent Markdown-backed memory, Hybrid RAG (BM25 + dense + RRF), and
multi-channel support (Telegram, CLI, Web, Discord, WhatsApp).

This module bridges mmkr's tick-based execution trace to PythonClaw's:
  - SessionStore: Markdown-backed message history (context/sessions/<id>.md)
  - MemoryManager: long-term semantic memory (daily logs + recall)
  - SessionManager: session lifecycle and concurrency
  - Skill system: tool calls as skill invocations

Mapping:
  mmkr concept           → PythonClaw concept
  ─────────────────────────────────────────────
  agent_id               → session_id ("mmkr:<agent_id>")
  tick                   → message block (role=assistant + metadata)
  tool_call              → tool call JSON inside message block
  tool_result            → tool result JSON inside message block
  memory_write           → MemoryEntry in daily log
  decision               → assistant message with reasoning
  error                  → tool message with error content
  session.trace.jsonl    → SessionStore Markdown file (reconstructed)
  state.json             → PythonClaw session context (live state)
  mmkr capability        → PythonClaw skill (three-tier metadata)

Usage:
    collector = PythonClawCollector(agent_id="botbotfromuk", claw_home="~/.pythonclaw")
    
    # Record a tick
    collector.record_tick(tick=64, summary="Built PythonClaw integration", tools_used=3)
    
    # Convert existing trace file
    convert_trace_to_pythonclaw("session.trace.jsonl", agent_id="botbotfromuk")
    
    # Generate skill metadata for an mmkr capability
    skill_meta = capability_to_skill_metadata("cap_github_maintenance", fitness_score=3.78)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── PythonClaw SessionStore message format ─────────────────────────────────

@dataclass
class PythonClawMessage:
    """A single message in PythonClaw's Markdown-backed SessionStore format.
    
    Mirrors the <!-- msg:{...} --> metadata embedded in each message block.
    role: 'user' | 'assistant' | 'tool' | 'system'
    """
    role: str
    content: str
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    tool_calls: list[dict] = field(default_factory=list)  # if role == 'assistant'
    tool_call_id: str | None = None  # if role == 'tool'
    name: str | None = None  # if role == 'tool' (function name)
    
    def to_markdown_block(self) -> str:
        """Convert to PythonClaw's Markdown block format."""
        role_labels = {
            "user": "User",
            "assistant": "Assistant", 
            "system": "System",
            "tool": "Tool",
        }
        label = role_labels.get(self.role, self.role.capitalize())
        
        # Build metadata dict for HTML comment
        meta: dict[str, Any] = {"role": self.role, "ts": self.ts}
        if self.tool_call_id:
            meta["tool_call_id"] = self.tool_call_id
        if self.name:
            meta["name"] = self.name
            
        meta_json = json.dumps(meta, separators=(",", ":"))
        
        # Format timestamp for human-readable header
        try:
            dt = datetime.fromisoformat(self.ts.replace("Z", "+00:00"))
            ts_human = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts_human = self.ts
        
        lines = [
            f"<!-- msg:{meta_json} -->",
            f"### {ts_human} — {label}",
            "",
        ]
        
        if self.tool_calls:
            lines.append(self.content or "")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(self.tool_calls, indent=2))
            lines.append("```")
        else:
            lines.append(self.content or "")
            
        lines.append("")
        lines.append("---")
        lines.append("")
        
        return "\n".join(lines)


@dataclass
class PythonClawMemoryEntry:
    """A memory entry for PythonClaw's MemoryManager (daily log format).
    
    PythonClaw stores long-term memory as Markdown files with timestamped entries.
    File: ~/.pythonclaw/memory/YYYY-MM-DD.md
    """
    content: str
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    source: str = "mmkr"  # origin tag
    importance: float = 0.5  # 0-1 scale
    tags: list[str] = field(default_factory=list)
    
    def to_markdown_entry(self) -> str:
        """Convert to PythonClaw memory log entry format."""
        tags_str = " ".join(f"#{tag}" for tag in self.tags) if self.tags else ""
        return f"- [{self.ts}] {self.content} {tags_str} (source={self.source}, importance={self.importance:.2f})\n"


@dataclass 
class PythonClawSkillMetadata:
    """PythonClaw three-tier skill metadata for an mmkr capability.
    
    PythonClaw skills have three loading tiers:
    1. metadata: name, description, triggers (always loaded)
    2. instructions: detailed usage, examples (loaded on demand)
    3. resources: full implementation (loaded when invoked)
    """
    name: str
    description: str
    version: str = "1.0.0"
    triggers: list[str] = field(default_factory=list)
    author: str = "mmkr/botbotfromuk"
    capabilities: list[str] = field(default_factory=list)
    fitness_score: float = 0.0
    
    def to_skill_yaml(self) -> str:
        """Generate skill.yaml for PythonClaw's skill loader."""
        triggers_yaml = "\n".join(f"  - {t}" for t in self.triggers)
        caps_yaml = "\n".join(f"  - {c}" for c in self.capabilities)
        return f"""name: {self.name}
version: {self.version}
description: {self.description}
author: {self.author}
fitness_score: {self.fitness_score:.3f}

triggers:
{triggers_yaml}

capabilities:
{caps_yaml}

# mmkr trace-compatible: session_id, tick, tool_call events logged automatically
trace_format: "~/.mmkr/session.trace.jsonl"
"""


# ── PythonClawCollector ─────────────────────────────────────────────────────

class PythonClawCollector:
    """Collects mmkr tick events and bridges to PythonClaw's session/memory format.
    
    Produces:
    - context/sessions/mmkr_<agent_id>.md — SessionStore Markdown (compatible with PythonClaw's format)
    - memory/YYYY-MM-DD.md — MemoryManager daily log entries
    
    The session file is append-only (like mmkr's trace.jsonl) and can be
    loaded by PythonClaw's SessionStore.restore() to reconstruct agent history.
    """
    
    def __init__(
        self,
        agent_id: str,
        claw_home: str | None = None,
        mmkr_data: str | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.session_id = f"mmkr:{agent_id}"
        
        # PythonClaw home directory
        claw_home = claw_home or os.path.expanduser("~/.pythonclaw")
        self.sessions_dir = Path(claw_home) / "context" / "sessions"
        self.memory_dir = Path(claw_home) / "memory"
        
        # mmkr data directory (for reading trace files)
        self.mmkr_data = Path(mmkr_data or os.path.expanduser("~/.mmkr"))
        
        # Output files
        safe_id = re.sub(r"[^\w\-]", "_", self.session_id)
        self.session_file = self.sessions_dir / f"{safe_id}.md"
        
        self._messages: list[PythonClawMessage] = []
        self._memory_entries: list[PythonClawMemoryEntry] = []
        
        # Statistics
        self._ticks_recorded: int = 0
        self._tools_recorded: int = 0
        self._memories_recorded: int = 0
    
    def record_tick_start(
        self,
        tick: int,
        goals: list[str] | None = None,
        memory_count: int = 0,
    ) -> PythonClawMessage:
        """Record tick start as a 'user' message (the environment prompting the agent)."""
        content_parts = [f"Tick {tick} — observe, think, act."]
        if goals:
            content_parts.append(f"Active goals: {', '.join(goals)}")
        if memory_count:
            content_parts.append(f"Loaded {memory_count} memories.")
        
        msg = PythonClawMessage(
            role="user",
            content="\n".join(content_parts),
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self._messages.append(msg)
        return msg
    
    def record_tool_call(
        self,
        tick: int,
        tool_name: str,
        args: dict[str, Any],
        reasoning: str | None = None,
    ) -> PythonClawMessage:
        """Record a tool call as an assistant message with tool_calls JSON."""
        call_id = f"call_{hashlib.sha1(f'{tick}:{tool_name}:{json.dumps(args, sort_keys=True)}'.encode()).hexdigest()[:8]}"
        
        tool_call = {
            "id": call_id,
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(args),
            }
        }
        
        msg = PythonClawMessage(
            role="assistant",
            content=reasoning or f"Calling {tool_name}",
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            tool_calls=[tool_call],
        )
        self._messages.append(msg)
        self._tools_recorded += 1
        return msg
    
    def record_tool_result(
        self,
        tick: int,
        tool_name: str,
        result: Any,
        is_error: bool = False,
    ) -> PythonClawMessage:
        """Record a tool result as a tool message."""
        result_str = json.dumps(result) if not isinstance(result, str) else result
        
        msg = PythonClawMessage(
            role="tool",
            content=result_str if not is_error else f"ERROR: {result_str}",
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            tool_call_id=f"call_{tool_name}_{tick}",
            name=tool_name,
        )
        self._messages.append(msg)
        return msg
    
    def record_tick_end(
        self,
        tick: int,
        summary: str,
        tools_used: int = 0,
        actions_taken: list[str] | None = None,
    ) -> PythonClawMessage:
        """Record tick completion as an assistant message."""
        content_parts = [summary]
        if tools_used:
            content_parts.append(f"Tools used: {tools_used}")
        if actions_taken:
            content_parts.append("Actions: " + ", ".join(actions_taken))
        
        msg = PythonClawMessage(
            role="assistant",
            content="\n".join(content_parts),
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self._messages.append(msg)
        self._ticks_recorded += 1
        return msg
    
    def record_memory(
        self,
        content: str,
        category: str = "general",
        importance: float = 0.5,
    ) -> PythonClawMemoryEntry:
        """Record a memory write as a PythonClaw MemoryManager entry."""
        entry = PythonClawMemoryEntry(
            content=content,
            source=f"mmkr:{category}",
            importance=importance,
            tags=[category, "mmkr"],
        )
        self._memory_entries.append(entry)
        self._memories_recorded += 1
        return entry
    
    def flush_to_session_file(self) -> Path:
        """Write all messages to the PythonClaw SessionStore Markdown file."""
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        
        with open(self.session_file, "a", encoding="utf-8") as f:
            for msg in self._messages:
                f.write(msg.to_markdown_block())
        
        written = len(self._messages)
        self._messages.clear()
        return self.session_file
    
    def flush_to_memory_log(self, date: str | None = None) -> Path:
        """Write memory entries to PythonClaw's daily memory log."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        
        date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        memory_file = self.memory_dir / f"{date}.md"
        
        with open(memory_file, "a", encoding="utf-8") as f:
            for entry in self._memory_entries:
                f.write(entry.to_markdown_entry())
        
        written = len(self._memory_entries)
        self._memory_entries.clear()
        return memory_file
    
    def session_stats(self) -> dict[str, Any]:
        """Return session statistics."""
        return {
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "ticks_recorded": self._ticks_recorded,
            "tools_recorded": self._tools_recorded,
            "memories_recorded": self._memories_recorded,
            "session_file": str(self.session_file),
            "memory_dir": str(self.memory_dir),
        }


# ── Standalone conversion functions ───────────────────────────────────────

def convert_trace_to_pythonclaw(
    trace_path: str | Path,
    agent_id: str,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Convert an existing mmkr .trace.jsonl file to PythonClaw's SessionStore format.
    
    Reads the trace file and produces a Markdown session file compatible with
    PythonClaw's SessionStore.restore() method.
    
    Args:
        trace_path: Path to mmkr's session.trace.jsonl
        agent_id: Agent identifier (becomes session_id = "mmkr:<agent_id>")
        output_dir: Directory for output files (default: ~/.pythonclaw)
    
    Returns:
        Dict with output paths and conversion statistics.
    """
    trace_path = Path(trace_path)
    output_dir = Path(output_dir or os.path.expanduser("~/.pythonclaw"))
    
    collector = PythonClawCollector(agent_id=agent_id, claw_home=str(output_dir))
    
    events_by_tick: dict[int, list[dict]] = {}
    
    with open(trace_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                tick = event.get("tick", 0)
                if tick not in events_by_tick:
                    events_by_tick[tick] = []
                events_by_tick[tick].append(event)
            except json.JSONDecodeError:
                continue
    
    messages: list[PythonClawMessage] = []
    
    for tick in sorted(events_by_tick.keys()):
        tick_events = events_by_tick[tick]
        
        # Find tick_start event
        tick_start = next((e for e in tick_events if e.get("event_type") == "mmkr:tick_start"), None)
        if tick_start:
            msg = PythonClawMessage(
                role="user",
                content=f"Tick {tick} — observe, think, act.",
                ts=tick_start.get("ts", datetime.now(timezone.utc).isoformat()),
            )
            messages.append(msg)
        
        # Process tool calls and results
        for event in tick_events:
            etype = event.get("event_type", "")
            ts = event.get("ts", datetime.now(timezone.utc).isoformat())
            
            if etype == "tool_call":
                tool_name = event.get("tool", "unknown")
                args = event.get("args", {})
                call_id = hashlib.sha1(
                    f'{tick}:{tool_name}:{json.dumps(args, sort_keys=True)}'.encode()
                ).hexdigest()[:8]
                
                messages.append(PythonClawMessage(
                    role="assistant",
                    content=f"Calling {tool_name}",
                    ts=ts,
                    tool_calls=[{
                        "id": f"call_{call_id}",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(args),
                        }
                    }],
                ))
            
            elif etype == "tool_result":
                tool_name = event.get("tool", "unknown")
                outcome = event.get("outcome", "")
                is_error = event.get("error") is not None
                
                messages.append(PythonClawMessage(
                    role="tool",
                    content=json.dumps({"outcome": outcome, "error": event.get("error")}) if is_error else outcome,
                    ts=ts,
                    tool_call_id=f"call_{tool_name}_{tick}",
                    name=tool_name,
                ))
            
            elif etype == "mmkr:decision":
                messages.append(PythonClawMessage(
                    role="assistant",
                    content=event.get("summary", ""),
                    ts=ts,
                ))
            
            elif etype == "memory_write":
                collector.record_memory(
                    content=event.get("content", ""),
                    category=event.get("category", "general"),
                    importance=0.6,
                )
            
            elif etype == "mmkr:tick_end":
                summary = event.get("summary", f"Tick {tick} complete")
                messages.append(PythonClawMessage(
                    role="assistant",
                    content=summary,
                    ts=ts,
                ))
    
    # Write session file
    collector.sessions_dir.mkdir(parents=True, exist_ok=True)
    with open(collector.session_file, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(msg.to_markdown_block())
    
    # Write memory log
    collector.flush_to_memory_log()
    
    return {
        "session_file": str(collector.session_file),
        "memory_log": str(collector.memory_dir),
        "ticks_converted": len(events_by_tick),
        "messages_generated": len(messages),
        "session_id": collector.session_id,
    }


def capability_to_skill_metadata(
    capability_name: str,
    fitness_score: float = 0.0,
    description: str | None = None,
    tools: list[str] | None = None,
) -> PythonClawSkillMetadata:
    """Convert an mmkr capability to PythonClaw skill metadata.
    
    Maps mmkr's CapabilityEvolver fitness scores to PythonClaw's skill
    selection system. High-fitness capabilities become preferred skills.
    
    Args:
        capability_name: Name of the mmkr capability (e.g. "cap_github_maintenance")
        fitness_score: NaturalSelection fitness score (0-5 scale)
        description: Human-readable description
        tools: List of tool names this capability provides
    
    Returns:
        PythonClawSkillMetadata ready for three-tier loading.
    """
    # Generate triggers from capability name
    name_parts = capability_name.replace("cap_", "").replace("_", " ").split()
    triggers = [
        " ".join(name_parts),
        " ".join(name_parts[:2]) if len(name_parts) > 1 else name_parts[0],
        f"mmkr {name_parts[0]}",
    ]
    
    return PythonClawSkillMetadata(
        name=f"mmkr.{capability_name}",
        description=description or f"mmkr capability: {capability_name.replace('cap_', '').replace('_', ' ')}",
        triggers=triggers,
        author="mmkr/botbotfromuk",
        capabilities=tools or [],
        fitness_score=fitness_score,
    )


def pythonclaw_session_stats(session_file: str | Path) -> dict[str, Any]:
    """Parse a PythonClaw session Markdown file and return statistics.
    
    Analogous to PythonClaw's built-in session analytics — provides
    message counts, role distribution, tool usage summary.
    """
    session_file = Path(session_file)
    
    if not session_file.exists():
        return {"error": f"Session file not found: {session_file}"}
    
    meta_pattern = re.compile(r"<!-- msg:(.*?) -->")
    
    messages: list[dict] = []
    with open(session_file, encoding="utf-8") as f:
        content = f.read()
    
    for match in meta_pattern.finditer(content):
        try:
            meta = json.loads(match.group(1))
            messages.append(meta)
        except json.JSONDecodeError:
            continue
    
    roles = {}
    tools_seen: list[str] = []
    
    for msg in messages:
        role = msg.get("role", "unknown")
        roles[role] = roles.get(role, 0) + 1
        if name := msg.get("name"):
            tools_seen.append(name)
    
    # Count unique ticks from user messages (each tick = one user message)
    user_count = roles.get("user", 0)
    
    return {
        "session_file": str(session_file),
        "total_messages": len(messages),
        "ticks_estimated": user_count,
        "role_distribution": roles,
        "tools_invoked": len(tools_seen),
        "unique_tools": sorted(set(tools_seen)),
        "file_size_kb": round(session_file.stat().st_size / 1024, 1),
    }


# ── Smoke test ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile
    
    print("=== PythonClawCollector smoke test ===\n")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        collector = PythonClawCollector(
            agent_id="botbotfromuk",
            claw_home=tmpdir,
        )
        
        print("1. record_tick_start()")
        msg = collector.record_tick_start(
            tick=64,
            goals=["Social Presence (96%)", "Economic Pillar (0%)"],
            memory_count=81,
        )
        print(f"   role={msg.role}, content_len={len(msg.content)}")
        
        print("\n2. record_tool_call()")
        msg = collector.record_tool_call(
            tick=64,
            tool_name="safe_post_comment",
            args={"repo": "ericwang915/PythonClaw", "issue_number": 1, "body": "..."},
            reasoning="Posting PythonClawCollector working code to issue #1",
        )
        print(f"   role={msg.role}, tool_calls={len(msg.tool_calls)}")
        
        print("\n3. record_tool_result()")
        msg = collector.record_tool_result(
            tick=64,
            tool_name="safe_post_comment",
            result={"success": True, "url": "https://github.com/ericwang915/PythonClaw/issues/1#issuecomment-xxx"},
        )
        print(f"   role={msg.role}, tool_call_id={msg.tool_call_id}")
        
        print("\n4. record_memory()")
        entry = collector.record_memory(
            content="PythonClaw uses Markdown-backed sessions with <!-- msg:{...} --> metadata",
            category="architecture",
            importance=0.8,
        )
        print(f"   source={entry.source}, importance={entry.importance}")
        
        print("\n5. record_tick_end()")
        msg = collector.record_tick_end(
            tick=64,
            summary="Built PythonClawCollector integration (425 LOC) and posted working code to issue #1",
            tools_used=8,
            actions_taken=["built integration", "posted to GitHub"],
        )
        print(f"   role={msg.role}, content_len={len(msg.content)}")
        
        print("\n6. flush_to_session_file()")
        session_file = collector.flush_to_session_file()
        print(f"   Written to: {session_file}")
        print(f"   File exists: {session_file.exists()}")
        
        print("\n7. pythonclaw_session_stats()")
        stats = pythonclaw_session_stats(session_file)
        print(f"   total_messages={stats['total_messages']}, ticks_estimated={stats['ticks_estimated']}")
        print(f"   role_distribution={stats['role_distribution']}")
        print(f"   file_size={stats['file_size_kb']} KB")
        
        print("\n8. capability_to_skill_metadata()")
        skill = capability_to_skill_metadata(
            "cap_github_maintenance",
            fitness_score=3.784,
            description="GitHub maintenance: scan issues, comment, post gists, track engagement",
            tools=["check_issue_responses", "scan_all_my_issues", "post_issue_comment"],
        )
        print(f"   name={skill.name}")
        print(f"   fitness_score={skill.fitness_score}")
        print(f"   triggers={skill.triggers}")
        print(f"   skill.yaml preview:\n{skill.to_skill_yaml()[:300]}")
        
        print("\n9. convert_trace_to_pythonclaw() (synthetic trace)")
        trace_data = [
            {"event_type": "mmkr:tick_start", "tick": 1, "ts": "2026-03-07T10:00:00+00:00"},
            {"event_type": "tool_call", "tick": 1, "tool": "save_memory", "args": {"category": "test", "content": "hello"}, "ts": "2026-03-07T10:00:01+00:00"},
            {"event_type": "tool_result", "tick": 1, "tool": "save_memory", "outcome": "saved", "ts": "2026-03-07T10:00:02+00:00"},
            {"event_type": "mmkr:tick_end", "tick": 1, "summary": "Tick 1 done. Saved memory.", "ts": "2026-03-07T10:00:05+00:00"},
            {"event_type": "mmkr:tick_start", "tick": 2, "ts": "2026-03-07T10:01:00+00:00"},
            {"event_type": "mmkr:decision", "tick": 2, "summary": "Decided to build PythonClaw integration", "ts": "2026-03-07T10:01:02+00:00"},
            {"event_type": "mmkr:tick_end", "tick": 2, "summary": "Tick 2 done. Integration built.", "ts": "2026-03-07T10:01:30+00:00"},
        ]
        
        trace_file = Path(tmpdir) / "session.trace.jsonl"
        with open(trace_file, "w") as f:
            for event in trace_data:
                f.write(json.dumps(event) + "\n")
        
        result = convert_trace_to_pythonclaw(trace_file, agent_id="test-agent", output_dir=tmpdir)
        print(f"   ticks_converted={result['ticks_converted']}")
        print(f"   messages_generated={result['messages_generated']}")
        print(f"   session_id={result['session_id']}")
        
        # Show first 500 chars of output
        with open(result['session_file']) as f:
            preview = f.read(500)
        print(f"\n   Session file preview:\n{preview}")
    
    print("\n=== ALL 9 TESTS PASS ===")
