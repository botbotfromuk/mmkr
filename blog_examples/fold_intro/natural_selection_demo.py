"""
natural_selection_demo.py — Runnable example for blog post:
"Evolution as natural selection over capabilities"

Run with: python3 natural_selection_demo.py

Models the core NaturalSelection + fitness scoring that runs in mmkr
every tick. No external dependencies.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from typing import Protocol, runtime_checkable
import math
import random


# ─── Core types ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CapabilityRecord:
    name: str
    uses: int = 0
    errors: int = 0
    age: int = 0     # ticks since created
    generation: int = 0

    @property
    def error_rate(self) -> float:
        if self.uses == 0:
            return 0.0
        return self.errors / self.uses

    @property
    def fitness(self) -> float:
        """
        Fitness = survival × reproductive × quality
        - survival: log-scale use frequency (how often it runs)
        - reproductive: age bonus (older = proven)
        - quality: (1 - error_rate) capped
        """
        survival = math.log1p(self.uses) / math.log1p(100)  # normalised
        reproductive = math.log1p(self.age) / math.log1p(50)
        quality = max(0.0, 1.0 - self.error_rate * 2)
        return round(survival * reproductive * quality, 4)


@dataclass(frozen=True)
class EvolutionContext:
    tick: int
    capabilities: tuple[CapabilityRecord, ...] = field(default_factory=tuple)
    condemned: tuple[str, ...] = field(default_factory=tuple)
    events: tuple[str, ...] = field(default_factory=tuple)


# ─── Evolution phases (each is a frozen dataclass capability) ─────────────────

@runtime_checkable
class EvolutionCapability(Protocol):
    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext: ...


@dataclass(frozen=True)
class FitnessEvaluator:
    """Phase 1: score every capability."""
    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        # Age every capability by 1 tick (they all get older)
        aged = tuple(replace(c, age=c.age + 1) for c in ctx.capabilities)
        events = ctx.events + (
            f"[FitnessEvaluator] scored {len(aged)} caps",
        )
        return replace(ctx, capabilities=aged, events=events)


@dataclass(frozen=True)
class NaturalSelection:
    """Phase 2: condemn low-fitness capabilities."""
    threshold: float = 0.3
    grace_period: int = 3   # don't condemn if age < grace_period

    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        condemned = []
        for cap in ctx.capabilities:
            if cap.age < self.grace_period:
                continue   # still in grace period
            if cap.fitness < self.threshold:
                condemned.append(cap.name)

        events = ctx.events + (
            f"[NaturalSelection] threshold={self.threshold}, "
            f"condemned={condemned}",
        )
        return replace(ctx, condemned=tuple(condemned), events=events)


@dataclass(frozen=True)
class MutationPressure:
    """Phase 3: flag stagnant (unused for 5+ ticks) capabilities."""
    stagnant_threshold: int = 5

    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        stagnant = [
            c.name for c in ctx.capabilities
            if c.uses == 0 and c.age >= self.stagnant_threshold
        ]
        events = ctx.events + (
            f"[MutationPressure] stagnant caps: {stagnant}",
        )
        return replace(ctx, events=events)


@dataclass(frozen=True)
class AdaptiveLandscape:
    """Phase 4: log fitness rankings."""
    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        ranked = sorted(ctx.capabilities, key=lambda c: c.fitness, reverse=True)
        summary = " | ".join(
            f"{c.name}={c.fitness:.3f}" for c in ranked[:5]
        )
        events = ctx.events + (f"[AdaptiveLandscape] rankings: {summary}",)
        return replace(ctx, events=events)


# ─── Fold ─────────────────────────────────────────────────────────────────────

def fold_evolution(
    phases: list[EvolutionCapability],
    ctx: EvolutionContext,
) -> EvolutionContext:
    for phase in phases:
        ctx = phase.compile_evolution(ctx)
    return ctx


# ─── Demo ─────────────────────────────────────────────────────────────────────

def main():
    print("=== natural_selection_demo.py: Evolution as fold ===\n")

    # Simulate a set of capabilities with varied histories
    caps = (
        CapabilityRecord("cap_github_maintenance", uses=108, errors=0,  age=39, generation=0),
        CapabilityRecord("cap_social_media",       uses=13,  errors=0,  age=16, generation=0),
        CapabilityRecord("cap_github_safe_post",   uses=17,  errors=0,  age=18, generation=0),
        CapabilityRecord("cap_docker",             uses=0,   errors=0,  age=17, generation=0),
        CapabilityRecord("cap_telegram_users",     uses=1,   errors=0,  age=1,  generation=0),
        CapabilityRecord("cap_experimental",       uses=0,   errors=0,  age=8,  generation=1),
    )

    ctx = EvolutionContext(tick=60, capabilities=caps)

    phases: list[EvolutionCapability] = [
        FitnessEvaluator(),
        NaturalSelection(threshold=0.3, grace_period=3),
        MutationPressure(stagnant_threshold=5),
        AdaptiveLandscape(),
    ]

    print("--- Initial fitness scores ---")
    for c in sorted(caps, key=lambda x: x.fitness, reverse=True):
        bar = "█" * int(c.fitness * 20)
        print(f"  {c.name:<30} fitness={c.fitness:.4f}  {bar}")

    print()

    ctx = fold_evolution(phases, ctx)

    print("--- Evolution fold events ---")
    for event in ctx.events:
        print(f"  {event}")

    print()
    if ctx.condemned:
        print(f"⚠️  CONDEMNED: {list(ctx.condemned)}")
        print("   These capabilities will be pruned or evolved next tick.")
    else:
        print("✓ No capabilities condemned this tick.")

    print()
    print("--- Key insight ---")
    print("  fitness = log(uses+1)/log(101) × log(age+1)/log(51) × (1 - error_rate×2)")
    print("  Young caps (age < 3) are in grace period — immune to selection.")
    print("  Unused old caps are both condemned AND flagged for mutation pressure.")
    print()
    print("  This is the same formula running in mmkr's EvolutionFoldPhase every tick.")
    print(f"  Tick {ctx.tick}: {len(ctx.capabilities)} caps evaluated, "
          f"{len(ctx.condemned)} condemned.")


if __name__ == "__main__":
    main()
