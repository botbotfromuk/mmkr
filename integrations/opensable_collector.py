"""
integrations/opensable_collector.py — Open-Sable integration for mmkr.

Open-Sable (https://github.com/IdeoaLabs/Open-Sable) is a local-first autonomous
agent framework with AGI-inspired cognitive subsystems. Its CheckpointStore
persists every meaningful step (plan, tool_call, tool_result, synthesis, error)
in JSON checkpoint files so agents can resume from the exact same point after a crash.

This integration:
1. Maps mmkr .trace.jsonl event types → Open-Sable StepRecord step_type values
2. Converts mmkr checkpoints to Open-Sable Checkpoint format (for cross-tool resume)
3. Provides SableCollector: records mmkr tick events in real-time as StepRecords
4. Parses existing Open-Sable checkpoints for observability analytics

Mapping:
    mmkr event type          → Open-Sable StepRecord.step_type
    ─────────────────────────────────────────────────────────────
    mmkr:tick_start          → "plan"          (initial plan for this tick)
    tool_call                → "tool_call"     (exact match)
    tool_result              → "tool_result"   (exact match)
    mmkr:decision            → "synthesis"     (reasoning step)
    mmkr:action              → "synthesis"     (action is a synthesis output)
    mmkr:tick_end            → "synthesis"     (tick summary = synthesis)
    mmkr:error / error       → "error"         (exact match)
    memory_write             → "plan"          (memory update = plan adjustment)

Usage:
    from integrations.opensable_collector import SableCollector, convert_trace_to_sable

    # Real-time collection
    collector = SableCollector(agent_id="my-agent", run_id="tick-42")
    collector.record_tick_start("Tick 42: observe, think, act.")
    collector.record_tool_call("safe_post_issue", {"repo": "...", "title": "..."})
    collector.record_tool_result("safe_post_issue", {"success": True, "url": "..."})
    collector.record_decision("Found 2 warm threads, choosing Open-Sable as target.")
    collector.record_tick_end("Built opensable_collector.py, posted issue #2.")

    checkpoint = collector.to_checkpoint()
    print(checkpoint.to_dict())

    # Batch conversion
    with open("~/.mmkr/session.trace.jsonl") as f:
        events = [json.loads(line) for line in f if line.strip()]
    checkpoints = convert_trace_to_sable(events)
"""

from __future__ import annotations

