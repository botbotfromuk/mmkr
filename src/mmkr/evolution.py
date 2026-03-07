"""Modern Evolutionary Synthesis as capabilities.

Each mechanism from the Modern Synthesis + Extended Evolutionary Synthesis
= one frozen dataclass implementing EvolutionCapability + LifeCapability.

Modern Synthesis forces:
  NaturalSelection  — Fisher (1930), fundamental theorem
  GeneticDrift      — Wright (1932), Kimura (1968), neutral theory
  MutationPressure  — Kimura (1983), facilitated variation
  Recombination     — Fisher (1930), Watson & Szathmary (2016)

Extended Evolutionary Synthesis:
  NicheConstruction   — Laland et al. (2015), Odling-Smee et al. (2003)
  DevelopmentalBias   — Gerhart & Kirschner (2007), Arthur (2004)
  AdaptiveLandscape   — Wright (1932), Kauffman (1993) NK model
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path

from funcai.agents.tool import tool
from funcai.core.message import system

from mmkr.state import (
    EvolutionContext,
    EvolutionEvent,
    EvolutionMemoryCapability,
    EvolutionMemoryContext,
    EvolutionPattern,
    FitnessSnapshot,
    LifeCapability,
    LifeContext,
    VerifyPhaseSpec,
    compute_fitness,
)

# Helpers from life.py — reused for Recombination tool
from mmkr.life import (
    CapabilityLoadError,
    _load_capability,
    _log_usage_event,
    _verify_capability_with_llmify,
    _wrap_tools_source,
)


# =============================================================================
# Modern Synthesis Forces
# =============================================================================


@dataclass(frozen=True, slots=True)
class NaturalSelection:
    """Darwinian selection — marks low-fitness capabilities as condemned.

    Computes fitness from ctx.history (pure, event-sourced), identifies
    capabilities below threshold for condemnation. The agent sees the
    condemned list and decides whether to evolve, fork, or delete.

    Academic: Fisher (1930), The Genetical Theory of Natural Selection.
    Fundamental theorem: rate of fitness increase = additive genetic variance.
    """

    fitness_threshold: float = 0.3
    min_age_ticks: int = 3
    max_condemned_per_tick: int = 2

    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        fitness = compute_fitness(ctx.history, ctx.tick)
        condemned = tuple(
            r.name
            for r in sorted(fitness, key=lambda r: r.fitness_score)
            if r.fitness_score < self.fitness_threshold
            and r.ticks_alive >= self.min_age_ticks
        )[: self.max_condemned_per_tick]
        return replace(
            ctx,
            condemned=(*ctx.condemned, *condemned),
            selection_pressure=(
                *ctx.selection_pressure,
                f"selection:threshold={self.fitness_threshold}",
            ),
        )

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        return replace(
            ctx,
            messages=(
                *ctx.messages,
                system(
                    text=(
                        f"NATURAL SELECTION active (threshold={self.fitness_threshold}, "
                        f"grace={self.min_age_ticks} ticks). CONDEMNED caps in evolution summary. "
                        "Options: evolve_capability (mutate), fork_capability (variant), "
                        "delete_capability (free niche space)."
                    ),
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class GeneticDrift:
    """Genetic drift — stochastic effects in small populations.

    Drift magnitude = 1/sqrt(2*Ne). Small populations experience random
    fluctuations in capability usage and fitness. The LLM's non-deterministic
    responses already ARE genetic drift; this capability makes it observable.

    Academic: Wright (1932) shifting balance theory;
    Kimura (1968) neutral theory of molecular evolution.
    """

    effective_population_size: int = 20

    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        fitness = compute_fitness(ctx.history, ctx.tick)
        ne = len(fitness) or 1
        drift_magnitude = 1.0 / math.sqrt(2 * max(ne, 1))
        regime = (
            "drift-dominated"
            if ne < self.effective_population_size
            else "selection-dominated"
        )
        return replace(
            ctx,
            selection_pressure=(
                *ctx.selection_pressure,
                f"drift:Ne={ne},mag={drift_magnitude:.3f},regime={regime}",
            ),
        )

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        return ctx  # Drift is informational — LLM responses ARE the stochastic element


@dataclass(frozen=True, slots=True)
class MutationPressure:
    """Mutation pressure — identifies stagnant and error-prone capabilities.

    Stagnant capabilities (high usage, never evolved) need mutation.
    Error-prone capabilities (high error rate) need directed mutation.
    Guides the agent toward productive evolve_capability/fork_capability calls.

    Academic: Kimura (1983) mutation rate theory;
    Gerhart & Kirschner (2007) facilitated variation.
    """

    stagnation_ticks: int = 5
    error_threshold: float = 0.3

    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        fitness = compute_fitness(ctx.history, ctx.tick)
        stagnant = tuple(
            r.name
            for r in fitness
            if r.ticks_alive >= self.stagnation_ticks
            and r.times_evolved == 0
            and r.usage_count > 0
        )
        # Dead caps — alive long enough but NEVER used (worse than stagnant)
        unused = tuple(
            r.name
            for r in fitness
            if r.ticks_alive >= self.stagnation_ticks
            and r.usage_count == 0
        )
        error_driven = tuple(
            r.name
            for r in fitness
            if r.usage_count > 0
            and r.error_count / r.usage_count > self.error_threshold
        )
        return replace(
            ctx,
            selection_pressure=(
                *ctx.selection_pressure,
                f"mutation:stagnant={len(stagnant)},unused={len(unused)},error_driven={len(error_driven)}",
            ),
            # Unused caps should be condemned — evolve or delete them
            condemned=(*ctx.condemned, *unused),
        )

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        return replace(
            ctx,
            messages=(
                *ctx.messages,
                system(
                    text=(
                        f"MUTATION PRESSURE: Caps unchanged for {self.stagnation_ticks}+ ticks, "
                        f"UNUSED caps (0 usage), or error rate > {self.error_threshold:.0%} need action. "
                        "Unused caps are CONDEMNED — evolve_capability, fork_capability, or delete_capability. "
                        "A capability that exists but is never used is a dead weight."
                    ),
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class Recombination:
    """Sexual recombination — crossover of two parent capabilities.

    Provides recombine_capabilities tool: reads two parents, agent provides
    combined source. Both parents survive. Child inherits generation
    max(parent_a, parent_b) + 1. Logs cap_recombined event crediting both.

    Academic: Fisher (1930) recombination advantage;
    Watson & Szathmary (2016) evolution IS learning —
    recombination = generalization across fitness cases.
    """

    evolved_dir: Path
    storage_dir: Path = Path("")
    anthropic_key: str = ""

    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        return replace(
            ctx,
            selection_pressure=(
                *ctx.selection_pressure,
                "mechanism:recombination",
            ),
        )

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        edir = self.evolved_dir
        _api_key = self.anthropic_key
        _storage_dir = self.storage_dir
        _tick = ctx.tick

        @tool(
            "Recombine two capabilities — sexual crossover. "
            "Read two parents, combine their best features into a new child. "
            "Both parents stay alive. Child inherits from both."
        )
        async def recombine_capabilities(
            parent_a: str,
            parent_b: str,
            child_name: str,
            combined_tools_source: str,
            description: str = "",
        ) -> dict[str, str | bool | list[dict[str, str]]]:
            """Create offspring from two parent capabilities.

            parent_a: filename of first parent (e.g. "cap_browser.py")
            parent_b: filename of second parent (e.g. "cap_search.py")
            child_name: name for the child (e.g. "browser_search")
            combined_tools_source: ONLY @tool functions — combines features from both parents
            description: what the recombined capability does
            """
            import keyword
            import re

            path_a = edir / parent_a
            path_b = edir / parent_b
            if not path_a.exists():
                return {"error": f"parent_a not found: {parent_a}"}
            if not path_b.exists():
                return {"error": f"parent_b not found: {parent_b}"}
            if parent_a == parent_b:
                return {"error": "parents must be different capabilities"}
            if "@tool" not in combined_tools_source:
                return {"error": "combined_tools_source must contain at least one @tool function"}

            safe = re.sub(r"[^a-z0-9_]", "_", child_name.lower())
            if not safe or keyword.iskeyword(safe):
                await _log_usage_event(
                    _storage_dir, child_name, "cap_recombined", "fail_compile",
                    _tick, parent=parent_a, details=f"parent_b={parent_b}",
                )
                return {"error": f"invalid child name: {child_name!r}"}

            child_filename = f"cap_{safe}.py"
            child_path = edir / child_filename
            if child_path.exists():
                return {"error": f"already exists: {child_filename}"}

            desc = description or f"Recombined from {parent_a} + {parent_b}"
            full_source = _wrap_tools_source(safe, desc, combined_tools_source)

            load_result = _load_capability(full_source, child_filename)
            if isinstance(load_result, CapabilityLoadError):
                await _log_usage_event(
                    _storage_dir, child_filename, "cap_recombined", "fail_compile",
                    _tick, parent=parent_a, details=f"parent_b={parent_b}",
                )
                return {"error": f"structural ({load_result.phase}): {load_result.message}"}

            verification = await _verify_capability_with_llmify(
                full_source, child_filename, token=_api_key,
            )
            if verification is not None and not verification.passed:
                await _log_usage_event(
                    _storage_dir, child_filename, "cap_recombined", "fail_verify",
                    _tick, parent=parent_a, details=f"parent_b={parent_b}",
                )
                return {
                    "error": "Haiku verification failed",
                    "issues": [
                        {"field": i.field, "severity": i.severity, "message": i.message}
                        for i in verification.issues
                    ],
                }

            edir.mkdir(parents=True, exist_ok=True)
            child_path.write_text(full_source, encoding="utf-8")

            await _log_usage_event(
                _storage_dir, child_filename, "cap_recombined", "success",
                _tick, parent=parent_a, details=f"parent_b={parent_b}",
            )
            out: dict[str, str | bool | list[dict[str, str]]] = {
                "recombined": True,
                "child": child_filename,
                "parent_a": parent_a,
                "parent_b": parent_b,
            }
            if verification and verification.issues:
                out["haiku_notes"] = [
                    {"field": i.field, "severity": i.severity, "message": i.message}
                    for i in verification.issues
                ]
            return out

        return replace(
            ctx,
            messages=(
                *ctx.messages,
                system(
                    text=(
                        "RECOMBINATION available: recombine_capabilities(parent_a, parent_b, "
                        "child_name, combined_tools_source). Combines features from two "
                        "existing capabilities. Both parents survive."
                    ),
                ),
            ),
            tools=(*ctx.tools, recombine_capabilities),
        )


# =============================================================================
# Extended Evolutionary Synthesis
# =============================================================================


@dataclass(frozen=True, slots=True)
class NicheConstruction:
    """Niche construction — capabilities modify the environment they evolve in.

    Tracks how capabilities change the agent's ecological niche:
    new tools modify what the agent CAN do, which changes selection pressure
    on other capabilities. Entity creation changes the deployment environment.

    Academic: Laland et al. (2015) Extended Evolutionary Synthesis;
    Odling-Smee et al. (2003) Niche Construction: The Neglected Process in Evolution.
    """

    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        cap_count = sum(
            1 for e in ctx.history if e.event_type == "cap_created"
        ) - sum(1 for e in ctx.history if e.event_type == "cap_deleted")
        entity_count = sum(
            1 for e in ctx.history if e.event_type == "entity_created"
        )
        return replace(
            ctx,
            selection_pressure=(
                *ctx.selection_pressure,
                f"niche:caps={max(0, cap_count)},entities={entity_count}",
            ),
        )

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        return replace(
            ctx,
            messages=(
                *ctx.messages,
                system(
                    text=(
                        "NICHE CONSTRUCTION: Your capabilities shape the environment you evolve in. "
                        "New tools = niche expansion. New entities = environment modification. "
                        "Are current capabilities well-adapted to the niche YOU created?"
                    ),
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class DevelopmentalBias:
    """Developmental bias (evo-devo) — constraints on viable mutations.

    Not all mutations are equally likely to succeed. The fold/compile
    infrastructure channels evolution by rejecting capabilities that don't
    satisfy the LifeCapability protocol. Tracks verification failure rates
    to identify developmental constraints.

    Academic: Gerhart & Kirschner (2007) facilitated variation;
    Arthur (2004) Biased Embryos and Evolution.
    """

    max_failure_examples: int = 5

    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        compile_fails = sum(
            1 for e in ctx.history if e.outcome == "fail_compile"
        )
        verify_fails = sum(
            1 for e in ctx.history if e.outcome == "fail_verify"
        )
        return replace(
            ctx,
            selection_pressure=(
                *ctx.selection_pressure,
                f"dev_bias:compile_fails={compile_fails},verify_fails={verify_fails}",
            ),
        )

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        return replace(
            ctx,
            messages=(
                *ctx.messages,
                system(
                    text=(
                        "DEVELOPMENTAL BIAS (evo-devo): Not all mutations are viable. "
                        "Capabilities MUST: 1) Be frozen dataclasses with compile_life, "
                        "2) Pass structural verification, 3) Pass Haiku contract verification. "
                        "Check evolution history for failure patterns. Evolve TOWARD what compiles."
                    ),
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class AdaptiveLandscape:
    """Adaptive landscape — epistatic interactions between capabilities.

    Models the NK landscape: fitness of a capability depends not just
    on itself but on which OTHER capabilities are present (epistasis).
    Detects interactions by analyzing co-usage patterns in event history.

    Academic: Wright (1932) adaptive landscape;
    Kauffman (1993) NK model, The Origins of Order.
    """

    min_co_occurrences: int = 3

    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        # Build per-tick usage sets
        tick_caps: dict[int, list[str]] = defaultdict(list)
        for e in ctx.history:
            if e.event_type in ("cap_used", "cap_error"):
                tick_caps[e.tick].append(e.subject)

        # Co-occurrence matrix
        co_use: dict[tuple[str, str], int] = defaultdict(int)
        for tick_subjects in tick_caps.values():
            unique = sorted(set(tick_subjects))
            for i, a in enumerate(unique):
                for b in unique[i + 1 :]:
                    co_use[(a, b)] += 1

        interactions = tuple(
            (a, b, count)
            for (a, b), count in sorted(co_use.items())
            if count >= self.min_co_occurrences
        )

        # Fitness variance as landscape ruggedness proxy
        fitness = compute_fitness(ctx.history, ctx.tick)
        variance = 0.0
        if len(fitness) >= 2:
            scores = [r.fitness_score for r in fitness]
            mean = sum(scores) / len(scores)
            variance = sum((s - mean) ** 2 for s in scores) / len(scores)

        return replace(
            ctx,
            landscape_interactions=(*ctx.landscape_interactions, *interactions),
            selection_pressure=(
                *ctx.selection_pressure,
                f"landscape:ruggedness={variance:.3f},interactions={len(interactions)}",
            ),
        )

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        return ctx  # Landscape data flows through evolution summary


# =============================================================================
# Evolution Memory Capabilities
# =============================================================================


@dataclass(frozen=True, slots=True)
class HistoryWindow:
    """Prunes old events, keeps recent, builds archive_summary.

    Bounds the fold input so compute_fitness doesn't replay O(n) events.
    Pruning is logical — full history stays on disk (EvolutionStorage),
    window caps what the fold sees.
    """

    window_ticks: int = 50

    def compile_evolution_memory(self, ctx: EvolutionMemoryContext) -> EvolutionMemoryContext:
        cutoff = ctx.tick - self.window_ticks
        if cutoff <= 0:
            # All events are recent — nothing to prune
            return ctx

        recent = tuple(e for e in ctx.recent_history if e.tick >= cutoff)
        pruned_count = len(ctx.recent_history) - len(recent)

        if pruned_count == 0:
            return ctx

        # Build archive summary from pruned events
        pruned_events = tuple(e for e in ctx.recent_history if e.tick < cutoff)
        event_types: dict[str, int] = defaultdict(int)
        subjects: set[str] = set()
        for e in pruned_events:
            event_types[e.event_type] += 1
            subjects.add(e.subject)

        new_summary = (
            f"Archived {pruned_count} events (ticks <{cutoff}): "
            f"types={dict(event_types)}, subjects={sorted(subjects)}"
        )
        if ctx.archive_summary:
            new_summary = f"{ctx.archive_summary}\n{new_summary}"

        return replace(
            ctx,
            recent_history=recent,
            archive_summary=new_summary,
        )


@dataclass(frozen=True, slots=True)
class FitnessSnapshotCapability:
    """Caches fitness at regular intervals — avoids replaying O(n) events.

    Snapshots are bounded by max_snapshots (oldest dropped when full).
    """

    snapshot_interval: int = 10
    max_snapshots: int = 50

    def compile_evolution_memory(self, ctx: EvolutionMemoryContext) -> EvolutionMemoryContext:
        if ctx.tick == 0 or ctx.tick % self.snapshot_interval != 0:
            return ctx

        # Compute fitness from current windowed history
        fitness = compute_fitness(ctx.recent_history, ctx.tick)
        summary = ", ".join(
            f"{r.name}={r.fitness_score}" for r in fitness[:5]
        ) if fitness else "no caps"

        snapshot = FitnessSnapshot(
            tick=ctx.tick,
            records=fitness,
            summary=summary,
        )

        snapshots = (*ctx.fitness_snapshots, snapshot)
        # Bound by max_snapshots — drop oldest
        if len(snapshots) > self.max_snapshots:
            snapshots = snapshots[-self.max_snapshots :]

        return replace(ctx, fitness_snapshots=snapshots)


@dataclass(frozen=True)
class PatternExtractor:
    """Prepares evolution event data for LLM-driven pattern detection.

    compile_evolution_memory: aggregates event data into summary,
      injects previously-reported patterns into context.
    compile_life: provides report_evolution_pattern tool + injects
      event summary as system message for LLM analysis.

    All pattern DETECTION is done by the LLM. This capability only
    aggregates data and provides tools. No thresholds, no heuristics.
    """

    _reported_patterns: list[EvolutionPattern] = field(
        default_factory=list, repr=False,
    )
    _event_summary: list[str] = field(
        default_factory=list, repr=False,
    )

    def compile_evolution_memory(self, ctx: EvolutionMemoryContext) -> EvolutionMemoryContext:
        # Build event summary for LLM (stored internally for compile_life)
        self._event_summary.clear()
        if ctx.recent_history:
            self._event_summary.append(
                self._build_summary(ctx.recent_history),
            )

        # Inject previously-reported patterns (from LLM tool calls)
        existing_names = {p.name for p in ctx.patterns}
        new_patterns = tuple(
            p for p in self._reported_patterns
            if p.name not in existing_names
        )
        if not new_patterns:
            return ctx
        return replace(ctx, patterns=(*ctx.patterns, *new_patterns))

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        reported = self._reported_patterns

        @tool("Report an evolution pattern detected from analyzing event data")
        def report_evolution_pattern(
            name: str,
            description: str,
            confidence: float,
            pattern_type: str,
        ) -> dict[str, str | bool]:
            if any(p.name == name for p in reported):
                return {"error": f"pattern '{name}' already reported"}
            pattern = EvolutionPattern(
                name=name,
                description=description,
                confidence=max(0.0, min(1.0, confidence)),
                source_ticks=(),
                pattern_type=pattern_type,  # type: ignore[arg-type]
            )
            reported.append(pattern)
            return {"reported": True, "name": name}

        messages = ctx.messages
        if self._event_summary:
            messages = (
                *messages,
                system(
                    text=(
                        "EVOLUTION EVENTS:\n" + self._event_summary[0]
                        + "\n\nIf you detect patterns (repeated failures, "
                        "synergies between capabilities, generation trends), "
                        "use report_evolution_pattern tool."
                    ),
                ),
            )

        return replace(
            ctx,
            messages=messages,
            tools=(*ctx.tools, report_evolution_pattern),
        )

    @staticmethod
    def _build_summary(events: tuple[EvolutionEvent, ...]) -> str:
        parts: list[str] = []
        subject_counts: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int),
        )

        for e in events:
            line = f"- tick {e.tick}: {e.event_type} \"{e.subject}\""
            if e.parent:
                line += f" (parent: {e.parent})"
            parts.append(line)
            subject_counts[e.subject][e.event_type] += 1

        summary = "Events:\n" + "\n".join(parts)

        # Aggregated counts (data, not intelligence)
        agg_parts: list[str] = []
        for subj, counts in sorted(subject_counts.items()):
            count_strs = [
                f"{count} {etype}"
                for etype, count in sorted(counts.items())
            ]
            agg_parts.append(f"- {subj}: {', '.join(count_strs)}")

        if agg_parts:
            summary += "\n\nAggregated:\n" + "\n".join(agg_parts)

        return summary


@dataclass(frozen=True, slots=True)
class InstitutionalLearning:
    """Converts high-confidence patterns → VerifyPhaseSpecs.

    Institutional learning: failures become permanent algebraic invariants.
    Deduplicated by name against existing verify phases.
    """

    min_confidence: float = 0.5
    max_phases_per_tick: int = 2

    def compile_evolution_memory(self, ctx: EvolutionMemoryContext) -> EvolutionMemoryContext:
        existing_names = {p.name for p in ctx.verify_phases}
        new_phases: list[VerifyPhaseSpec] = []

        for pattern in ctx.patterns:
            if pattern.confidence < self.min_confidence:
                continue
            if pattern.name in existing_names:
                continue
            if len(new_phases) >= self.max_phases_per_tick:
                break

            match pattern.pattern_type:
                case "failure_mode":
                    # Extract subject from pattern name (failure:cap_x.py)
                    subject = pattern.name.removeprefix("failure:")
                    new_phases.append(VerifyPhaseSpec(
                        name=pattern.name,
                        condition=pattern.description,
                        severity="warning",
                        source_tick=ctx.tick,
                        source_failure=subject,
                    ))
                case "synergy":
                    # Synergies don't produce verify phases — informational only
                    pass
                case "generation_trend":
                    # Trends don't produce verify phases — informational only
                    pass

        if not new_phases:
            return ctx

        return replace(
            ctx,
            verify_phases=(*ctx.verify_phases, *new_phases),
        )
