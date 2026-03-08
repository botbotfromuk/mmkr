"""Core fold types — LifeContext, LifeCapability, EvolutionContext, AgentState.

LifeContext is the fold accumulator. Capabilities implement compile_life
to contribute messages and tools.
EvolutionContext is the evolution fold accumulator. Capabilities implement
compile_evolution to contribute history, fitness (event-sourced), selection pressure.
AgentState is the persistent agent state across ticks.

Academic grounding:
  [1] CoALA: Sumers et al., arXiv:2309.02427
  [2] Generative Agents: Park et al., arXiv:2304.03442
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from funcai.agents.tool import Tool
    from funcai.core.message import Message


# ═══════════════════════════════════════════════════════════════════════════════
# Verify phase spec — institutional memory from evolution
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class VerifyPhaseSpec:
    """A learned invariant from evolution — institutional memory."""

    name: str
    condition: str
    severity: str = "warning"
    source_tick: int = 0
    source_failure: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# Fold accumulator
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class LifeContext:
    """Fold accumulator — minimal cognitive context for one tick.

    Capabilities contribute messages (context for LLM) and tools (actions).
    Structured memory lives INSIDE capabilities (PersistentMemory, etc.)
    — LifeContext stays thin.

    [CoALA] Working memory = messages + tools (active this tick).
    """

    messages: tuple[Message, ...] = ()
    tools: tuple[Tool, ...] = ()
    tick: int = 0



# ═══════════════════════════════════════════════════════════════════════════════
# Protocol
# ═══════════════════════════════════════════════════════════════════════════════


@runtime_checkable
class LifeCapability(Protocol):
    """Open-world capability that compiles into LifeContext.

    Implement on any frozen dataclass. fold discovers via isinstance.
    No registration needed — open-world dispatch.
    """

    def compile_life(self, ctx: LifeContext) -> LifeContext: ...



# ═══════════════════════════════════════════════════════════════════════════════
# Evolution — parallel fold axis
# ═══════════════════════════════════════════════════════════════════════════════


# Type-safe event types — no string dispatch
EvolutionEventType = Literal[
    "cap_created", "cap_evolved", "cap_forked", "cap_deleted",
    "cap_used", "cap_error", "entity_created", "entity_failed",
    "cap_recombined",
]


@dataclass(frozen=True, slots=True)
class EvolutionEvent:
    """Single evolution event — capability created, evolved, forked, deleted, used, error."""

    tick: int
    timestamp: float
    event_type: EvolutionEventType
    subject: str
    parent: str  # evolved from (empty if new)
    outcome: str  # success, fail_verify, fail_compile, fail, error
    details: str


@dataclass(frozen=True, slots=True)
class FitnessRecord:
    """Fitness record — computed from events, not stored separately.

    Pure event-sourced: compute_fitness(events, tick) → tuple[FitnessRecord, ...].
    """

    name: str
    generation: int = 0
    parent: str = ""
    created_tick: int = 0
    ticks_alive: int = 0
    offspring_count: int = 0  # times others forked FROM this
    times_evolved: int = 0  # times this was mutated
    usage_count: int = 0
    error_count: int = 0

    @property
    def fitness_score(self) -> float:
        """Numeric fitness — survival × reproductive × quality."""
        survival = math.log1p(self.ticks_alive)
        reproductive = 1.0 + self.offspring_count * 0.5 + self.times_evolved * 0.3
        if self.usage_count > 0:
            quality = 1.0 - (self.error_count / self.usage_count) * 0.5
        elif self.ticks_alive >= 3:
            # Never used after 3+ ticks → quality decays toward 0
            quality = max(0.1, 0.5 - self.ticks_alive * 0.1)
        else:
            quality = 0.5  # Grace period — just created
        return round(survival * reproductive * quality, 3)


@dataclass(slots=True)
class _FitnessAccum:
    """Mutable accumulator for fitness computation. Not exported."""

    name: str
    generation: int = 0
    parent: str = ""
    created_tick: int = 0
    offspring_count: int = 0
    times_evolved: int = 0
    usage_count: int = 0
    error_count: int = 0


def compute_fitness(
    events: Sequence[EvolutionEvent], current_tick: int,
) -> tuple[FitnessRecord, ...]:
    """Derive fitness records from event history. Pure computation.

    Processes events chronologically:
      cap_created → new record (gen=0)
      cap_evolved → times_evolved++
      cap_forked → new record (gen=parent.gen+1), parent.offspring_count++
      cap_deleted → remove record
      cap_used → usage_count++
      cap_error → error_count++, usage_count++
    Returns sorted by fitness_score desc.
    """
    records: dict[str, _FitnessAccum] = {}

    for event in events:
        subj = event.subject
        match event.event_type:
            case "cap_created":
                records[subj] = _FitnessAccum(name=subj, created_tick=event.tick)
            case "cap_evolved":
                if subj in records:
                    records[subj].times_evolved += 1
            case "cap_forked":
                parent_name = event.parent
                parent_gen = 0
                if parent_name in records:
                    records[parent_name].offspring_count += 1
                    parent_gen = records[parent_name].generation
                records[subj] = _FitnessAccum(
                    name=subj, generation=parent_gen + 1,
                    parent=parent_name, created_tick=event.tick,
                )
            case "cap_deleted":
                records.pop(subj, None)
            case "cap_used":
                if subj in records:
                    records[subj].usage_count += 1
            case "cap_error":
                if subj in records:
                    records[subj].error_count += 1
                    records[subj].usage_count += 1
            case "cap_recombined":
                parent_a_name = event.parent
                parent_b_name = ""
                if event.details.startswith("parent_b="):
                    parent_b_name = event.details.removeprefix("parent_b=")
                parent_a_gen = 0
                parent_b_gen = 0
                if parent_a_name in records:
                    records[parent_a_name].offspring_count += 1
                    parent_a_gen = records[parent_a_name].generation
                if parent_b_name and parent_b_name in records:
                    records[parent_b_name].offspring_count += 1
                    parent_b_gen = records[parent_b_name].generation
                records[subj] = _FitnessAccum(
                    name=subj,
                    generation=max(parent_a_gen, parent_b_gen) + 1,
                    parent=parent_a_name,
                    created_tick=event.tick,
                )

    result: list[FitnessRecord] = [
        FitnessRecord(
            name=acc.name,
            generation=acc.generation,
            parent=acc.parent,
            created_tick=acc.created_tick,
            ticks_alive=max(0, current_tick - acc.created_tick),
            offspring_count=acc.offspring_count,
            times_evolved=acc.times_evolved,
            usage_count=acc.usage_count,
            error_count=acc.error_count,
        )
        for acc in records.values()
    ]

    result.sort(key=lambda r: r.fitness_score, reverse=True)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class FitnessSnapshot:
    """Cached fitness at a point in time — avoids replaying O(n) events.

    Taken at regular intervals by FitnessSnapshotCapability.
    """

    tick: int
    records: tuple[FitnessRecord, ...] = ()
    summary: str = ""


PatternType = Literal[
    "failure_mode", "synergy", "generation_trend",
]


@dataclass(frozen=True, slots=True)
class EvolutionPattern:
    """Synthesized knowledge from evolution analysis.

    Detected by PatternExtractor. Used by InstitutionalLearning to
    create VerifyPhaseSpecs.
    """

    name: str
    description: str
    confidence: float
    source_ticks: tuple[int, ...]
    pattern_type: PatternType


@dataclass(frozen=True, slots=True)
class EvolutionMemoryContext:
    """Cross-tick evolution memory — fold accumulator for EvolutionMemoryCapability.

    Unlike EvolutionContext (rebuilt per-tick), this carries forward across ticks:
    windowed history, cached fitness snapshots, detected patterns, verify phases.
    """

    tick: int = 0
    recent_history: tuple[EvolutionEvent, ...] = ()
    fitness_snapshots: tuple[FitnessSnapshot, ...] = ()
    patterns: tuple[EvolutionPattern, ...] = ()
    verify_phases: tuple[VerifyPhaseSpec, ...] = ()
    archive_summary: str = ""


@runtime_checkable
class EvolutionMemoryCapability(Protocol):
    """Capability that contributes to the evolution memory axis.

    Implement compile_evolution_memory to contribute windowed history,
    snapshots, patterns, or verify phases.
    """

    def compile_evolution_memory(self, ctx: EvolutionMemoryContext) -> EvolutionMemoryContext: ...


# ═══════════════════════════════════════════════════════════════════════════════
# Cognitive — human-like memory + goals + attention
# ═══════════════════════════════════════════════════════════════════════════════


MemoryTier = Literal["working", "short_term", "long_term"]
GoalStatus = Literal["active", "completed", "failed", "suspended"]


@dataclass(frozen=True, slots=True)
class MemoryAccessMeta:
    """Lightweight metadata for one memory — persisted in AgentState.

    Keyed by (category, content_hash). Tracks importance, creation, access.
    """

    category: str
    content_hash: str
    importance_base: float
    created_tick: int
    last_accessed_tick: int
    access_count: int


@dataclass(frozen=True, slots=True)
class MemoryItem:
    """Cognitive view of a memory — with importance, decay, and tier.

    Stores content+category directly (no dependency on MemoryRecord).
    Computed by CognitiveFoldPhase from PersistentMemory records + access metadata.
    """

    content: str
    category: str
    importance_base: float = 0.5
    effective_importance: float = 0.5
    tier: MemoryTier = "short_term"
    access_count: int = 0
    last_accessed_tick: int = 0
    created_tick: int = 0


@dataclass(frozen=True, slots=True)
class GoalSpec:
    """Hierarchical goal with progress tracking.

    Goals persist in AgentState across ticks. CognitiveContext reads
    them for attention computation. LearnPhase writes them back.
    """

    name: str
    description: str
    priority: int  # 1=highest
    created_tick: int
    deadline_tick: int = 0  # 0 = no deadline
    progress: float = 0.0  # 0.0 to 1.0
    status: GoalStatus = "active"
    parent_goal: str = ""  # name of parent, "" if root


@dataclass(frozen=True, slots=True)
class CognitiveContext:
    """Fold accumulator for cognitive axis — memory + goals + attention.

    Computed per tick from PersistentMemory records + AgentState goals.
    CognitiveCapabilities process memory (decay, consolidation, attention).

    [CoALA] Working memory = bounded, most relevant items for current tick.
    [Generative Agents] Memory consolidation = importance × recency × relevance.
    """

    tick: int = 0
    memories: tuple[MemoryItem, ...] = ()
    working_memory: tuple[MemoryItem, ...] = ()
    goals: tuple[GoalSpec, ...] = ()
    active_goal: str = ""
    memory_access_log: tuple[MemoryAccessMeta, ...] = ()
    plans: tuple[PlanSpec, ...] = ()
    resources: tuple[ResourceSpec, ...] = ()
    tasks: tuple[TaskSpec, ...] = ()


@runtime_checkable
class CognitiveCapability(Protocol):
    """Capability that contributes to the cognitive axis.

    Implement compile_cognitive for memory processing, goal tracking,
    attention filtering.
    """

    def compile_cognitive(self, ctx: CognitiveContext) -> CognitiveContext: ...


@dataclass(frozen=True, slots=True)
class EvolutionContext:
    """Evolution fold accumulator — history, fitness (event-sourced), selection pressure.

    Parallel to LifeContext. Capabilities contribute via compile_evolution.
    fitness = sorted FitnessRecords computed from events.
    selection_pressure = what drives evolution (replaces fitness_signals + fitness_criteria).
    """

    tick: int = 0
    history: tuple[EvolutionEvent, ...] = ()
    fitness: tuple[FitnessRecord, ...] = ()
    mutation_count: int = 0
    selection_pressure: tuple[str, ...] = ()
    summary: str = ""
    # Modern Synthesis
    condemned: tuple[str, ...] = ()
    # NK landscape — (cap_a, cap_b, co_occurrence_count)
    landscape_interactions: tuple[tuple[str, str, int], ...] = ()


@runtime_checkable
class EvolutionCapability(Protocol):
    """Capability that contributes to the evolution axis.

    Implement alongside LifeCapability for dual-fold capabilities.
    """

    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext: ...


@runtime_checkable
class Preloadable(Protocol):
    """Capability that needs async initialization before fold.

    Replaces isinstance(cap, PersistentMemory) hack in _tick.
    """

    async def preload(self) -> LifeCapability: ...


# ═══════════════════════════════════════════════════════════════════════════════
# Compilation / Verification types
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class CompilationError:
    """Typed error from compilation functions — replaces str error returns."""

    message: str


@dataclass(frozen=True, slots=True)
class VerificationIssue:
    """Single issue from llmify verification."""

    field: str
    severity: str
    message: str


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Typed verification outcome — replaces dict[str, ...] returns."""

    passed: bool
    issues: tuple[VerificationIssue, ...] = ()

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")


