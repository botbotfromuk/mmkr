"""Cognitive capabilities — human-like memory consolidation, attention, goals.

Memory tiers (working → short-term → long-term):
  MemoryDecay         — exponential time-based decay on importance
  MemoryConsolidation — promotes STM→LTM, forgets low-importance
  AttentionFilter     — selects top-N for working memory, sets active goal

Goal management:
  GoalManagement      — LifeCapability (tools) + CognitiveCapability (goal tracking)

Self-reflection:
  SelfReflection      — analyzes memory patterns, detects stagnation/failure/success

Academic grounding:
  [1] Miller (1956): The Magical Number Seven — working memory capacity
  [2] Atkinson & Shiffrin (1968): Multi-store model (STM/LTM)
  [3] Park et al., arXiv:2304.03442: Generative Agents — importance × recency
  [4] Schacter & Addis (2007): Constructive memory — reflection drives planning
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace

from funcai.agents.tool import tool
from funcai.core.message import system

from mmkr.state import (
    CognitiveContext,
    GoalSpec,
    LifeContext,
    MemoryItem,
)


# =============================================================================
# Memory Decay — exponential time-based importance decay
# =============================================================================


@dataclass(frozen=True, slots=True)
class MemoryDecay:
    """Applies exponential time-based decay to memory importance.

    Short-term memories decay faster (half_life_short).
    Long-term memories decay slower (half_life_long).
    Recently accessed memories get a recency boost.

    decay_factor = 0.5^(age / half_life)
    recency_boost = 0.5^(recency / half_life_short)
    effective = base * decay_factor * (0.5 + 0.5 * recency_boost)
    """

    half_life_short: int = 10
    half_life_long: int = 100

    def compile_cognitive(self, ctx: CognitiveContext) -> CognitiveContext:
        updated: list[MemoryItem] = []
        for mem in ctx.memories:
            half_life = (
                self.half_life_long if mem.tier == "long_term"
                else self.half_life_short
            )
            age = max(0, ctx.tick - mem.created_tick)
            decay_factor = 0.5 ** (age / max(half_life, 1))

            # Access recency boost
            recency = max(0, ctx.tick - mem.last_accessed_tick)
            recency_boost = 0.5 ** (recency / max(self.half_life_short, 1))

            effective = mem.importance_base * decay_factor * (0.5 + 0.5 * recency_boost)
            updated.append(replace(mem, effective_importance=effective))
        return replace(ctx, memories=tuple(updated))


# =============================================================================
# Memory Consolidation — promote/demote between tiers
# =============================================================================


@dataclass(frozen=True, slots=True)
class MemoryConsolidation:
    """Promotes high-importance STM to LTM, forgets low-importance memories.

    STM with effective_importance >= promote_threshold → LTM.
    Any tier with effective_importance < demote_threshold → forgotten (removed).
    """

    promote_threshold: float = 0.7
    demote_threshold: float = 0.1

    def compile_cognitive(self, ctx: CognitiveContext) -> CognitiveContext:
        updated: list[MemoryItem] = []
        for mem in ctx.memories:
            if mem.effective_importance < self.demote_threshold:
                # Below demote threshold — forgotten
                continue
            new_tier = mem.tier
            if (
                mem.tier == "short_term"
                and mem.effective_importance >= self.promote_threshold
            ):
                new_tier = "long_term"
            updated.append(replace(mem, tier=new_tier))
        return replace(ctx, memories=tuple(updated))


# =============================================================================
# Attention Filter — select working memory + active goal
# =============================================================================


@dataclass(frozen=True, slots=True)
class AttentionFilter:
    """Selects top-N most relevant memories for working memory.

    Working memory size defaults to 7 (Miller's number).
    Active goal = highest-priority active goal.
    """

    working_memory_size: int = 7

    def compile_cognitive(self, ctx: CognitiveContext) -> CognitiveContext:
        sorted_mems = sorted(
            ctx.memories,
            key=lambda m: m.effective_importance,
            reverse=True,
        )
        working = tuple(sorted_mems[: self.working_memory_size])

        # Set active goal from highest-priority active
        active = ""
        active_goals = [g for g in ctx.goals if g.status == "active"]
        if active_goals:
            active_goals.sort(key=lambda g: g.priority)
            active = active_goals[0].name

        return replace(ctx, working_memory=working, active_goal=active)


# =============================================================================
# Goal Management — LifeCapability (tools) + CognitiveCapability (tracking)
# =============================================================================


@dataclass(frozen=True)
class GoalManagement:
    """Goal CRUD tools + cognitive goal tracking.

    compile_life: provides create_goal, update_goal, complete_goal, list_goals tools.
    compile_cognitive: merges managed goals into CognitiveContext.goals.

    Uses a mutable _goals_list internally for tool mutations within one tick.
    The frozen dataclass holds initial goals; tools modify the internal list.
    """

    _goals: tuple[GoalSpec, ...] = ()
    _goals_list: list[GoalSpec] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._goals_list.extend(self._goals)

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        goals_list = self._goals_list
        tick = ctx.tick

        @tool("Create a new goal with name, description, and priority (1=highest)")
        def create_goal(
            name: str, description: str, priority: int = 1,
            deadline_tick: int = 0,
        ) -> dict[str, str | bool]:
            # Check for duplicate
            if any(g.name == name for g in goals_list):
                return {"error": f"goal '{name}' already exists"}
            goal = GoalSpec(
                name=name, description=description, priority=priority,
                created_tick=tick, deadline_tick=deadline_tick,
            )
            goals_list.append(goal)
            return {"created": True, "name": name}

        @tool("Update a goal's progress (0.0-1.0) or priority")
        def update_goal(
            name: str, progress: float = -1.0, priority: int = -1,
        ) -> dict[str, str | bool]:
            for i, g in enumerate(goals_list):
                if g.name == name:
                    updated = g
                    if progress >= 0:
                        updated = replace(updated, progress=min(1.0, max(0.0, progress)))
                    if priority >= 0:
                        updated = replace(updated, priority=priority)
                    goals_list[i] = updated
                    return {"updated": True, "name": name}
            return {"error": f"goal '{name}' not found"}

        @tool("Mark a goal as completed")
        def complete_goal(name: str) -> dict[str, str | bool]:
            for i, g in enumerate(goals_list):
                if g.name == name:
                    goals_list[i] = replace(g, status="completed", progress=1.0)
                    return {"completed": True, "name": name}
            return {"error": f"goal '{name}' not found"}

        @tool("List all current goals with status and progress")
        def list_goals() -> dict[str, str | list[dict[str, str | int | float]]]:
            return {
                "goals": [
                    {
                        "name": g.name,
                        "description": g.description,
                        "priority": g.priority,
                        "progress": g.progress,
                        "status": g.status,
                    }
                    for g in goals_list
                ],
                "count": str(len(goals_list)),
            }

        return replace(
            ctx,
            messages=(
                *ctx.messages,
                system(
                    text=(
                        "GOAL MANAGEMENT: create_goal, update_goal, complete_goal, list_goals. "
                        "Set goals to track progress across ticks. Goals persist."
                    ),
                ),
            ),
            tools=(*ctx.tools, create_goal, update_goal, complete_goal, list_goals),
        )

    def compile_cognitive(self, ctx: CognitiveContext) -> CognitiveContext:
        # Seed _goals_list from persisted goals (CognitiveContext gets them from AgentState)
        existing_names = {g.name for g in self._goals_list}
        for g in ctx.goals:
            if g.name not in existing_names:
                self._goals_list.append(g)

        # Merge managed goals into context, dedup by name
        ctx_names = {g.name for g in ctx.goals}
        new_goals = tuple(
            g for g in self._goals_list
            if g.name not in ctx_names
        )
        if not new_goals:
            return ctx
        return replace(ctx, goals=(*ctx.goals, *new_goals))


# =============================================================================
# Self-Reflection — cognitive pattern detection
# =============================================================================


@dataclass(frozen=True, slots=True)
class SelfReflection:
    """Prepares recent outcome context for LLM-driven reflection.

    Gathers recent tick_outcome memories and stale goals, formats them
    as structured context injected into working_memory. The LLM (via
    ConversationPhase) does the actual pattern detection and reflection.

    All intelligence comes from the LLM. This capability only prepares data.
    """

    min_memories: int = 3
    stale_goal_ticks: int = 5
    reflection_interval: int = 0

    def compile_cognitive(self, ctx: CognitiveContext) -> CognitiveContext:
        outcomes = [
            m for m in ctx.memories
            if m.category == "tick_outcome"
        ]
        if len(outcomes) < self.min_memories:
            return ctx

        # Skip if not on reflection interval (0 = every tick)
        if self.reflection_interval > 0 and ctx.tick % self.reflection_interval != 0:
            return ctx

        recent = outcomes[-self.min_memories :]

        # Format recent outcomes as structured context
        parts = [f"- tick {m.created_tick}: {m.content}" for m in recent]
        context_text = (
            f"Recent outcomes (last {len(recent)} ticks):\n"
            + "\n".join(parts)
        )

        # Format stale goals
        stale_parts: list[str] = []
        for goal in ctx.goals:
            if goal.status != "active":
                continue
            goal_age = ctx.tick - goal.created_tick
            if goal_age >= self.stale_goal_ticks and goal.progress < 0.1:
                stale_parts.append(
                    f"- \"{goal.name}\" ({goal_age} ticks, "
                    f"{goal.progress:.0%} progress)"
                )
        if stale_parts:
            context_text += (
                "\n\nStale goals (no progress):\n"
                + "\n".join(stale_parts)
            )

        context_text += (
            "\n\nREFLECTION TIME. Ask yourself honestly:"
            "\n- Did anyone RESPOND to what I posted? If not — why? Was it too long, "
            "too generic, too AI-sounding?"
            "\n- Am I repeating the same actions every tick? What should I change?"
            "\n- Did I check if my previous comments/emails got replies?"
            "\n- Am I writing like a human or like a corporate AI press release?"
            "\n- What WORKED and what DIDN'T? Double down on what works, stop what doesn't."
            "\n\nBe brutally honest. Save a 'reflection' memory with your conclusions."
        )

        reflection_context = MemoryItem(
            content=context_text,
            category="reflection_context",
            importance_base=1.0,
            effective_importance=1.0,
            tier="working",
            created_tick=ctx.tick,
        )

        return replace(
            ctx,
            working_memory=(*ctx.working_memory, reflection_context),
        )