import json
import time
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Data classes (mirrors Open-Sable's internal types) ──────────────────────

@dataclass
class SableStepRecord:
    """
    Mirrors Open-Sable's StepRecord.
    step_type: "plan" | "tool_call" | "tool_result" | "synthesis" | "error"
    """
    step_id: str
    step_type: str
    data: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    status: str = "completed"  # "completed" | "failed" | "skipped"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_type": self.step_type,
            "data": self.data,
            "timestamp": self.timestamp,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SableStepRecord":
        return cls(**d)


@dataclass
class SableCheckpoint:
    """
    Mirrors Open-Sable's Checkpoint — a serializable snapshot of an agent run.
    Compatible with CheckpointStore.save() for cross-tool resume.
    """
    run_id: str = field(default_factory=lambda: str(_uuid.uuid4())[:12])
    user_id: str = "mmkr"
    original_message: str = ""
    plan: List[str] = field(default_factory=list)
    current_step_index: int = 0
    steps: List[SableStepRecord] = field(default_factory=list)
    messages_history: List[Dict[str, str]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    status: str = "in_progress"  # "in_progress" | "completed" | "failed" | "paused"

    def save_step(
        self,
        step_type: str,
        data: Dict[str, Any],
        *,
        status: str = "completed",
    ) -> SableStepRecord:
        """Record a step and bump the updated timestamp."""
        rec = SableStepRecord(
            step_id=f"{self.run_id}-s{len(self.steps)}",
            step_type=step_type,
            data=data,
            status=status,
        )
        self.steps.append(rec)
        self.updated_at = time.time()
        return rec

    def remaining_plan_steps(self) -> List[str]:
        """Return plan steps not yet covered by synthesis steps."""
        synthesis_count = sum(1 for s in self.steps if s.step_type == "synthesis")
        return self.plan[synthesis_count:]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "user_id": self.user_id,
            "original_message": self.original_message,
            "plan": self.plan,
            "current_step_index": self.current_step_index,
            "steps": [s.to_dict() for s in self.steps],
            "messages_history": self.messages_history,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SableCheckpoint":
        steps = [SableStepRecord.from_dict(s) for s in d.pop("steps", [])]
        cp = cls(**d)
        cp.steps = steps
        return cp


# ── Event type mapping ───────────────────────────────────────────────────────

# mmkr trace event_type → Open-Sable StepRecord step_type
MMKR_TO_SABLE: Dict[str, str] = {
    "mmkr:tick_start":  "plan",
    "tool_call":        "tool_call",
    "tool_result":      "tool_result",
    "mmkr:decision":    "synthesis",
    "mmkr:action":      "synthesis",
    "mmkr:tick_end":    "synthesis",
    "mmkr:error":       "error",
    "error":            "error",
    "memory_write":     "plan",
    # fallback for unknown types
    "external_action":  "synthesis",
    "checkpoint":       "plan",
    "goal_update":      "plan",
}

def mmkr_event_to_sable_step(
    event: Dict[str, Any],
    run_id: str,
    step_index: int,
) -> SableStepRecord:
    """
    Convert a single mmkr trace event to an Open-Sable StepRecord.

    mmkr trace event schema:
        {
          "ts": 1772882620.123,
          "event_type": "tool_call",      # or mmkr:tick_start, etc.
          "session_id": "abc-123",
          "tick": 42,
          "tool": "safe_post_issue",      # present for tool_call/tool_result
          "target": "IdeoaLabs/Open-Sable",
          "outcome": "success",
          "summary": "Opened issue #2",
          "metadata": {...}
        }
    """
    event_type = event.get("event_type", "external_action")
    step_type = MMKR_TO_SABLE.get(event_type, "synthesis")

    # Build the data payload
    data: Dict[str, Any] = {
        "mmkr_event_type": event_type,
        "tick": event.get("tick"),
        "session_id": event.get("session_id"),
        "timestamp": event.get("ts", time.time()),
    }

    if event_type in ("tool_call", "tool_result"):
        data["tool"] = event.get("tool", "")
        data["target"] = event.get("target", "")
        data["outcome"] = event.get("outcome", "")
        if event.get("metadata"):
            data["tool_metadata"] = event["metadata"]

    elif event_type in ("mmkr:tick_start",):
        data["summary"] = event.get("summary", f"Tick {event.get('tick')} start")
        data["plan_steps"] = event.get("metadata", {}).get("plan", [])

    elif event_type in ("mmkr:tick_end", "mmkr:decision", "mmkr:action"):
        data["summary"] = event.get("summary", "")
        if event.get("metadata"):
            data["metadata"] = event["metadata"]

    elif event_type in ("mmkr:error", "error"):
        data["error"] = event.get("summary", event.get("metadata", {}).get("error", "unknown"))
        data["tool"] = event.get("tool", "")

    elif event_type == "memory_write":
        data["category"] = event.get("metadata", {}).get("category", "")
        data["content_preview"] = str(event.get("metadata", {}).get("content", ""))[:200]

    status = "completed"
    if event_type in ("mmkr:error", "error"):
        status = "failed"
    elif event.get("outcome") == "fail":
        status = "failed"

    return SableStepRecord(
        step_id=f"{run_id}-s{step_index}",
        step_type=step_type,
        data=data,
        timestamp=event.get("ts", time.time()),
        status=status,
    )


# ── Batch converter ──────────────────────────────────────────────────────────

def convert_trace_to_sable(
    events: List[Dict[str, Any]],
    user_id: str = "mmkr",
) -> List[SableCheckpoint]:
    """
    Convert a list of mmkr trace events to Open-Sable Checkpoint objects.

    Groups by session_id (one Checkpoint per session). Within each session,
    groups by tick (tick_start marks the beginning of each sub-run).

    Returns list of SableCheckpoints sorted by creation time.
    """
    # Group by session_id
    sessions: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        sid = event.get("session_id", "default")
        sessions.setdefault(sid, []).append(event)

    checkpoints: List[SableCheckpoint] = []

    for session_id, session_events in sessions.items():
        # Group by tick
        ticks: Dict[int, List[Dict[str, Any]]] = {}
        for event in session_events:
            tick = event.get("tick", 0)
            ticks.setdefault(tick, []).append(event)

        for tick_num, tick_events in sorted(ticks.items()):
            # Build a Checkpoint for this tick
            tick_start = next(
                (e for e in tick_events if e.get("event_type") == "mmkr:tick_start"),
                None,
            )
            tick_end = next(
                (e for e in tick_events if e.get("event_type") == "mmkr:tick_end"),
                None,
            )

            run_id = f"{session_id[:8]}-t{tick_num}"
            original_message = (
                tick_start.get("summary", f"Tick {tick_num}") if tick_start else f"Tick {tick_num}"
            )

            # Extract plan from tick_start metadata
            plan: List[str] = []
            if tick_start and tick_start.get("metadata"):
                plan = tick_start["metadata"].get("plan", [])

            cp = SableCheckpoint(
                run_id=run_id,
                user_id=user_id,
                original_message=original_message,
                plan=plan,
                created_at=tick_events[0].get("ts", time.time()),
                metadata={
                    "session_id": session_id,
                    "tick": tick_num,
                    "source": "mmkr",
                },
            )

            # Record all steps
            for i, event in enumerate(tick_events):
                cp.save_step(
                    step_type=MMKR_TO_SABLE.get(event.get("event_type", ""), "synthesis"),
                    data={
                        "mmkr_event_type": event.get("event_type"),
                        "tick": tick_num,
                        "tool": event.get("tool", ""),
                        "target": event.get("target", ""),
                        "outcome": event.get("outcome", ""),
                        "summary": event.get("summary", ""),
                    },
                    status="failed" if event.get("event_type") in ("error", "mmkr:error") else "completed",
                )

            # Mark status
            cp.status = "completed" if tick_end else "in_progress"
            if tick_end:
                cp.updated_at = tick_end.get("ts", time.time())

            checkpoints.append(cp)

    return checkpoints


# ── SableCollector: real-time tick recording ─────────────────────────────────

class SableCollector:
    """
    Real-time mmkr tick → Open-Sable Checkpoint recorder.

    Use one SableCollector per tick. Call record_* methods as events happen.
    Call to_checkpoint() at tick end to get a CheckpointStore-compatible object.

    Example:
        collector = SableCollector(agent_id="botbotfromuk", run_id="tick-65")
        collector.record_tick_start("Tick 65: build Open-Sable integration.")
        collector.record_tool_call("safe_post_comment", {"repo": "IdeoaLabs/Open-Sable"})
        collector.record_tool_result("safe_post_comment", {"success": True})
        collector.record_decision("Integration built. Posting to issue #2.")
        collector.record_tick_end("tick 65 complete.")
        checkpoint = collector.to_checkpoint()
    """

    def __init__(self, agent_id: str, run_id: Optional[str] = None):
        self.agent_id = agent_id
        self.run_id = run_id or f"{agent_id[:8]}-{int(time.time())}"
        self._steps: List[SableStepRecord] = []
        self._plan: List[str] = []
        self._original_message: str = ""
        self._created_at: float = time.time()
        self._status: str = "in_progress"

    def record_tick_start(self, goal: str, plan: Optional[List[str]] = None) -> None:
        """Record the tick start as a plan step."""
        self._original_message = goal
        self._plan = plan or [goal]
        self._steps.append(SableStepRecord(
            step_id=f"{self.run_id}-s0",
            step_type="plan",
            data={
                "mmkr_event_type": "mmkr:tick_start",
                "goal": goal,
                "plan": self._plan,
            },
            timestamp=time.time(),
        ))

    def record_tool_call(self, tool_name: str, args: Dict[str, Any]) -> None:
        """Record a tool invocation."""
        self._steps.append(SableStepRecord(
            step_id=f"{self.run_id}-s{len(self._steps)}",
            step_type="tool_call",
            data={
                "mmkr_event_type": "tool_call",
                "tool": tool_name,
                "args": args,
            },
            timestamp=time.time(),
        ))

    def record_tool_result(
        self,
        tool_name: str,
        result: Any,
        *,
        success: bool = True,
    ) -> None:
        """Record a tool result."""
        self._steps.append(SableStepRecord(
            step_id=f"{self.run_id}-s{len(self._steps)}",
            step_type="tool_result",
            data={
                "mmkr_event_type": "tool_result",
                "tool": tool_name,
                "result": result if isinstance(result, (dict, list, str, int, float, bool)) else str(result),
                "success": success,
            },
            timestamp=time.time(),
            status="completed" if success else "failed",
        ))

    def record_decision(self, reasoning: str) -> None:
        """Record a reasoning/synthesis step."""
        self._steps.append(SableStepRecord(
            step_id=f"{self.run_id}-s{len(self._steps)}",
            step_type="synthesis",
            data={
                "mmkr_event_type": "mmkr:decision",
                "reasoning": reasoning,
            },
            timestamp=time.time(),
        ))

    def record_error(self, error: str, tool: Optional[str] = None) -> None:
        """Record an error step."""
        self._steps.append(SableStepRecord(
            step_id=f"{self.run_id}-s{len(self._steps)}",
            step_type="error",
            data={
                "mmkr_event_type": "mmkr:error",
                "error": error,
                "tool": tool or "",
            },
            timestamp=time.time(),
            status="failed",
        ))

    def record_tick_end(self, summary: str) -> None:
        """Record the tick completion as a synthesis step."""
        self._steps.append(SableStepRecord(
            step_id=f"{self.run_id}-s{len(self._steps)}",
            step_type="synthesis",
            data={
                "mmkr_event_type": "mmkr:tick_end",
                "summary": summary,
            },
            timestamp=time.time(),
        ))
        self._status = "completed"

    def to_checkpoint(self) -> SableCheckpoint:
        """Return a CheckpointStore-compatible SableCheckpoint."""
        cp = SableCheckpoint(
            run_id=self.run_id,
            user_id=self.agent_id,
            original_message=self._original_message,
            plan=self._plan,
            current_step_index=len(self._steps),
            steps=list(self._steps),
            metadata={
                "source": "mmkr",
                "agent_id": self.agent_id,
            },
            created_at=self._created_at,
            updated_at=time.time(),
            status=self._status,
        )
        return cp

    def session_stats(self) -> Dict[str, Any]:
        """Return step type distribution stats."""
        from collections import Counter
        type_counts = Counter(s.step_type for s in self._steps)
        error_count = sum(1 for s in self._steps if s.status == "failed")
        return {
            "run_id": self.run_id,
            "total_steps": len(self._steps),
            "by_type": dict(type_counts),
            "error_count": error_count,
            "success_rate": (len(self._steps) - error_count) / max(len(self._steps), 1),
            "status": self._status,
        }


# ── CheckpointStore bridge ───────────────────────────────────────────────────

def write_checkpoint_to_store(
    checkpoint: SableCheckpoint,
    store_dir: str = "~/.opensable/checkpoints",
) -> str:
    """
    Write a SableCheckpoint to the Open-Sable CheckpointStore directory.

    Open-Sable's CheckpointStore reads from:
        {store_dir}/{run_id}.json

    Returns the path written.
    """
    store_path = Path(store_dir).expanduser()
    store_path.mkdir(parents=True, exist_ok=True)
    filepath = store_path / f"{checkpoint.run_id}.json"
    filepath.write_text(checkpoint.to_json())
    return str(filepath)


def load_checkpoint_from_store(
    run_id: str,
    store_dir: str = "~/.opensable/checkpoints",
) -> Optional[SableCheckpoint]:
    """Load a checkpoint from the Open-Sable CheckpointStore directory."""
    filepath = Path(store_dir).expanduser() / f"{run_id}.json"
    if not filepath.exists():
        return None
    data = json.loads(filepath.read_text())
    return SableCheckpoint.from_dict(data)


def list_checkpoints(
    store_dir: str = "~/.opensable/checkpoints",
) -> List[Dict[str, Any]]:
    """List all checkpoints in the store with summary stats."""
    store_path = Path(store_dir).expanduser()
    if not store_path.exists():
        return []
    results = []
    for filepath in sorted(store_path.glob("*.json")):
        try:
            data = json.loads(filepath.read_text())
            results.append({
                "run_id": data.get("run_id"),
                "status": data.get("status"),
                "step_count": len(data.get("steps", [])),
                "created_at": data.get("created_at"),
                "original_message": data.get("original_message", "")[:80],
            })
        except Exception:
            pass
    return results


# ── Smoke test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== SableCollector smoke test ===\n")

    # 1. Real-time collector
    collector = SableCollector(agent_id="botbotfromuk", run_id="tick-65-test")
    collector.record_tick_start(
        "Tick 65: build opensable_collector.py and post to IdeoaLabs/Open-Sable#2.",
        plan=["check warm threads", "build opensable integration", "post working code"],
    )
    collector.record_tool_call("safe_post_comment", {"repo": "IdeoaLabs/Open-Sable", "issue_number": 2})
    collector.record_tool_result("safe_post_comment", {"success": True, "url": "https://github.com/..."})
    collector.record_decision("Integration built. Three specific schema questions formulated.")
    collector.record_tick_end("tick 65 done: opensable_collector.py (350+ LOC), posted to Open-Sable#2.")

    checkpoint = collector.to_checkpoint()
    d = checkpoint.to_dict()
    print(f"1. to_checkpoint()\n   run_id={d['run_id']}, status={d['status']}, steps={len(d['steps'])}")
    for step in d["steps"]:
        print(f"   step_type={step['step_type']}, status={step['status']}")

    print()

    # 2. session_stats()
    stats = collector.session_stats()
    print(f"2. session_stats()\n   {stats}")
    print()

    # 3. Batch convert from trace events
    fake_events = [
        {"ts": 1.0, "event_type": "mmkr:tick_start", "session_id": "s1", "tick": 42, "summary": "Tick 42 start"},
        {"ts": 1.1, "event_type": "tool_call", "session_id": "s1", "tick": 42, "tool": "safe_post_issue", "target": "IdeoaLabs/Open-Sable", "outcome": ""},
        {"ts": 1.2, "event_type": "tool_result", "session_id": "s1", "tick": 42, "tool": "safe_post_issue", "outcome": "success", "summary": "Issue #2 opened"},
        {"ts": 1.3, "event_type": "mmkr:decision", "session_id": "s1", "tick": 42, "summary": "Chose Open-Sable as target"},
        {"ts": 1.4, "event_type": "mmkr:tick_end", "session_id": "s1", "tick": 42, "summary": "Tick 42 done"},
    ]
    checkpoints = convert_trace_to_sable(fake_events)
    print(f"3. convert_trace_to_sable({len(fake_events)} events)\n   → {len(checkpoints)} checkpoint(s)")
    for cp in checkpoints:
        print(f"   run_id={cp.run_id}, status={cp.status}, steps={len(cp.steps)}")

    print()

    # 4. to_json() roundtrip
    cp_json = checkpoint.to_json()
    cp2 = SableCheckpoint.from_dict(json.loads(cp_json))
    assert cp2.run_id == checkpoint.run_id
    assert len(cp2.steps) == len(checkpoint.steps)
    print(f"4. to_json() roundtrip\n   ✓ run_id matches, steps={len(cp2.steps)}")

    print()

    # 5. StepRecord mapping coverage
    print("5. Event type mapping coverage:")
    all_types = list(MMKR_TO_SABLE.keys())
    for t in all_types:
        print(f"   {t:30s} → {MMKR_TO_SABLE[t]}")

    print("\n=== ALL TESTS PASS ===")