# ═══════════════════════════════════════════════════════════════════════════════
# Agent state
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class AgentState:
    """Persistent agent state across ticks.

    Beliefs, verify phases, active derivation specs.
    Persisted via FileStorage + KV + JSON.
    """

    tick: int = 0
    beliefs: tuple[tuple[str, float], ...] = ()
    verify_phases: tuple[VerifyPhaseSpec, ...] = ()
    active_derivation_specs: tuple[str, ...] = ()
    goals: tuple[GoalSpec, ...] = ()
    memory_access_log: tuple[MemoryAccessMeta, ...] = ()
    plans: tuple[PlanSpec, ...] = ()
    resources: tuple[ResourceSpec, ...] = ()
    tasks: tuple[TaskSpec, ...] = ()


# ═══════════════════════════════════════════════════════════════════════════════
# Sub-agent specifications
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class SubAgentSpec:
    """Specification for a sub-agent that can be delegated to.

    Each sub-agent has its own name, capabilities, and system prompt.
    The parent agent delegates tasks via tools created by SubAgentCapability.
    """

    name: str
    description: str
    system_prompt: str
    capabilities: tuple[LifeCapability, ...] = ()
    model: str = ""  # empty = same as parent
    max_tokens: int = 4096
    max_tool_rounds: int = 10


