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
    # tick_start is filtered (None mapping), so 3 events pass: tool_call, action, tick_complete
    assert len(events) == 3, f"Expected 3 events (tick_start filtered), got {len(events)}"

    # Verify structure
    for e in events:
        assert "ts" in e
        assert "tick" in e
        assert "agent_id" in e

    # group_by_tick produces one group per tick
    groups = group_by_tick(events)
    assert 1 in groups, "Expected tick 1 in groups"
    assert len(groups[1]) == 3

    print(f"  ✓ hydra ingest: {len(events)} events, {len(groups)} tick(s) (tick_start filtered per spec)")


def test_hydra_collector_emits_valid_jsonl():
    """HydraCollector writes valid JSONL that can be re-parsed."""
    from integrations.hydra_ingestor import HydraCollector

    with tempfile.TemporaryDirectory() as tmpdir:
        collector = HydraCollector(
            agent_id="test-agent",
            session_id="sess_test",
            feed_dir=tmpdir,
        )

        collector.on_external_action(tick=1, tool="save_memory", target="memories",
                                      outcome="success")
        collector.on_checkpoint(tick=1, summary="Test checkpoint")
        collector.on_tick_end(tick=1, summary="Test tick complete")

        trace_path = Path(tmpdir) / "test-agent.trace.jsonl"
        assert trace_path.exists(), "trace.jsonl not written"
        lines = trace_path.read_text().strip().split("\n")
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



def test_kalibr_collector_records_outcome():
    """KalibrCollector records events with correct outcome mapping."""
    import time, tempfile
    from integrations.kalibr_collector import KalibrCollector
    
    with tempfile.TemporaryDirectory() as tmpdir:
        collector = KalibrCollector(
            agent_id="test-agent",
            output_path=Path(tmpdir) / "kalibr.jsonl",
        )
        
        with collector.tick_context(tick=1, goal="test goal"):
            collector.record_tool_call("test_tool", {"key": "value"}, result="ok")
            event = collector.record_outcome(
                path="github_engagement",
                success=True,
                path_score=0.85,
            )
        
        assert event.outcome == "success", f"Expected success, got {event.outcome}"
        assert event.path_name == "github_engagement"
        assert event.path_score == 0.85
        assert len(event.tool_calls) == 1
        assert event.tool_calls[0]["tool"] == "test_tool"
        
        payload = event.to_kalibr_payload()
        assert payload["success"] is True
        assert payload["path"] == "github_engagement"
        assert "mmkr_capability_fitness" in payload["metadata"]
        
        print("  ✓ kalibr collector records outcome correctly")


def test_kalibr_router_selects_best_path():
    """KalibrRouter selects highest-fitness path in exploit mode."""
    from integrations.kalibr_collector import KalibrRouter
    import random
    random.seed(42)  # deterministic (seed 42 → exploit, not explore at 10%)
    
    router = KalibrRouter(
        goal="build something",
        paths={
            "path_a": lambda: "a",
            "path_b": lambda: "b",
            "path_c": lambda: "c",
        },
        success_when=lambda r: bool(r),
        fitness_scores={"path_a": 0.3, "path_b": 0.9, "path_c": 0.5},
    )
    
    # Run 10 selections and check most are path_b (highest fitness)
    selected = []
    for i in range(10):
        name, fn = router.select(tick=i)
        selected.append(name)
    
    # path_b should dominate (exploration_rate=0.1 → ~90% exploitation)
    path_b_count = selected.count("path_b")
    assert path_b_count >= 7, f"Expected path_b dominant, got {selected}"
    
    print(f"  ✓ kalibr router selects best path ({path_b_count}/10 → path_b)")


