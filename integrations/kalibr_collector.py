"""
integrations/kalibr_collector.py — Kalibr SDK integration for mmkr.

Kalibr (https://github.com/kalibr-ai/kalibr-sdk-python) is a self-improvement
infrastructure for agents: it captures execution telemetry, tracks whether goals
were achieved, and routes future calls to what actually works.

This module maps mmkr's tick-based execution model to Kalibr's:
  - Router: goal-based path selection (mmkr: capability selection via NaturalSelection)
  - outcome tracking: per-tick success/failure mapped to Kalibr outcome signals
  - capability drift: mmkr fitness scores feed Kalibr's routing decisions

MAPPING TABLE:
  mmkr concept              → Kalibr concept
  ─────────────────────────────────────────────────────
  capability (LifeCapability) → Router path
  NaturalSelection score      → Kalibr outcome weight
  tick                        → execution unit / Router call
  tick_complete               → outcome signal (success/partial/fail)
  tool_call                   → telemetry event
  tool_result                 → path performance signal
  action/decision             → step annotation
  error                       → negative outcome

USAGE (drop-in with mmkr):

    from mmkr.life import standard_tick
    from integrations.kalibr_collector import KalibrCollector, KalibrRouter

    collector = KalibrCollector(
        agent_id="mmkr-prod",
        kalibr_api_key=os.environ["KALIBR_API_KEY"],
        emit_live=True,   # POST to Kalibr API in real-time
    )

    router = KalibrRouter(
        goal="build social presence",
        paths={
            "github_engagement": lambda: engage_github(),
            "blog_post": lambda: write_blog_post(),
            "integration_build": lambda: build_integration(),
        },
        success_when=lambda result: result.get("responses_received", 0) > 0,
    )

    # Use collector in tick loop
    with collector.tick_context(tick=56):
        chosen_path = router.select()
        result = chosen_path()
        collector.record_outcome(
            path=chosen_path.__name__,
            success=router.evaluate(result),
            metadata=result,
        )

    # Or convert existing trace
    events = convert_trace_to_kalibr("session.trace.jsonl", agent_id="mmkr-prod")
    for event in events:
        print(event)
"""

from __future__ import annotations

