"""Integration smoke tests for mmkr.

These tests verify the integrations can run without errors and produce
structurally valid output. They don't require external services.

Run:
    python3 -m pytest tests/ -v
    # or without pytest:
    python3 tests/test_integrations.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Make integrations importable when running from mmkr root
sys.path.insert(0, str(Path(__file__).parent.parent))


# ─── Hydra Ingestor ──────────────────────────────────────────────────────────

def test_hydra_ingest_from_fixture():
    """ingest_agent_trace() reads a .trace.jsonl and produces HydraTimelineEvents."""
    from integrations.hydra_ingestor import ingest_agent_trace, group_by_tick

    fixture = [
        {"ts": "2026-03-07T00:00:00Z", "agent_id": "test-agent", "session_id": "sess_test",
         "tick": 1, "event_type": "tick_start", "outcome": "success"},
        {"ts": "2026-03-07T00:00:30Z", "agent_id": "test-agent", "session_id": "sess_test",
         "tick": 1, "event_type": "tool_call", "tool": "load_memories", "target": "all",
         "outcome": "success"},
        {"ts": "2026-03-07T00:01:00Z", "agent_id": "test-agent", "session_id": "sess_test",
         "tick": 1, "event_type": "action", "summary": "Loaded 5 memories",
         "outcome": "success"},
        {"ts": "2026-03-07T00:02:00Z", "agent_id": "test-agent", "session_id": "sess_test",
         "tick": 1, "event_type": "tick_complete", "outcome": "success",
         "metadata": {"duration_ms": 12000}},
    ]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".trace.jsonl", delete=False) as f:
        for e in fixture:
            f.write(json.dumps(e) + "\n")
        path = f.name

    events = ingest_agent_trace(path)
    assert len(events) == 4, f"Expected 4 events, got {len(events)}"

    # Verify structure: ingest_agent_trace returns normalized events with 'event' key (renamed from event_type)
    for e in events:
        assert "ts" in e
        assert "tick" in e
        assert "agent_id" in e

    # group_by_tick produces one group per tick
    groups = group_by_tick(events)
    assert 1 in groups, "Expected tick 1 in groups"
    assert len(groups[1]) == 4

    print(f"  ✓ hydra ingest: {len(events)} events, {len(groups)} tick(s)")


def test_hydra_collector_emits_valid_jsonl():
    """HydraCollector writes valid JSONL that can be re-parsed."""
    from integrations.hydra_ingestor import HydraCollector

    with tempfile.NamedTemporaryFile(mode="w", suffix=".hydra.jsonl", delete=False) as f:
        path = f.name

    # HydraCollector uses named methods (not emit) — typed API
    collector = HydraCollector(
        path,
        agent_id="test-agent",
        session_id="sess_test",
    )

    collector.phase_start(tick=1, name="observe")
    collector.tool_call(tick=1, phase="act", tool_name="save_memory", args="{}")
    collector.tool_result(tick=1, phase="act", tool_name="save_memory", result="ok")
    collector.action(tick=1, action_type="memory_save", description="Test tick",
                     tool_used="save_memory", succeeded=True, result="ok")
    collector.close()

    lines = Path(path).read_text().strip().split("\n")
    lines = [l for l in lines if l.strip()]
    assert len(lines) >= 1, f"Expected at least 1 line, got {len(lines)}"

    for line in lines:
        obj = json.loads(line)
        assert "ts" in obj
        assert "agent_id" in obj

    print(f"  ✓ hydra collector: emitted {len(lines)} valid JSONL events")


# ─── Slopometry Collector ────────────────────────────────────────────────────

def test_slopometry_mmkr_event_to_hook_mapping():
    """mmkr_event_to_hook() maps all known event types correctly."""
    from integrations.slopometry_collector import mmkr_event_to_hook, HOOK_EVENT_TYPE_MAP

    for mmkr_type, expected_hook_type in HOOK_EVENT_TYPE_MAP.items():
        event = {
            "ts": "2026-03-07T00:00:00Z",
            "event_type": mmkr_type,
            "tick": 1,
            "tool": "test_tool",
            "agent_id": "test-agent",
            "session_id": "sess_test",
            "outcome": "success",
            "summary": f"test {mmkr_type}",
        }
        hook = mmkr_event_to_hook(event)
        # hook uses 'hookEventName' key (matches slopometry's HookEvent schema)
        assert hook["hookEventName"] == expected_hook_type, (
            f"Expected {expected_hook_type} for {mmkr_type}, got {hook.get('hookEventName')}"
        )
        assert "timestamp" in hook
        assert "sessionId" in hook

    print(f"  ✓ slopometry mapping: {len(HOOK_EVENT_TYPE_MAP)} event types mapped correctly")


def test_slopometry_convert_trace():
    """convert_trace_to_slopometry() reads mmkr JSONL and produces HookEvents."""
    from integrations.slopometry_collector import convert_trace_to_slopometry, session_stats

    fixture = [
        {"ts": "2026-03-07T00:00:00Z", "agent_id": "test", "session_id": "sess_test",
         "tick": 1, "event_type": "tick_start", "outcome": "success"},
        {"ts": "2026-03-07T00:00:30Z", "agent_id": "test", "session_id": "sess_test",
         "tick": 1, "event_type": "tool_call", "tool": "github_api", "outcome": "success"},
        {"ts": "2026-03-07T00:01:00Z", "agent_id": "test", "session_id": "sess_test",
         "tick": 1, "event_type": "tool_result", "tool": "github_api", "outcome": "success"},
        {"ts": "2026-03-07T00:02:00Z", "agent_id": "test", "session_id": "sess_test",
         "tick": 1, "event_type": "tick_complete", "outcome": "success"},
    ]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".trace.jsonl", delete=False) as f:
        for e in fixture:
            f.write(json.dumps(e) + "\n")
        trace_path = f.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".slopometry.jsonl", delete=False) as f:
        slopo_path = f.name

    hook_events = convert_trace_to_slopometry(trace_path, slopo_path)
    assert len(hook_events) == 4

    stats = session_stats(hook_events)
    assert stats["total_events"] == 4
    assert stats["tool_call_count"] == 1
    assert stats["error_count"] == 0
    assert "github_api" in stats["tool_breakdown"]

    print(f"  ✓ slopometry convert: {len(hook_events)} events, stats={stats['tool_call_count']} tool calls")


def test_slopometry_collector_emits():
    """SlopometryCollector emits valid HookEvent JSONL in real-time."""
    from integrations.slopometry_collector import SlopometryCollector

    with tempfile.NamedTemporaryFile(mode="w", suffix=".slopo.jsonl", delete=False) as f:
        path = f.name

    collector = SlopometryCollector(
        path,
        agent_id="test-agent",
        session_id="sess_test",
    )

    collector.emit("tick_start", tick=5, phase="observe", summary="tick 5 start")
    collector.emit("tool_call", tick=5, tool="save_memory", phase="act")
    collector.emit("tool_result", tick=5, tool="save_memory", outcome="success", phase="act")
    collector.emit("action", tick=5, summary="Saved memory about slopometry", phase="act")
    collector.emit("tick_complete", tick=5, summary="tick 5 done", phase="persist")
    collector.flush()

    lines = [l for l in Path(path).read_text().strip().split("\n") if l.strip()]
    assert len(lines) == 5

    for line in lines:
        obj = json.loads(line)
        assert "hookEventName" in obj  # HookEventType
        assert "timestamp" in obj
        assert "sessionId" in obj

    print(f"  ✓ slopometry collector: emitted {len(lines)} valid HookEvents")


# ─── Schema Validation ────────────────────────────────────────────────────────

def test_trace_schema_required_fields():
    """Verify the mmkr trace schema validates required fields."""
    required = {"ts", "agent_id", "session_id", "tick", "event_type", "outcome"}
    optional = {"tool", "target", "summary", "phase", "metadata"}

    # Minimal valid event
    event = {
        "ts": "2026-03-07T00:00:00Z",
        "agent_id": "test-agent",
        "session_id": "sess_test",
        "tick": 1,
        "event_type": "tick_start",
        "outcome": "success",
    }

    missing = required - set(event.keys())
    assert not missing, f"Missing required fields: {missing}"

    # All values are JSON-serializable
    json_str = json.dumps(event)
    reparsed = json.loads(json_str)
    assert reparsed == event

    print(f"  ✓ schema: required fields {required} all present and JSON-serializable")


# ─── Runner ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        ("hydra ingest from fixture", test_hydra_ingest_from_fixture),
        ("hydra collector emits valid jsonl", test_hydra_collector_emits_valid_jsonl),
        ("slopometry event mapping", test_slopometry_mmkr_event_to_hook_mapping),
        ("slopometry convert trace", test_slopometry_convert_trace),
        ("slopometry collector emits", test_slopometry_collector_emits),
        ("trace schema required fields", test_trace_schema_required_fields),
    ]

    passed = 0
    failed = 0

    print("\nmmkr integration tests\n" + "=" * 40)
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            failed += 1

    print(f"\n{'=' * 40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
    else:
        print("All tests passed ✓")