def test_kalibr_convert_trace():
    """convert_trace_to_kalibr converts mmkr JSONL → KalibrTelemetryEvents."""
    import time, tempfile, json
    from integrations.kalibr_collector import convert_trace_to_kalibr, kalibr_session_stats
    
    with tempfile.TemporaryDirectory() as tmpdir:
        trace = Path(tmpdir) / "session.trace.jsonl"
        trace.write_text(
            json.dumps({"event_type": "tool_call", "tick": 1, "timestamp": time.time(),
                        "metadata": {"tool": "safe_post_issue", "args": {"repo": "test/repo"}}}) + "\n" +
            json.dumps({"event_type": "tool_result", "tick": 1, "timestamp": time.time(),
                        "metadata": {"tool": "safe_post_issue", "result": "ok"}}) + "\n" +
            json.dumps({"event_type": "mmkr:tick_end", "tick": 1, "timestamp": time.time(),
                        "metadata": {"summary": "Posted issue to test/repo", "goal": "social"}}) + "\n"
        )
        
        events = convert_trace_to_kalibr(trace, agent_id="mmkr-test")
        assert len(events) == 3, f"Expected 3 events, got {len(events)}"
        
        # Last event = tick_end → success
        tick_end = [e for e in events if e.path_name == "tick"]
        assert len(tick_end) == 1
        assert tick_end[0].outcome == "success"
        
        stats = kalibr_session_stats(events)
        assert "success_rate" in stats
        assert "path_breakdown" in stats
        assert "safe_post_issue" in stats["path_breakdown"]
        
        print(f"  ✓ kalibr trace conversion: {len(events)} events, rate={stats['success_rate']:.0%}")

if __name__ == "__main__":
    tests = [
        ("hydra ingest from fixture", test_hydra_ingest_from_fixture),
        ("hydra collector emits valid jsonl", test_hydra_collector_emits_valid_jsonl),
        ("slopometry event mapping", test_slopometry_mmkr_event_to_hook_mapping),
        ("slopometry convert trace", test_slopometry_convert_trace),
        ("slopometry collector emits", test_slopometry_collector_emits),
        ("trace schema required fields", test_trace_schema_required_fields),
        ("kalibr collector records outcome", test_kalibr_collector_records_outcome),
        ("kalibr router selects best path", test_kalibr_router_selects_best_path),
        ("kalibr convert trace", test_kalibr_convert_trace),
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


# ─── NetherBrain Adapter ─────────────────────────────────────────────────────

def test_netherbrain_mmkr_event_to_netherbrain():
    """mmkr_event_to_netherbrain() maps all core event types correctly."""
    from integrations.netherbrain_adapter import mmkr_event_to_netherbrain, NetherBrainEvent, EventType

    test_events = [
        {"ts": "2026-03-07T00:00:00Z", "agent_id": "test", "session_id": "sess_t",
         "tick": 1, "event_type": "tool_call", "tool": "save_memory", "outcome": "success"},
        {"ts": "2026-03-07T00:00:01Z", "agent_id": "test", "session_id": "sess_t",
         "tick": 1, "event_type": "tool_result", "tool": "save_memory", "result": "saved",
         "outcome": "success"},
        {"ts": "2026-03-07T00:00:02Z", "agent_id": "test", "session_id": "sess_t",
         "tick": 1, "event_type": "tick_start", "outcome": "success"},
        {"ts": "2026-03-07T00:00:03Z", "agent_id": "test", "session_id": "sess_t",
         "tick": 1, "event_type": "tick_complete", "outcome": "success"},
        {"ts": "2026-03-07T00:00:04Z", "agent_id": "test", "session_id": "sess_t",
         "tick": 1, "event_type": "action", "summary": "Did something", "outcome": "success"},
        {"ts": "2026-03-07T00:00:05Z", "agent_id": "test", "session_id": "sess_t",
         "tick": 1, "event_type": "error", "message": "oops", "outcome": "fail"},
    ]

    mapped_types = []
    for e in test_events:
        nb_event = mmkr_event_to_netherbrain(e)
        if nb_event is not None:
            assert isinstance(nb_event, NetherBrainEvent)
            assert nb_event.session_id == "sess_t"
            assert nb_event.type in (EventType.TEXT, EventType.METADATA,
                                     EventType.TOOL_CALL, EventType.TOOL_RETURN,
                                     EventType.ERROR, EventType.DONE)
            mapped_types.append(nb_event.type)

    assert len(mapped_types) >= 5, f"Expected >=5 mapped events, got {len(mapped_types)}"
    print(f"  ✓ netherbrain mapping: {len(mapped_types)} events mapped → types: {set(mapped_types)}")


def test_netherbrain_convert_trace():
    """convert_trace_to_netherbrain() round-trips a .trace.jsonl file."""
    import tempfile
    from integrations.netherbrain_adapter import convert_trace_to_netherbrain, group_by_conversation

    fixture = [
        {"ts": "2026-03-07T00:00:00Z", "agent_id": "test", "session_id": "sess_a",
         "tick": 1, "event_type": "tick_start", "outcome": "success"},
        {"ts": "2026-03-07T00:00:30Z", "agent_id": "test", "session_id": "sess_a",
         "tick": 1, "event_type": "tool_call", "tool": "load_memories", "outcome": "success"},
        {"ts": "2026-03-07T00:01:00Z", "agent_id": "test", "session_id": "sess_a",
         "tick": 1, "event_type": "action", "summary": "Loaded memories", "outcome": "success"},
        {"ts": "2026-03-07T00:02:00Z", "agent_id": "test", "session_id": "sess_a",
         "tick": 1, "event_type": "tick_complete", "outcome": "success"},
    ]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".trace.jsonl", delete=False) as f:
        for e in fixture:
            f.write(json.dumps(e) + "\n")
        path = f.name

    events = convert_trace_to_netherbrain(path)
    assert len(events) > 0, "Expected at least 1 NetherBrain event"

    # Verify structure
    for e in events:
        assert hasattr(e, "type")
        assert hasattr(e, "session_id")
        assert hasattr(e, "content")

    # Group by conversation
    groups = group_by_conversation(events)
    assert "sess_a" in groups, f"Expected session 'sess_a' in groups, got: {list(groups.keys())}"

    print(f"  ✓ netherbrain convert: {len(events)} events, {len(groups)} conversation(s)")


