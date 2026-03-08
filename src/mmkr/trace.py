"""Real-time trace protocol — self-describing tick execution.

TickTraceCollector emits events as tick phases execute.
ConsoleCollector prints real-time to stdout.
FileCollector appends JSONL to file — full reasoning log for training.
MultiCollector fans out to multiple collectors.

All through capabilities and fold.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class TickTraceCollector(Protocol):
    """Real-time event collector for tick execution.

    Full lifecycle: phase start/complete, LLM prompts/responses,
    tool calls/results, actions, decisions, wealth updates.
    """

    def phase_start(self, tick: int, name: str) -> None: ...
    def phase_complete(self, tick: int, name: str, duration_ms: float, summary: str) -> None: ...
    def llm_call(self, tick: int, model: str, duration_ms: float, success: bool) -> None: ...
    def error(self, tick: int, phase: str, message: str) -> None: ...
    def llm_prompt(self, tick: int, phase: str, prompt: str) -> None: ...
    def llm_response(self, tick: int, phase: str, response: str) -> None: ...
    def tool_call(self, tick: int, phase: str, tool_name: str, args: str) -> None: ...
    def tool_result(self, tick: int, phase: str, tool_name: str, result: str) -> None: ...
    def action(self, tick: int, action_type: str, description: str, tool_used: str, succeeded: bool, result: str) -> None: ...
    def decision(self, tick: int, phase: str, message: str) -> None: ...
    def wealth_update(self, tick: int, before: str, after: str, delta: str) -> None: ...


class ConsoleCollector:
    """Pretty real-time trace with emojis — human-readable life narration."""

    def phase_start(self, tick: int, name: str) -> None:
        print(f"  🔄 {name}...", flush=True)

    def phase_complete(self, tick: int, name: str, duration_ms: float, summary: str) -> None:
        print(f"  ✅ {name} — {summary} ({duration_ms:.0f}ms)", flush=True)

    def llm_call(self, tick: int, model: str, duration_ms: float, success: bool) -> None:
        icon = "🧠" if success else "💀"
        print(f"    {icon} {model} ({duration_ms:.0f}ms)", flush=True)

    def error(self, tick: int, phase: str, message: str) -> None:
        print(f"  ❌ [{phase}] {message}", flush=True)

    def llm_prompt(self, tick: int, phase: str, prompt: str) -> None:
        print(f"    💭 {prompt}", flush=True)

    def llm_response(self, tick: int, phase: str, response: str) -> None:
        print(f"    💬 {response}", flush=True)

    def tool_call(self, tick: int, phase: str, tool_name: str, args: str) -> None:
        print(f"    🔧 {tool_name}({args[:120]})", flush=True)

    def tool_result(self, tick: int, phase: str, tool_name: str, result: str) -> None:
        print(f"    📦 {tool_name} → {result[:200]}", flush=True)

    def action(self, tick: int, action_type: str, description: str, tool_used: str, succeeded: bool, result: str) -> None:
        icon = "⚡" if succeeded else "💥"
        print(f"    {icon} {action_type}: {description}", flush=True)
        if tool_used:
            print(f"       🔧 {tool_used}", flush=True)
        if result:
            print(f"       → {result[:200]}", flush=True)

    def decision(self, tick: int, phase: str, message: str) -> None:
        print(f"    🎯 {message}", flush=True)

    def wealth_update(self, tick: int, before: str, after: str, delta: str) -> None:
        icon = "📈" if float(delta) >= 0 else "📉"
        print(f"    {icon} ${before} → ${after} (Δ${delta})", flush=True)


class FileCollector:
    """Appends JSONL trace to file — full reasoning log for training."""

    __slots__ = ("_path", "_fh")

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._path.open("a", encoding="utf-8")

    def _emit(self, event: dict[str, str | int | float | bool]) -> None:
        event["ts"] = time.time()
        self._fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._fh.flush()

    def phase_start(self, tick: int, name: str) -> None:
        self._emit({"event": "phase_start", "tick": tick, "phase": name})

    def phase_complete(self, tick: int, name: str, duration_ms: float, summary: str) -> None:
        self._emit({"event": "phase_complete", "tick": tick, "phase": name, "duration_ms": round(duration_ms, 1), "summary": summary})

    def llm_call(self, tick: int, model: str, duration_ms: float, success: bool) -> None:
        self._emit({"event": "llm_call", "tick": tick, "model": model, "duration_ms": round(duration_ms, 1), "success": success})

    def error(self, tick: int, phase: str, message: str) -> None:
        self._emit({"event": "error", "tick": tick, "phase": phase, "message": message})

    def llm_prompt(self, tick: int, phase: str, prompt: str) -> None:
        self._emit({"event": "llm_prompt", "tick": tick, "phase": phase, "prompt": prompt})

    def llm_response(self, tick: int, phase: str, response: str) -> None:
        self._emit({"event": "llm_response", "tick": tick, "phase": phase, "response": response})

    def tool_call(self, tick: int, phase: str, tool_name: str, args: str) -> None:
        self._emit({"event": "tool_call", "tick": tick, "phase": phase, "tool": tool_name, "args": args})

    def tool_result(self, tick: int, phase: str, tool_name: str, result: str) -> None:
        self._emit({"event": "tool_result", "tick": tick, "phase": phase, "tool": tool_name, "result": result})

    def action(self, tick: int, action_type: str, description: str, tool_used: str, succeeded: bool, result: str) -> None:
        self._emit({"event": "action", "tick": tick, "type": action_type, "description": description, "tool_used": tool_used, "succeeded": succeeded, "result": result})

    def decision(self, tick: int, phase: str, message: str) -> None:
        self._emit({"event": "decision", "tick": tick, "phase": phase, "message": message})

    def wealth_update(self, tick: int, before: str, after: str, delta: str) -> None:
        self._emit({"event": "wealth_update", "tick": tick, "before": before, "after": after, "delta": delta})

    def close(self) -> None:
        self._fh.close()


class MultiCollector:
    """Fans out trace events to multiple collectors."""

    __slots__ = ("_collectors",)

    def __init__(self, collectors: Sequence[TickTraceCollector]) -> None:
        self._collectors = tuple(collectors)

    def phase_start(self, tick: int, name: str) -> None:
        for c in self._collectors:
            c.phase_start(tick, name)

    def phase_complete(self, tick: int, name: str, duration_ms: float, summary: str) -> None:
        for c in self._collectors:
            c.phase_complete(tick, name, duration_ms, summary)

    def llm_call(self, tick: int, model: str, duration_ms: float, success: bool) -> None:
        for c in self._collectors:
            c.llm_call(tick, model, duration_ms, success)

    def error(self, tick: int, phase: str, message: str) -> None:
        for c in self._collectors:
            c.error(tick, phase, message)

    def llm_prompt(self, tick: int, phase: str, prompt: str) -> None:
        for c in self._collectors:
            c.llm_prompt(tick, phase, prompt)

    def llm_response(self, tick: int, phase: str, response: str) -> None:
        for c in self._collectors:
            c.llm_response(tick, phase, response)

    def tool_call(self, tick: int, phase: str, tool_name: str, args: str) -> None:
        for c in self._collectors:
            c.tool_call(tick, phase, tool_name, args)

    def tool_result(self, tick: int, phase: str, tool_name: str, result: str) -> None:
        for c in self._collectors:
            c.tool_result(tick, phase, tool_name, result)

    def action(self, tick: int, action_type: str, description: str, tool_used: str, succeeded: bool, result: str) -> None:
        for c in self._collectors:
            c.action(tick, action_type, description, tool_used, succeeded, result)

    def decision(self, tick: int, phase: str, message: str) -> None:
        for c in self._collectors:
            c.decision(tick, phase, message)

    def wealth_update(self, tick: int, before: str, after: str, delta: str) -> None:
        for c in self._collectors:
            c.wealth_update(tick, before, after, delta)


class TraceTimer:
    """Context manager for timing a trace phase."""

    __slots__ = ("_trace", "_tick", "_name", "_start")

    def __init__(self, trace: TickTraceCollector | None, tick: int, name: str) -> None:
        self._trace = trace
        self._tick = tick
        self._name = name
        self._start = 0.0

    def __enter__(self) -> TraceTimer:
        if self._trace is not None:
            self._trace.phase_start(self._tick, self._name)
        self._start = time.monotonic()
        return self

    def complete(self, summary: str) -> None:
        elapsed_ms = (time.monotonic() - self._start) * 1000
        if self._trace is not None:
            self._trace.phase_complete(self._tick, self._name, elapsed_ms, summary)

    def __exit__(self, *_: object) -> None:
        pass