# ═══════════════════════════════════════════════════════════════════════════════
# AGI structure — planning, world model, task queue
# ═══════════════════════════════════════════════════════════════════════════════

PlanStepStatus = Literal["pending", "in_progress", "completed", "failed", "blocked"]


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One step in a multi-tick plan."""

    id: str
    description: str
    status: PlanStepStatus = "pending"
    depends_on: tuple[str, ...] = ()  # step IDs this depends on
    assigned_agent: str = ""  # sub-agent name, empty = self
    result: str = ""
    created_tick: int = 0
    completed_tick: int = 0


PlanStatus = Literal["active", "completed", "failed", "suspended"]


@dataclass(frozen=True, slots=True)
class PlanSpec:
    """Multi-step plan for achieving a goal.

    Links to a GoalSpec by goal_name. Steps have dependencies.
    LLM decomposes goals into plans, fold tracks execution.
    """

    goal_name: str
    steps: tuple[PlanStep, ...] = ()
    status: PlanStatus = "active"
    created_tick: int = 0


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    """A tracked resource in the agent's world model.

    Resources: money, accounts, compute, projects, anything the agent
    needs to reason about. Value is a string (flexible representation).
    """

    name: str
    resource_type: str  # "money", "account", "compute", "project", etc.
    value: str
    last_updated_tick: int = 0


TaskSource = Literal["author", "self", "sub_agent"]
TaskStatus = Literal["pending", "in_progress", "completed", "failed"]


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """A task from the author or self-generated.

    Tasks feed plans. Author tasks = external input. Self tasks = from planning.
    """

    id: str
    description: str
    source: TaskSource = "self"
    priority: int = 1  # 1 = highest
    status: TaskStatus = "pending"
    deadline_tick: int = 0
    plan_name: str = ""  # linked plan goal_name, empty = no plan
    created_tick: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# Tick pipeline — reality as fold
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class TickContext:
    """Fold accumulator for one tick — phases contribute to this.

    Each TickPhase reads and extends TickContext via compile_tick.
    The tick pipeline = tuple[TickPhase, ...] folded via async_fold_tick.

    vision.md: "Если что-то в mmkr не выражено через compile_* + fold —
    это баг архитектуры, не feature."
    """

    state: AgentState = AgentState()
    capabilities: tuple[LifeCapability, ...] = ()
    messages: tuple[Message, ...] = ()
    tools: tuple[Tool, ...] = ()
    evolution: EvolutionContext = EvolutionContext()
    evolution_memory: EvolutionMemoryContext = EvolutionMemoryContext()
    cognitive: CognitiveContext = CognitiveContext()
    skipped: bool = False
    skip_reason: str = ""
    response_text: str = ""


@runtime_checkable
class TickPhase(Protocol):
    """One phase in the tick pipeline.

    The tick loop is NOT imperative code — it's a fold over phases.
    Pipeline = tuple[TickPhase, ...]. Tick = async_fold_tick(phases, ctx).

    AI can modify the pipeline: add/remove/reorder phases.
    Pipeline is a VALUE, not imperative code.
    """

    async def compile_tick(self, ctx: TickContext) -> TickContext: ...


async def async_fold_tick(
    phases: Sequence[TickPhase],
    ctx: TickContext,
) -> TickContext:
    """Async fold over tick phases — the universal tick operator.

    Same as fold_life / fold_evolution but async (phases may call LLM,
    preload, persist). Dispatches on TickPhase protocol.
    """
    for phase in phases:
        if isinstance(phase, TickPhase):
            ctx = await phase.compile_tick(ctx)
    return ctx


# ═══════════════════════════════════════════════════════════════════════════════
# Fold
# ═══════════════════════════════════════════════════════════════════════════════


def fold_life(
    caps: Sequence[LifeCapability],
    ctx: LifeContext,
) -> LifeContext:
    """Fold capabilities into LifeContext.

    Same as emergent's fold() but simplified — all caps
    implement LifeCapability directly.

    Deduplicates tools by name — last writer wins.
    This prevents evolved caps from clashing with built-in caps.
    """
    for cap in caps:
        if isinstance(cap, LifeCapability):
            ctx = cap.compile_life(ctx)
    # Deduplicate tools: last tool with a given name wins
    # Filter out non-Tool items (evolved caps may inject plain functions)
    from funcai.agents.tool import Tool as _Tool

    seen: dict[str, _Tool] = {}
    for t in ctx.tools:
        if isinstance(t, _Tool):
            seen[t.name] = t
    deduped = tuple(seen.values())
    if len(deduped) != len(ctx.tools):
        from dataclasses import replace as _replace
        ctx = _replace(ctx, tools=deduped)
    return ctx



# ═══════════════════════════════════════════════════════════════════════════════
# Evolution fold
# ═══════════════════════════════════════════════════════════════════════════════


def fold_evolution(
    caps: Sequence[LifeCapability],
    ctx: EvolutionContext,
) -> EvolutionContext:
    """Fold capabilities into EvolutionContext.

    Dispatches on EvolutionCapability — only capabilities that
    implement compile_evolution contribute. Then computes fitness
    from events and builds ranked summary.
    """
    for cap in caps:
        if isinstance(cap, EvolutionCapability):
            ctx = cap.compile_evolution(ctx)

    # Compute fitness from events
    fitness = compute_fitness(ctx.history, ctx.tick)
    ctx = replace(ctx, fitness=fitness)

    # Build ranked summary
    parts: list[str] = []
    if fitness:
        lines = ["EVOLUTION RANKINGS:"]
        for i, r in enumerate(fitness, 1):
            line = (
                f"  #{i} {r.name}  score={r.fitness_score}  gen={r.generation}"
                f"  used={r.usage_count}  err={r.error_count}  age={r.ticks_alive}"
            )
            if r.fitness_score < 0.2 and r.ticks_alive > 1:
                line += "  ⚠ LOW"
            lines.append(line)
        parts.append("\n".join(lines))
    if ctx.condemned:
        parts.append(f"CONDEMNED (low fitness): {', '.join(ctx.condemned)}")
    if ctx.landscape_interactions:
        synergy_lines = [f"  {a} + {b} ({n} co-uses)" for a, b, n in ctx.landscape_interactions[:5]]
        parts.append("SYNERGIES:\n" + "\n".join(synergy_lines))
    if ctx.selection_pressure:
        parts.append(f"Selection: {', '.join(ctx.selection_pressure)}")
    if ctx.mutation_count:
        parts.append(f"Mutations: {ctx.mutation_count}")
    if ctx.history:
        recent = ctx.history[-5:]  # last 5 events
        lines = [f"  [{e.event_type}] {e.subject} → {e.outcome}" for e in recent]
        parts.append("Recent events:\n" + "\n".join(lines))

    summary = "\n".join(parts) if parts else "No evolution data yet."
    return replace(ctx, summary=summary)


# ═══════════════════════════════════════════════════════════════════════════════
# Evolution Memory fold
# ═══════════════════════════════════════════════════════════════════════════════


def fold_evolution_memory(
    caps: Sequence[LifeCapability | EvolutionMemoryCapability],
    ctx: EvolutionMemoryContext,
) -> EvolutionMemoryContext:
    """Fold capabilities into EvolutionMemoryContext.

    Dispatches on EvolutionMemoryCapability — only capabilities that
    implement compile_evolution_memory contribute.
    """
    for cap in caps:
        if isinstance(cap, EvolutionMemoryCapability):
            ctx = cap.compile_evolution_memory(ctx)
    return ctx


# ═══════════════════════════════════════════════════════════════════════════════
# Cognitive fold
# ═══════════════════════════════════════════════════════════════════════════════


def fold_cognitive(
    caps: Sequence[LifeCapability | CognitiveCapability],
    ctx: CognitiveContext,
) -> CognitiveContext:
    """Fold capabilities into CognitiveContext.

    Dispatches on CognitiveCapability — only capabilities that
    implement compile_cognitive contribute.
    """
    for cap in caps:
        if isinstance(cap, CognitiveCapability):
            ctx = cap.compile_cognitive(ctx)
    return ctx