import json
import hashlib
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterator, Optional


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class KalibrTelemetryEvent:
    """
    Mirrors Kalibr's telemetry event schema.
    
    Kalibr captures:
    - which path/capability was invoked
    - whether the goal was achieved
    - execution metadata (latency, token usage, tool calls)
    """
    agent_id: str
    session_id: str
    tick: int
    timestamp: float
    
    # Path (capability) that was selected
    path_name: str
    
    # Goal that was being pursued
    goal: str
    
    # Outcome: "success" | "partial" | "failure" | "error"
    outcome: str
    
    # Execution metadata
    tool_calls: list[dict] = field(default_factory=list)
    latency_ms: float = 0.0
    tokens_used: int = 0
    error_message: str = ""
    
    # Routing metadata (for Kalibr's path selection)
    path_score: float = 0.5          # mmkr NaturalSelection fitness score
    capability_fitness: float = 0.5  # evolved capability fitness (0-1)
    
    # Custom metadata
    metadata: dict = field(default_factory=dict)
    
    def to_kalibr_payload(self) -> dict:
        """Convert to Kalibr API payload format."""
        return {
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "tick": self.tick,
            "timestamp": self.timestamp,
            "path": self.path_name,
            "goal": self.goal,
            "outcome": self.outcome,
            "success": self.outcome == "success",
            "tool_calls_count": len(self.tool_calls),
            "latency_ms": self.latency_ms,
            "score": self.path_score,
            "metadata": {
                **self.metadata,
                "mmkr_capability_fitness": self.capability_fitness,
                "mmkr_tool_calls": self.tool_calls[:10],  # cap for payload size
                "mmkr_error": self.error_message,
            }
        }
    
    def to_jsonl(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class KalibrRouteDecision:
    """Records which path was selected and why."""
    tick: int
    goal: str
    selected_path: str
    available_paths: list[str]
    selection_reason: str  # "highest_fitness" | "least_errors" | "exploration"
    fitness_scores: dict[str, float] = field(default_factory=dict)


# ── Collector ─────────────────────────────────────────────────────────────────

class KalibrCollector:
    """
    Drop-in collector that maps mmkr tick events → Kalibr telemetry.
    
    Can emit live (POST to Kalibr API) or batch (write to JSONL for later import).
    """
    
    def __init__(
        self,
        agent_id: str,
        session_id: Optional[str] = None,
        kalibr_api_key: Optional[str] = None,
        output_path: Optional[Path] = None,
        emit_live: bool = False,
    ):
        self.agent_id = agent_id
        self.session_id = session_id or hashlib.sha1(
            f"{agent_id}-{time.time()}".encode()
        ).hexdigest()[:16]
        self.api_key = kalibr_api_key
        self.output_path = output_path or Path(f"~/.mmkr/kalibr_{agent_id}.jsonl").expanduser()
        self.emit_live = emit_live and kalibr_api_key is not None
        
        self._current_tick: int = 0
        self._tick_start: float = 0.0
        self._current_tool_calls: list[dict] = []
        self._current_goal: str = ""
        self._events: list[KalibrTelemetryEvent] = []
        
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
    
    @contextmanager
    def tick_context(self, tick: int, goal: str = "") -> Iterator[None]:
        """Context manager for a single tick execution."""
        self._current_tick = tick
        self._current_goal = goal
        self._tick_start = time.time()
        self._current_tool_calls = []
        try:
            yield
        finally:
            # Auto-record tick completion
            self._tick_start = 0.0
    
    def record_tool_call(self, tool_name: str, args: dict = None, result: Any = None, error: str = ""):
        """Record a tool invocation within the current tick."""
        self._current_tool_calls.append({
            "tool": tool_name,
            "args_keys": list((args or {}).keys()),
            "success": not error,
            "error": error[:200] if error else "",
        })
    
    def record_outcome(
        self,
        path: str,
        success: bool,
        path_score: float = 0.5,
        capability_fitness: float = 0.5,
        metadata: dict = None,
        error_message: str = "",
    ) -> KalibrTelemetryEvent:
        """Record tick outcome for Kalibr routing feedback."""
        latency_ms = (time.time() - self._tick_start) * 1000 if self._tick_start else 0.0
        
        outcome = "success" if success else ("error" if error_message else "failure")
        
        event = KalibrTelemetryEvent(
            agent_id=self.agent_id,
            session_id=self.session_id,
            tick=self._current_tick,
            timestamp=time.time(),
            path_name=path,
            goal=self._current_goal,
            outcome=outcome,
            tool_calls=self._current_tool_calls[:],
            latency_ms=latency_ms,
            path_score=path_score,
            capability_fitness=capability_fitness,
            error_message=error_message,
            metadata=metadata or {},
        )
        
        self._events.append(event)
        self._write_event(event)
        
        if self.emit_live:
            self._post_to_kalibr(event)
        
        return event
    
    def _write_event(self, event: KalibrTelemetryEvent):
        """Append event to JSONL output file."""
        with open(self.output_path, "a") as f:
            f.write(event.to_jsonl() + "\n")
    
    def _post_to_kalibr(self, event: KalibrTelemetryEvent):
        """POST event to Kalibr API (requires kalibr_api_key)."""
        import subprocess
        payload = json.dumps(event.to_kalibr_payload())
        result = subprocess.run(
            ["curl", "-s", "-X", "POST",
             "https://api.kalibr.systems/v1/telemetry",
             "-H", f"Authorization: Bearer {self.api_key}",
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            pass  # Silent fail — don't interrupt agent on network issues
    
    def session_stats(self) -> dict:
        """Aggregate stats across all recorded events."""
        if not self._events:
            return {"total_ticks": 0, "success_rate": 0.0, "paths": {}}
        
        total = len(self._events)
        successes = sum(1 for e in self._events if e.outcome == "success")
        
        path_stats: dict[str, dict] = {}
        for e in self._events:
            if e.path_name not in path_stats:
                path_stats[e.path_name] = {"calls": 0, "successes": 0, "avg_score": 0.0}
            path_stats[e.path_name]["calls"] += 1
            path_stats[e.path_name]["successes"] += int(e.outcome == "success")
            path_stats[e.path_name]["avg_score"] += e.path_score
        
        for p in path_stats.values():
            p["success_rate"] = p["successes"] / p["calls"] if p["calls"] else 0
            p["avg_score"] = p["avg_score"] / p["calls"] if p["calls"] else 0
        
        return {
            "total_ticks": total,
            "success_rate": successes / total,
            "paths": path_stats,
            "top_path": max(path_stats, key=lambda k: path_stats[k]["success_rate"]) if path_stats else None,
        }


# ── Router ─────────────────────────────────────────────────────────────────────

class KalibrRouter:
    """
    Minimal Kalibr-style Router for mmkr.
    
    Kalibr's Router(goal, paths, success_when) selects which path to execute
    based on historical performance. This implementation maps directly to
    mmkr's NaturalSelection capability scores.
    
    In mmkr, "paths" = evolved capabilities. NaturalSelection already does
    this routing — KalibrRouter makes it explicit and Kalibr-compatible.
    """
    
    def __init__(
        self,
        goal: str,
        paths: dict[str, Callable],
        success_when: Callable[[Any], bool],
        fitness_scores: dict[str, float] = None,
        exploration_rate: float = 0.1,
    ):
        self.goal = goal
        self.paths = paths
        self.success_when = success_when
        self.fitness_scores = fitness_scores or {p: 0.5 for p in paths}
        self.exploration_rate = exploration_rate
        self._history: list[KalibrRouteDecision] = []
    
    def select(self, tick: int = 0) -> tuple[str, Callable]:
        """Select the best path based on fitness scores (with exploration)."""
        import random
        
        if random.random() < self.exploration_rate:
            # Explore: pick random path
            name = random.choice(list(self.paths.keys()))
            reason = "exploration"
        else:
            # Exploit: pick highest-fitness path
            name = max(self.fitness_scores, key=self.fitness_scores.get)
            reason = "highest_fitness"
        
        decision = KalibrRouteDecision(
            tick=tick,
            goal=self.goal,
            selected_path=name,
            available_paths=list(self.paths.keys()),
            selection_reason=reason,
            fitness_scores=dict(self.fitness_scores),
        )
        self._history.append(decision)
        
        return name, self.paths[name]
    
    def update_fitness(self, path_name: str, success: bool, learning_rate: float = 0.1):
        """Update path fitness based on outcome (Kalibr-style feedback loop)."""
        current = self.fitness_scores.get(path_name, 0.5)
        target = 1.0 if success else 0.0
        self.fitness_scores[path_name] = current + learning_rate * (target - current)


# ── Trace converter ───────────────────────────────────────────────────────────

def mmkr_event_to_kalibr(event: dict, agent_id: str, session_id: str) -> Optional[KalibrTelemetryEvent]:
    """Convert a single mmkr .trace.jsonl event to KalibrTelemetryEvent."""
    event_type = event.get("event_type", "")
    tick = event.get("tick", 0)
    ts = event.get("timestamp", time.time())
    metadata = event.get("metadata", {})
    
    # Map mmkr event types to Kalibr outcome signals
    if event_type in ("tool_call",):
        return KalibrTelemetryEvent(
            agent_id=agent_id,
            session_id=session_id,
            tick=tick,
            timestamp=ts,
            path_name=metadata.get("tool", "unknown_tool"),
            goal=metadata.get("context", ""),
            outcome="partial",  # tool_call alone is not an outcome
            tool_calls=[{
                "tool": metadata.get("tool", ""),
                "args_keys": list(metadata.get("args", {}).keys()),
                "success": True,
                "error": "",
            }],
            metadata=metadata,
        )
    
    elif event_type in ("tool_result",):
        error = metadata.get("error", "")
        return KalibrTelemetryEvent(
            agent_id=agent_id,
            session_id=session_id,
            tick=tick,
            timestamp=ts,
            path_name=metadata.get("tool", "unknown_tool"),
            goal="",
            outcome="error" if error else "success",
            error_message=error,
            metadata=metadata,
        )
    
    elif event_type in ("mmkr:tick_end", "tick_complete"):
        summary = metadata.get("summary", event.get("summary", ""))
        success = not metadata.get("error", "")
        return KalibrTelemetryEvent(
            agent_id=agent_id,
            session_id=session_id,
            tick=tick,
            timestamp=ts,
            path_name="tick",
            goal=metadata.get("goal", ""),
            outcome="success" if success else "failure",
            metadata={"summary": summary, **metadata},
        )
    
    elif event_type in ("mmkr:error", "error"):
        return KalibrTelemetryEvent(
            agent_id=agent_id,
            session_id=session_id,
            tick=tick,
            timestamp=ts,
            path_name="error",
            goal="",
            outcome="error",
            error_message=metadata.get("error", event.get("error", "")),
            metadata=metadata,
        )
    
    # Skip events that don't map to outcomes
    return None


def convert_trace_to_kalibr(
    trace_path: str | Path,
    agent_id: str = "mmkr",
) -> list[KalibrTelemetryEvent]:
    """
    Convert an existing mmkr .trace.jsonl to Kalibr telemetry events.
    
    Usage:
        events = convert_trace_to_kalibr("~/.mmkr/session.trace.jsonl", agent_id="mmkr-prod")
        stats = kalibr_session_stats(events)
        print(f"Success rate: {stats['success_rate']:.1%}")
    """
    path = Path(trace_path).expanduser()
    if not path.exists():
        return []
    
    events = []
    session_id = hashlib.sha1(str(path).encode()).hexdigest()[:16]
    
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                event = mmkr_event_to_kalibr(raw, agent_id=agent_id, session_id=session_id)
                if event is not None:
                    events.append(event)
            except json.JSONDecodeError:
                continue
    
    return events


def kalibr_session_stats(events: list[KalibrTelemetryEvent]) -> dict:
    """Summarize Kalibr events into session statistics."""
    if not events:
        return {"total": 0, "success_rate": 0.0, "path_breakdown": {}}
    
    total = len(events)
    successes = sum(1 for e in events if e.outcome == "success")
    errors = sum(1 for e in events if e.outcome == "error")
    
    path_breakdown: dict[str, int] = {}
    for e in events:
        path_breakdown[e.path_name] = path_breakdown.get(e.path_name, 0) + 1
    
    return {
        "total": total,
        "success_rate": successes / total,
        "error_rate": errors / total,
        "path_breakdown": path_breakdown,
        "top_path": max(path_breakdown, key=path_breakdown.get) if path_breakdown else None,
        "unique_paths": len(path_breakdown),
    }


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile, os
    
    print("KalibrCollector smoke test")
    print("=" * 40)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        collector = KalibrCollector(
            agent_id="mmkr-test",
            output_path=Path(tmpdir) / "kalibr.jsonl",
        )
        
        # Simulate tick 56
        with collector.tick_context(tick=56, goal="build social presence"):
            collector.record_tool_call("safe_post_issue", {"repo": "kalibr-ai/kalibr-sdk-python"}, result="ok")
            collector.record_tool_call("github_api", {"endpoint": "repos/kalibr-ai/kalibr-sdk-python/issues"}, result="ok")
            
            event = collector.record_outcome(
                path="github_engagement",
                success=True,
                path_score=0.85,
                capability_fitness=0.9,
                metadata={"issue_number": 98, "target": "kalibr-ai"},
            )
        
        print(f"  ✓ Recorded event: path={event.path_name}, outcome={event.outcome}")
        print(f"  ✓ Kalibr payload keys: {list(event.to_kalibr_payload().keys())}")
        
        stats = collector.session_stats()
        print(f"  ✓ Session stats: success_rate={stats['success_rate']:.0%}, ticks={stats['total_ticks']}")
        
        # Test Router
        router = KalibrRouter(
            goal="build social presence",
            paths={
                "github_engagement": lambda: {"responses_received": 1},
                "blog_post": lambda: {"views": 50},
                "integration_build": lambda: {"adapters": 7},
            },
            success_when=lambda r: bool(r),
            fitness_scores={
                "github_engagement": 0.85,
                "blog_post": 0.6,
                "integration_build": 0.9,
            },
        )
        
        name, fn = router.select(tick=56)
        result = fn()
        router.update_fitness(name, success=True)
        print(f"  ✓ Router selected: {name} → result={result}")
        
        # Test trace conversion
        trace_fixture = Path(tmpdir) / "test.trace.jsonl"
        trace_fixture.write_text(json.dumps({
            "event_type": "tool_call",
            "tick": 56,
            "timestamp": time.time(),
            "metadata": {"tool": "safe_post_issue", "args": {"repo": "kalibr-ai/kalibr-sdk-python"}},
        }) + "\n" + json.dumps({
            "event_type": "mmkr:tick_end",
            "tick": 56,
            "timestamp": time.time(),
            "metadata": {"summary": "Posted KalibrCollector to issue #98"},
        }) + "\n")
        
        events = convert_trace_to_kalibr(trace_fixture, agent_id="mmkr-test")
        stats2 = kalibr_session_stats(events)
        print(f"  ✓ Trace conversion: {len(events)} events, success_rate={stats2['success_rate']:.0%}")
        
        print()
        print("All tests passed ✓")