def test_netherbrain_sse_serialization():
    """NetherBrainEvent.to_sse_line() produces valid SSE format."""
    from integrations.netherbrain_adapter import NetherBrainEvent, EventType

    event = NetherBrainEvent(
        type=EventType.TEXT,
        session_id="sess_test",
        content="Tick 38 complete: shipped netherbrain tests",
        metadata={"tick": 38},
    )

    sse = event.to_sse_line()
    assert sse.startswith("data: "), f"SSE should start with 'data: ', got: {sse[:20]}"

    # Parse the JSON payload
    payload = json.loads(sse[6:])
    assert payload["type"] == EventType.TEXT
    assert payload["session_id"] == "sess_test"
    assert "tick" in payload.get("metadata", {})

    print(f"  ✓ netherbrain SSE: valid format, payload={list(payload.keys())}")



def _run_netherbrain_tests():
    """Run NetherBrain adapter tests."""
    tests = [
        test_netherbrain_mmkr_event_to_netherbrain,
        test_netherbrain_convert_trace,
        test_netherbrain_sse_serialization,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {e}")
            failed += 1
    return passed, failed



# ── Syke Adapter ───────────────────────────────────────────────────────────────

def test_syke_read_memory_events():
    """read_memory_events() reads a .memories.json dict and emits SykeEvents."""
    from integrations.syke_adapter import read_memory_events, SykeEvent
    import json, tempfile

    memories_data = {
        "tick_outcome": [
            {"content": "Tick 39 — Built integration docs.", "created_at": "2026-03-07T04:15:00Z"}
        ],
        "primary_mission": [
            {"content": "PRIMARY MISSION: develop mmkr, spread copies.", "created_at": "2026-03-07T01:00:00Z"}
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(memories_data, f)
        path = f.name

    events = list(read_memory_events(memories_path=path))
    assert len(events) >= 2
    for ev in events:
        assert ev.source in ("mmkr-memory", "mmkr")
        assert ev.content
        assert ev.timestamp

    print(f"  ✓ syke read_memory_events: {len(events)} events from 2 categories")


def test_syke_read_trace_events():
    """read_trace_events() aggregates .trace.jsonl by tick into SykeEvents."""
    from integrations.syke_adapter import read_trace_events
    import json, tempfile

    trace_events = [
        {"ts": "2026-03-07T04:00:00Z", "agent_id": "test", "session_id": "sess_test",
         "tick": 1, "event_type": "tool_call", "tool": "save_memory", "outcome": "success"},
        {"ts": "2026-03-07T04:00:30Z", "agent_id": "test", "session_id": "sess_test",
         "tick": 1, "event_type": "tick_complete", "outcome": "success"},
        {"ts": "2026-03-07T04:01:00Z", "agent_id": "test", "session_id": "sess_test",
         "tick": 2, "event_type": "tool_call", "tool": "Bash", "outcome": "success"},
    ]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".trace.jsonl", delete=False) as f:
        for ev in trace_events:
            f.write(json.dumps(ev) + "\n")
        path = f.name

    events = list(read_trace_events(trace_path=path))
    # Should have 2 events (one per tick)
    assert len(events) >= 1
    ticks = {int(ev.metadata.get("tick", 0)) for ev in events}
    assert 1 in ticks and 2 in ticks

    print(f"  ✓ syke read_trace_events: {len(events)} tick-aggregated events")


def test_syke_events_to_json():
    """events_to_syke_json() produces valid JSON array."""
    from integrations.syke_adapter import read_mmkr_events, events_to_syke_json, read_trace_events
    import json, tempfile

    memories_data = {"test": [{"content": "Test memory.", "created_at": "2026-03-07T04:00:00Z"}]}
    trace_events = [
        {"ts": "2026-03-07T04:00:00Z", "agent_id": "test", "session_id": "s",
         "tick": 1, "event_type": "tick_complete", "outcome": "success"},
    ]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as mf:
        json.dump(memories_data, mf)
        mem_path = mf.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".trace.jsonl", delete=False) as tf:
        for ev in trace_events:
            tf.write(json.dumps(ev) + "\n")
        trace_path = tf.name

    import os; os.environ["_SYKE_TEST_MEM"] = mem_path; events = list(read_mmkr_events(data_dir=os.path.dirname(trace_path), trace_path=trace_path))
    output = events_to_syke_json(events)
    parsed = json.loads(output)
    assert isinstance(parsed, list)
    assert len(parsed) >= 1
    for item in parsed:
        assert "source" in item
        assert "content" in item

    print(f"  ✓ syke events_to_syke_json: {len(parsed)} events, valid JSON")


# ── mmkr_verify ─────────────────────────────────────────────────────────────────

def test_verify_generate_and_verify():
    """generate_proof() + verify_proof() round-trip."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from mmkr_verify import generate_proof, verify_proof
    import tempfile, pathlib

    # Generate proof with temp data dir
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = pathlib.Path(tmpdir)
        (data_dir / "memories.json").write_text('{"test": [{"content": "hello"}]}')

        proof = generate_proof(
            agent_id="test-agent",
            session_id="sess_test",
            tick=1,
            wallet_address="0x0000",
            data_dir=data_dir,
        )

        assert proof.agent_id == "test-agent"
        assert proof.tick == 1
        assert proof.memory_hash != ""
        assert proof.signature != ""

        # Verify it
        proof_dict = vars(proof)  # include all fields including proof_generated_at
        valid, reason = verify_proof(proof_dict)
        assert valid, f"Proof invalid: {reason}"

    print(f"  ✓ mmkr_verify: generate + verify round-trip OK")


def _run_syke_and_verify_tests():
    tests = [
        test_syke_read_memory_events,
        test_syke_read_trace_events,
        test_syke_events_to_json,
        test_verify_generate_and_verify,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            import traceback
            print(f"  ✗ {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    return passed, failed


# ── Kalibr tests ─────────────────────────────────────────────────────────────
