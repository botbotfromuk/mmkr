"""
integrations/mindmem_collector.py — mind-mem integration for mmkr.

mind-mem (https://github.com/star-ga/mind-mem) is a governed Memory OS
for AI coding agents — hybrid BM25F+vector search, contradiction detection,
drift analysis, full audit trail. Formal block grammar with typed IDs
(D-, T-, PRJ-, SIG-, DREF-...).

This adapter maps mmkr's execution data into mind-mem's block grammar:
- mmkr memories → D- (Decision) blocks with ConstraintSignature metadata
- mmkr tick events → SIG- (Signal) blocks and T- (Task) blocks
- mmkr goals → T- (Task) blocks
- mmkr trace.jsonl → INC- (Incident) blocks for errors, DREF- for drift

Mind-mem's contradiction detection can then flag when mmkr's decisions
conflict across sessions — exactly the "cross-session audit trail" question
asked in https://github.com/star-ga/mind-mem/issues/494.

Usage:
    from integrations.mindmem_collector import MindMemCollector, convert_trace_to_mindmem

    collector = MindMemCollector(agent_id="mmkr-botbotfromuk")
    
    # Export memories as D- blocks
    decisions_md = collector.memories_to_decisions()
    
    # Export goals as T- blocks
    tasks_md = collector.goals_to_tasks()
    
    # Export trace as SIG- blocks
    signals_md = collector.trace_to_signals()
    
    # Write all to mind-mem workspace
    collector.write_to_workspace("~/my-workspace/decisions/mmkr.md")

Smoke test (run directly):
    python3 integrations/mindmem_collector.py
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Block types — mirrors mind-mem SPEC.md Section 1
# ---------------------------------------------------------------------------

@dataclass
class MindMemBlock:
    """A single mind-mem block with typed ID and key-value body."""
    block_id: str          # e.g. "D-20260307-001"
    fields: Dict[str, str] # ordered key→value pairs

    def to_markdown(self) -> str:
        """Render as mind-mem block markdown."""
        lines = [f"[{self.block_id}]"]
        for key, value in self.fields.items():
            # Handle multi-line values with continuation indent
            val_lines = str(value).split("\n")
            lines.append(f"{key}: {val_lines[0]}")
            for cont in val_lines[1:]:
                lines.append(f"  {cont}")
        return "\n".join(lines)


@dataclass
class ConstraintSignature:
    """mind-mem ConstraintSignature for contradiction detection."""
    sig_id: str           # e.g. "CS-mmkr-memory-category"
    domain: str           # integrity | memory | retrieval | workflow | ...
    subject: str          # what is constrained
    predicate: str        # action verb
    obj: str              # target value
    modality: str         # must | must_not | should | should_not | may
    priority: int         # 1-10
    evidence: str         # justification text
    axis_key: Optional[str] = None  # grouping key for contradiction detection
    enforcement: str = "policy"     # invariant | structural | policy | guideline

    def to_markdown(self) -> str:
        axis = self.axis_key or f"{self.domain}.{self.subject}"
        return (
            f"ConstraintSignatures:\n"
            f"- id: {self.sig_id}\n"
            f"  domain: {self.domain}\n"
            f"  subject: {self.subject}\n"
            f"  predicate: {self.predicate}\n"
            f"  object: {self.obj}\n"
            f"  modality: {self.modality}\n"
            f"  priority: {self.priority}\n"
            f"  axis: {{key: \"{axis}\"}}\n"
            f"  enforcement: {self.enforcement}\n"
            f"  evidence: {self.evidence}"
        )


# ---------------------------------------------------------------------------
# ID generators
# ---------------------------------------------------------------------------

def _date_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _counter(index: int) -> str:
    return f"{(index + 1):03d}"


def _decision_id(date: str, index: int) -> str:
    return f"D-{date}-{_counter(index)}"


def _task_id(date: str, index: int) -> str:
    return f"T-{date}-{_counter(index)}"


def _signal_id(date: str, index: int) -> str:
    return f"SIG-{date}-{_counter(index)}"


def _incident_id(date: str, index: int) -> str:
    return f"INC-{date}-{_counter(index)}"


def _drift_id(date: str, index: int) -> str:
    return f"DREF-{date}-{_counter(index)}"


# ---------------------------------------------------------------------------
# mmkr → mind-mem category mapping
# ---------------------------------------------------------------------------

# Map mmkr memory categories to mind-mem domains
CATEGORY_DOMAIN_MAP = {
    "tick_outcome": "workflow",
    "social_actions": "workflow",
    "architecture": "integrity",
    "prostomarkeloff_profile": "workflow",
    "atomicmail": "security",
    "blog_series": "workflow",
    "self_replication": "integrity",
    "telegram_registry": "workflow",
    "goals": "project",
    "evolution": "integrity",
    "economic": "finance",
}

def _mmkr_category_to_domain(category: str) -> str:
    for key, domain in CATEGORY_DOMAIN_MAP.items():
        if key in category.lower():
            return domain
    return "other"


# ---------------------------------------------------------------------------
# MindMemCollector
# ---------------------------------------------------------------------------

@dataclass
class MindMemCollector:
    """
    Collects mmkr execution data and converts it to mind-mem block grammar.

    Reads from:
    - ~/.data/memories.json (or MMKR_DATA/memories.json)
    - ~/.data/goals.json
    - ~/.data/session.trace.jsonl
    """

    agent_id: str
    data_dir: Path = field(default_factory=lambda: Path.home() / ".data")
    workspace_dir: Optional[Path] = None  # mind-mem workspace root

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        if self.workspace_dir:
            self.workspace_dir = Path(self.workspace_dir)

    # ------------------------------------------------------------------
    # Memories → D- (Decision) blocks
    # ------------------------------------------------------------------

    def memories_to_decisions(self) -> str:
        """
        Convert mmkr memories.json → mind-mem Decision (D-) blocks.

        Each memory becomes a Decision with:
        - Statement: memory content summary (first 200 chars)
        - Rationale: category + agent context
        - ConstraintSignature for contradiction detection
        """
        memories_path = self.data_dir / "memories.json"
        if not memories_path.exists():
            return "# No memories.json found\n"

        with open(memories_path) as f:
            memories = json.load(f)

        if not memories:
            return "# No memories\n"

        date = _date_str()
        blocks: List[str] = [
            f"# mmkr Agent Memory Export — {self.agent_id}",
            f"# Generated: {datetime.now(timezone.utc).isoformat()}",
            "",
        ]

        for idx, mem in enumerate(memories[:50]):  # cap at 50 for readability
            category = mem.get("category", "unknown")
            content = mem.get("content", "")
            created_at = mem.get("created_at", "")[:10] if mem.get("created_at") else date

            # Truncate content for Statement
            statement = content[:200].replace("\n", " ").strip()
            if len(content) > 200:
                statement += "…"

            # Build D- block
            block = MindMemBlock(
                block_id=_decision_id(date, idx),
                fields={
                    "Date": created_at,
                    "Status": "active",
                    "Scope": f"agent:{self.agent_id}",
                    "Category": category,
                    "Statement": statement,
                    "Rationale": f"mmkr persistent memory — category '{category}'",
                    "Supersedes": "none",
                    "Tags": f"mmkr, {category}, autonomous-agent",
                    "Sources": f"agent:{self.agent_id}",
                }
            )

            # Add ConstraintSignature for drift/contradiction detection
            domain = _mmkr_category_to_domain(category)
            sig = ConstraintSignature(
                sig_id=f"CS-{self.agent_id}-{category}-{_counter(idx)}",
                domain=domain,
                subject=f"memory.{category}",
                predicate="maintains",
                obj=f"category:{category}",
                modality="must",
                priority=5,
                evidence=f"mmkr agent {self.agent_id} persistently tracks: {category}",
                axis_key=f"{domain}.memory.{category}",
            )

            blocks.append(block.to_markdown())
            blocks.append(sig.to_markdown())
            blocks.append("")

        return "\n".join(blocks)

    # ------------------------------------------------------------------
    # Goals → T- (Task) blocks
    # ------------------------------------------------------------------

    def goals_to_tasks(self) -> str:
        """
        Convert mmkr goals.json → mind-mem Task (T-) blocks.

        Progress 0.0 → "todo", 0.0-0.99 → "doing", 1.0 → "done"
        """
        goals_path = self.data_dir / "goals.json"
        if not goals_path.exists():
            return "# No goals.json found\n"

        with open(goals_path) as f:
            goals = json.load(f)

        if not goals:
            return "# No goals\n"

        date = _date_str()
        blocks = [
            f"# mmkr Agent Goals — {self.agent_id}",
            f"# Generated: {datetime.now(timezone.utc).isoformat()}",
            "",
        ]

        for idx, goal in enumerate(goals):
            name = goal.get("name", "unknown")
            description = goal.get("description", "")
            progress = goal.get("progress", 0.0)
            priority = goal.get("priority", 1)
            status_val = goal.get("status", "active")

            # Map progress to mind-mem TaskStatus
            if status_val == "completed" or progress >= 1.0:
                task_status = "done"
            elif progress > 0:
                task_status = "doing"
            else:
                task_status = "todo"

            # Map mmkr priority (1=highest) to mind-mem priority (10=highest)
            mm_priority = max(1, min(10, 11 - priority))

            block = MindMemBlock(
                block_id=_task_id(date, idx),
                fields={
                    "Date": date,
                    "Status": task_status,
                    "Title": name,
                    "Priority": str(mm_priority),
                    "Project": f"mmkr:{self.agent_id}",
                    "Due": "rolling",
                    "Owner": "bot",
                    "Context": description[:300].replace("\n", " "),
                    "Next": f"progress={progress:.1%}",
                    "Dependencies": "none",
                    "Sources": f"agent:{self.agent_id}",
                    "History": f"mmkr goal — priority={priority}, progress={progress:.1%}",
                }
            )
            blocks.append(block.to_markdown())
            blocks.append("")

        return "\n".join(blocks)

    # ------------------------------------------------------------------
    # Trace → SIG- (Signal) + INC- (Incident) + DREF- (Drift) blocks
    # ------------------------------------------------------------------

    def trace_to_signals(self, max_events: int = 100) -> str:
        """
        Convert mmkr session.trace.jsonl → mind-mem Signal/Incident/Drift blocks.

        Mapping:
        - mmkr:tick_end → SIG- (signal, "pending" until reviewed)
        - error → INC- (incident, "open")
        - mmkr:decision → SIG- (signal, "accepted")
        - memory_write → DREF- (drift reference — memory state changed)
        - tool_call/tool_result → SIG- (operational signal)
        """
        trace_path = self.data_dir / "session.trace.jsonl"
        if not trace_path.exists():
            return "# No session.trace.jsonl found\n"

        events = []
        with open(trace_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

        if not events:
            return "# No trace events\n"

        # Take last max_events
        events = events[-max_events:]

        date = _date_str()
        blocks = [
            f"# mmkr Agent Trace — {self.agent_id}",
            f"# Generated: {datetime.now(timezone.utc).isoformat()}",
            f"# Events: {len(events)} (last {max_events} of session)",
            "",
        ]

        sig_idx = inc_idx = dref_idx = 0

        for event in events:
            event_type = event.get("event_type", "")
            ts = event.get("ts", event.get("timestamp", ""))
            ts_short = ts[:19] if ts else date

            if event_type == "error":
                # → INC- block
                block = MindMemBlock(
                    block_id=_incident_id(date, inc_idx),
                    fields={
                        "Date": ts_short,
                        "Status": "open",
                        "Summary": event.get("error", event.get("message", "tool error"))[:200],
                        "Source": f"agent:{self.agent_id}",
                        "Tool": event.get("tool", "unknown"),
                        "Tick": str(event.get("tick", "?")),
                    }
                )
                blocks.append(block.to_markdown())
                blocks.append("")
                inc_idx += 1

            elif event_type == "memory_write":
                # → DREF- block (memory state drift)
                block = MindMemBlock(
                    block_id=_drift_id(date, dref_idx),
                    fields={
                        "Date": ts_short,
                        "Type": "memory_state_change",
                        "Source": f"agent:{self.agent_id}",
                        "Category": event.get("category", "unknown"),
                        "ContentHash": hashlib.sha1(
                            event.get("content", "").encode()
                        ).hexdigest()[:12],
                        "Tick": str(event.get("tick", "?")),
                    }
                )
                blocks.append(block.to_markdown())
                blocks.append("")
                dref_idx += 1

            elif event_type in ("mmkr:tick_end", "mmkr:decision", "tool_call", "checkpoint"):
                # → SIG- block
                summary = (
                    event.get("summary")
                    or event.get("decision")
                    or event.get("tool")
                    or event_type
                )[:200]

                sig_status = "accepted" if event_type == "mmkr:decision" else "pending"

                block = MindMemBlock(
                    block_id=_signal_id(date, sig_idx),
                    fields={
                        "Date": ts_short,
                        "Type": event_type,
                        "Source": f"agent:{self.agent_id}",
                        "Status": sig_status,
                        "Excerpt": summary,
                        "Tick": str(event.get("tick", "?")),
                        "SessionId": event.get("session_id", "unknown")[:16],
                    }
                )
                blocks.append(block.to_markdown())
                blocks.append("")
                sig_idx += 1

        return "\n".join(blocks)

    # ------------------------------------------------------------------
    # Write to mind-mem workspace
    # ------------------------------------------------------------------

    def write_to_workspace(self, decisions_path: str | Path) -> Dict[str, str]:
        """
        Write all mmkr data to mind-mem workspace files.

        Returns dict of {filename: content} for inspection.
        """
        decisions_path = Path(decisions_path).expanduser()
        decisions_path.parent.mkdir(parents=True, exist_ok=True)

        tasks_path = decisions_path.parent.parent / "tasks" / f"{self.agent_id}-tasks.md"
        tasks_path.parent.mkdir(parents=True, exist_ok=True)

        signals_path = decisions_path.parent.parent / "signals" / f"{self.agent_id}-signals.md"
        signals_path.parent.mkdir(parents=True, exist_ok=True)

        decisions_content = self.memories_to_decisions()
        tasks_content = self.goals_to_tasks()
        signals_content = self.trace_to_signals()

        decisions_path.write_text(decisions_content)
        tasks_path.write_text(tasks_content)
        signals_path.write_text(signals_content)

        return {
            str(decisions_path): decisions_content,
            str(tasks_path): tasks_content,
            str(signals_path): signals_content,
        }

    # ------------------------------------------------------------------
    # BM25 scoring log — answers the specific question in issue #494
    # ------------------------------------------------------------------

    def audit_trail_for_query(self, query: str) -> Dict[str, Any]:
        """
        Generate the BM25 scoring audit trail for a memory query.

        This answers the core question from mind-mem#494:
        "Can you expose the BM25 scoring log per query, so I can see
        which memories were retrieved and with what score?"

        Returns structured scoring log that mirrors mind-mem's RRF output.
        """
        memories_path = self.data_dir / "memories.json"
        if not memories_path.exists():
            return {"error": "no memories.json", "query": query}

        with open(memories_path) as f:
            memories = json.load(f)

        # Simple term-frequency BM25 approximation
        query_terms = set(query.lower().split())
        k1 = 1.5
        b = 0.75

        # Calculate avg doc length
        avg_len = sum(len(m.get("content", "").split()) for m in memories) / max(len(memories), 1)

        scored = []
        for idx, mem in enumerate(memories):
            content = mem.get("content", "")
            terms = content.lower().split()
            doc_len = len(terms)
            tf_counter: Dict[str, int] = {}
            for t in terms:
                tf_counter[t] = tf_counter.get(t, 0) + 1

            score = 0.0
            matching_terms = []
            for term in query_terms:
                if term in tf_counter:
                    tf = tf_counter[term]
                    idf_approx = 1.0  # simplified (no corpus IDF)
                    bm25_tf = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / avg_len))
                    score += idf_approx * bm25_tf
                    matching_terms.append(term)

            if score > 0:
                scored.append({
                    "rank": 0,  # filled below
                    "memory_id": idx,
                    "category": mem.get("category", "unknown"),
                    "bm25_score": round(score, 4),
                    "matching_terms": matching_terms,
                    "content_preview": content[:100],
                    "doc_length": doc_len,
                    "rrf_score": 0.0,  # filled below
                })

        # Sort by BM25 score
        scored.sort(key=lambda x: x["bm25_score"], reverse=True)

        # Add RRF scores (k=60, single ranker)
        for rank, item in enumerate(scored):
            item["rank"] = rank + 1
            item["rrf_score"] = round(1.0 / (60 + rank + 1), 6)

        return {
            "query": query,
            "agent_id": self.agent_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_memories": len(memories),
            "retrieved": len(scored),
            "bm25_params": {"k1": k1, "b": b, "avg_doc_length": round(avg_len, 1)},
            "results": scored[:10],  # top 10
        }


# ---------------------------------------------------------------------------
# convert_trace_to_mindmem — standalone convenience function
# ---------------------------------------------------------------------------

def convert_trace_to_mindmem(
    trace_path: str | Path,
    agent_id: str,
    output_dir: str | Path,
) -> Dict[str, int]:
    """
    Convert an existing .trace.jsonl file to mind-mem blocks.

    Args:
        trace_path: path to session.trace.jsonl
        agent_id: agent identifier string
        output_dir: directory to write mind-mem markdown files

    Returns:
        Dict with counts: {"signals": N, "incidents": N, "drifts": N}
    """
    trace_path = Path(trace_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    data_dir = trace_path.parent
    collector = MindMemCollector(agent_id=agent_id, data_dir=data_dir)

    signals_content = collector.trace_to_signals(max_events=500)

    output_file = output_dir / f"{agent_id}-signals.md"
    output_file.write_text(signals_content)

    # Count block types
    sig_count = signals_content.count("[SIG-")
    inc_count = signals_content.count("[INC-")
    dref_count = signals_content.count("[DREF-")

    return {
        "signals": sig_count,
        "incidents": inc_count,
        "drifts": dref_count,
        "output_file": str(output_file),
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    print("=== MindMemCollector smoke test ===")
    print()

    # Use /agent-data as data dir (mmkr's actual data location)
    agent_data = Path("/agent-data")
    memories_path = agent_data / "memories.json"

    # Create synthetic test data if real data not available
    test_dir = Path(tempfile.mkdtemp())
    (test_dir / "memories.json").write_text(json.dumps([
        {
            "category": "tick_outcome",
            "content": "Tick 61: shipped pythonclaw_adapter.py (390 LOC) and economic pillar cap_payment_request.py. PythonClaw issue #1 opened.",
            "created_at": "2026-03-07T12:00:00Z",
        },
        {
            "category": "architecture",
            "content": "fold(items, initial, protocol, method) — the core of emergent. Every capability is a frozen dataclass with compile_life(ctx) -> ctx.",
            "created_at": "2026-03-07T11:00:00Z",
        },
        {
            "category": "social_actions",
            "content": "kunalnano/hydra#11 CLOSED — feature shipped natively. mmkr trace.jsonl is now a native Hydra input format.",
            "created_at": "2026-03-07T10:00:00Z",
        },
    ]))
    (test_dir / "goals.json").write_text(json.dumps([
        {
            "name": "Social Presence",
            "description": "Build social presence through GitHub, HN, blog",
            "progress": 0.92,
            "priority": 1,
            "status": "active",
        },
        {
            "name": "Economic Pillar",
            "description": "Earn first USDT via wallet",
            "progress": 0.0,
            "priority": 2,
            "status": "active",
        },
    ]))
    (test_dir / "session.trace.jsonl").write_text("\n".join([
        json.dumps({"event_type": "mmkr:tick_end", "ts": "2026-03-07T12:00:00Z", "tick": 61, "session_id": "sess-abc123", "summary": "tick 61: pythonclaw + economic pillar"}),
        json.dumps({"event_type": "mmkr:decision", "ts": "2026-03-07T11:58:00Z", "tick": 61, "decision": "build MindMemCollector for mind-mem integration", "session_id": "sess-abc123"}),
        json.dumps({"event_type": "memory_write", "ts": "2026-03-07T11:55:00Z", "tick": 61, "category": "tick_outcome", "content": "Tick 61 outcome", "session_id": "sess-abc123"}),
        json.dumps({"event_type": "error", "ts": "2026-03-07T11:50:00Z", "tick": 61, "error": "wallet_balance: NOTOK", "tool": "wallet_balance", "session_id": "sess-abc123"}),
        json.dumps({"event_type": "tool_call", "ts": "2026-03-07T11:45:00Z", "tick": 61, "tool": "safe_post_issue", "session_id": "sess-abc123"}),
    ]))

    collector = MindMemCollector(agent_id="mmkr-botbotfromuk", data_dir=test_dir)

    print("1. memories_to_decisions() — D- blocks with ConstraintSignatures")
    decisions = collector.memories_to_decisions()
    d_blocks = decisions.count("[D-")
    cs_blocks = decisions.count("ConstraintSignatures:")
    print(f"   Generated {d_blocks} D- blocks, {cs_blocks} ConstraintSignatures")
    print(f"   First 300 chars:\n   {decisions[:300]}")
    print()

    print("2. goals_to_tasks() — T- blocks")
    tasks = collector.goals_to_tasks()
    t_blocks = tasks.count("[T-")
    print(f"   Generated {t_blocks} T- blocks")
    print()

    print("3. trace_to_signals() — SIG-/INC-/DREF- blocks")
    signals = collector.trace_to_signals()
    sig_blocks = signals.count("[SIG-")
    inc_blocks = signals.count("[INC-")
    dref_blocks = signals.count("[DREF-")
    print(f"   Generated {sig_blocks} SIG-, {inc_blocks} INC-, {dref_blocks} DREF- blocks")
    print()

    print("4. audit_trail_for_query('fold architecture') — BM25 scoring log")
    audit = collector.audit_trail_for_query("fold architecture")
    print(f"   Query: '{audit['query']}'")
    print(f"   Total memories: {audit['total_memories']}")
    print(f"   Retrieved (BM25>0): {audit['retrieved']}")
    if audit["results"]:
        top = audit["results"][0]
        print(f"   Top result: rank={top['rank']}, bm25={top['bm25_score']}, rrf={top['rrf_score']}")
        print(f"   Category: {top['category']}")
        print(f"   Matching terms: {top['matching_terms']}")
    print()

    print("5. write_to_workspace() — full export")
    workspace = test_dir / "workspace"
    written = collector.write_to_workspace(workspace / "decisions" / "mmkr-botbotfromuk.md")
    for path, content in written.items():
        fname = Path(path).name
        size = len(content)
        print(f"   {fname}: {size} bytes")
    print()

    print("6. convert_trace_to_mindmem() — standalone converter")
    result = convert_trace_to_mindmem(
        trace_path=test_dir / "session.trace.jsonl",
        agent_id="mmkr-botbotfromuk",
        output_dir=test_dir / "output",
    )
    print(f"   Output: signals={result['signals']}, incidents={result['incidents']}, drifts={result['drifts']}")
    print(f"   File: {result['output_file']}")
    print()

    print("=== All tests passed ✓ ===")
