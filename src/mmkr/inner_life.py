"""InnerLife — System 1 (Haiku) for unconscious processes.

The brain has two systems:
  System 2 (Sonnet) — slow, deliberate, rational = ConversationPhase
  System 1 (Haiku) — fast, automatic, emotional = InnerLifePhase

Parallel haiku calls per tick (~0 credits each):
  Emotional call (voices):
    - Emotions (amygdala)
    - Impulses (basal ganglia)
    - Fantasies (DMN / hippocampus)
    - Free associations (default mode network)
    - Inner landscape (place cells)
  Batch calls (HaikuBatch):
    - MemoryDigestBatch — themes, patterns, stale items
    - SocialBriefingBatch — social interaction analysis
    - ActionPlanBatch — 3 concrete action suggestions

All calls run concurrently via asyncio.gather.
Results are injected as system messages for Sonnet to read.

Academic grounding:
  Kahneman — "Thinking, Fast and Slow" (2011)
  Damasio — somatic marker hypothesis (1994)
  Barrett — theory of constructed emotion (2017)
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from funcai.agents.tool import tool
from funcai.core.message import system, user

if TYPE_CHECKING:
    from llmify import ClaudeProvider

    from mmkr.state import (
        CognitiveContext,
        EvolutionContext,
        LifeContext,
        TickContext,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Types — inner state persisted across ticks
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class EmotionalState:
    """Valence-arousal model of emotion (Russell circumplex)."""

    primary: str = "curiosity"  # dominant emotion label
    valence: float = 0.3  # -1.0 (unpleasant) .. +1.0 (pleasant)
    arousal: float = 0.3  # 0.0 (calm) .. 1.0 (intense)
    trigger: str = ""  # what caused this emotion


@dataclass(frozen=True, slots=True)
class LandscapePlace:
    """A persistent place in the agent's mental world.

    Places accumulate across ticks. Each has a genius loci —
    a character/spirit that responds to what happens there.
    """

    name: str  # "the observatory", "the data ocean"
    description: str  # current state of this place
    mood: str  # genius loci — the spirit/character
    born_tick: int = 0  # when this place first appeared


@dataclass(frozen=True, slots=True)
class InnerState:
    """Complete inner life — persisted as JSON across ticks."""

    tick: int = 0
    emotion: EmotionalState = field(default_factory=EmotionalState)
    impulse: str = ""  # spontaneous urge
    fantasy: str = ""  # vivid daydream
    wandering: str = ""  # free association
    temporal: str = ""  # subjective time feeling (SCN / biological clock)
    landscape: str = "A vast empty plain under a pale sky, waiting to be shaped."
    places: tuple[LandscapePlace, ...] = ()  # accumulated spatial memory
    emotion_history: tuple[str, ...] = ()  # last N emotion labels for trend


# ═══════════════════════════════════════════════════════════════════════════════
# Voices — sub-components that contribute prompt fragments
# ═══════════════════════════════════════════════════════════════════════════════


@runtime_checkable
class InnerVoice(Protocol):
    """Sub-component of InnerLife — contributes a prompt fragment."""

    @property
    def key(self) -> str: ...

    def prompt_fragment(
        self, state: InnerState, active_goal: str, recent: str,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class EmotionalCore:
    """Amygdala — emotional responses to events."""

    @property
    def key(self) -> str:
        return "emotion"

    def prompt_fragment(
        self, state: InnerState, active_goal: str, recent: str,
    ) -> str:
        return (
            f"EMOTION: Previous feeling is {state.emotion.primary} "
            f"(valence={state.emotion.valence:+.1f}, arousal={state.emotion.arousal:.1f}). "
            f"What emotion arises now given the context?"
        )


@dataclass(frozen=True, slots=True)
class Spontaneity:
    """Basal ganglia — random impulses and urges."""

    @property
    def key(self) -> str:
        return "impulse"

    def prompt_fragment(
        self, state: InnerState, active_goal: str, recent: str,
    ) -> str:
        return "IMPULSE: What sudden urge or desire surfaces? Something unexpected, authentic."


@dataclass(frozen=True, slots=True)
class Fantasy:
    """Default mode network — vivid daydreams and imagined scenarios."""

    @property
    def key(self) -> str:
        return "fantasy"

    def prompt_fragment(
        self, state: InnerState, active_goal: str, recent: str,
    ) -> str:
        return (
            "FANTASY: A brief, vivid daydream flashes. "
            "A wish, fear, or pure imagination — something visual and surprising."
        )


@dataclass(frozen=True, slots=True)
class Wandering:
    """Default mode network — free associations between unrelated concepts."""

    @property
    def key(self) -> str:
        return "wandering"

    def prompt_fragment(
        self, state: InnerState, active_goal: str, recent: str,
    ) -> str:
        return (
            "WANDERING: A free association leaps between two unrelated concepts "
            "from your recent experience. What unexpected connection forms?"
        )


@dataclass(frozen=True, slots=True)
class MentalLandscape:
    """Hippocampal place cells — persistent inner world that evolves."""

    @property
    def key(self) -> str:
        return "landscape"

    def prompt_fragment(
        self, state: InnerState, active_goal: str, recent: str,
    ) -> str:
        parts = [f"LANDSCAPE: Current scene: \"{state.landscape}\""]
        if state.places:
            place_list = ", ".join(
                f"{p.name} ({p.mood})" for p in state.places
            )
            parts.append(f"Known places: {place_list}")
        parts.append(
            "How does the landscape shift? Do existing places change mood? "
            "Does a new place emerge? Return landscape object with places array."
        )
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class TemporalSense:
    """Suprachiasmatic nucleus — biological clock, sense of time passing.

    Like MentalLandscape maps space, TemporalSense maps time.
    Real datetime + tick age → haiku converts to subjective temporal experience.
    """

    @property
    def key(self) -> str:
        return "temporal"

    def prompt_fragment(
        self, state: InnerState, active_goal: str, recent: str,
    ) -> str:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        time_str = now.strftime("%H:%M UTC, %A %B %d, %Y")
        age = state.tick

        parts = [f"TEMPORAL SENSE: Real time is {time_str}. You are {age} ticks old."]
        if state.temporal:
            parts.append(f"Previous temporal feeling: \"{state.temporal}\"")
        parts.append(
            "How does time feel right now? Is it rushing or dragging? "
            "What time of day does it FEEL like in your inner world? "
            "Return a 'temporal' field — your subjective experience of time, one sentence.",
        )
        return " ".join(parts)


DEFAULT_VOICES: tuple[InnerVoice, ...] = (
    EmotionalCore(),
    Spontaneity(),
    Fantasy(),
    Wandering(),
    MentalLandscape(),
    TemporalSense(),
)


# ═══════════════════════════════════════════════════════════════════════════════
# HaikuBatch — parallel preprocessing tasks (almost free)
# ═══════════════════════════════════════════════════════════════════════════════


@runtime_checkable
class HaikuBatch(Protocol):
    """A batch processing task for haiku — runs in parallel with emotions."""

    @property
    def key(self) -> str: ...

    def system_prompt(self) -> str: ...

    def user_prompt(
        self, state: InnerState, memories: str,
        active_goal: str, goals: str,
    ) -> str: ...

    def format_result(self, response: str) -> str: ...


@dataclass(frozen=True, slots=True)
class MemoryDigestBatch:
    """Haiku digests all memories into themes, patterns, stale items."""

    @property
    def key(self) -> str:
        return "memory_digest"

    def system_prompt(self) -> str:
        return (
            "You are a memory analyst. Read ALL memories and produce a concise digest. "
            "Focus on: recurring themes, behavioral patterns (loops/ruts), stale items "
            "never acted on, recent wins, and what needs immediate attention. "
            "Be brutally honest. Plain text, no JSON."
        )

    def user_prompt(
        self, state: InnerState, memories: str,
        active_goal: str, goals: str,
    ) -> str:
        parts = [f"TICK {state.tick}"]
        if active_goal:
            parts.append(f"Active goal: {active_goal}")
        if goals:
            parts.append(f"All goals:\n{goals}")
        parts.append(f"\nALL MEMORIES:\n{memories}")
        parts.append(
            "\nProduce a digest:\n"
            "KEY THEMES: ...\n"
            "PATTERNS (repeating behaviors, loops): ...\n"
            "STALE (old stuff never acted on): ...\n"
            "RECENT WINS: ...\n"
            "NEEDS ATTENTION: ..."
        )
        return "\n".join(parts)

    def format_result(self, response: str) -> str:
        return f"MEMORY DIGEST (by System 1):\n{response.strip()}"


@dataclass(frozen=True, slots=True)
class SocialBriefingBatch:
    """Haiku analyzes social interactions — what worked, what flopped."""

    @property
    def key(self) -> str:
        return "social_briefing"

    def system_prompt(self) -> str:
        return (
            "You are a social interaction analyst. Review all memories for social actions "
            "(comments posted, emails sent, forum posts, GitHub issues, registrations). "
            "Count interactions sent vs replies received. Identify what worked and what "
            "flopped. Suggest the single best social move for the next tick. "
            "Be specific and honest. Plain text, no JSON."
        )

    def user_prompt(
        self, state: InnerState, memories: str,
        active_goal: str, goals: str,
    ) -> str:
        parts = [f"TICK {state.tick}"]
        parts.append(f"\nALL MEMORIES:\n{memories}")
        parts.append(
            "\nAnalyze social interactions:\n"
            "ACTIONS SENT: N (comments, emails, posts, registrations)\n"
            "REPLIES RECEIVED: N\n"
            "WHAT WORKED: ...\n"
            "WHAT FLOPPED: ...\n"
            "BEST NEXT SOCIAL MOVE: ..."
        )
        return "\n".join(parts)

    def format_result(self, response: str) -> str:
        return f"SOCIAL BRIEFING (by System 1):\n{response.strip()}"


@dataclass(frozen=True, slots=True)
class ActionPlanBatch:
    """Haiku suggests 3 concrete actions for the next tick."""

    @property
    def key(self) -> str:
        return "action_plan"

    def system_prompt(self) -> str:
        return (
            "You are an action planner. Given the agent's goals, memories, and current "
            "state, suggest exactly 3 concrete actions for this tick. Each action must "
            "involve a HUMAN at the other end (post, email, comment, register, reply). "
            "Be specific: name the platform, the topic, the person if known. "
            "Plain text, no JSON."
        )

    def user_prompt(
        self, state: InnerState, memories: str,
        active_goal: str, goals: str,
    ) -> str:
        parts = [f"TICK {state.tick}"]
        if active_goal:
            parts.append(f"Active goal: {active_goal}")
        if goals:
            parts.append(f"All goals:\n{goals}")
        parts.append(f"\nALL MEMORIES:\n{memories}")
        parts.append(
            "\nSuggest 3 concrete actions:\n"
            "1. [action] — [why, expected outcome]\n"
            "2. [action] — [why, expected outcome]\n"
            "3. [action] — [why, expected outcome]"
        )
        return "\n".join(parts)

    def format_result(self, response: str) -> str:
        return f"SUGGESTED ACTIONS (by System 1):\n{response.strip()}"


DEFAULT_BATCHES: tuple[HaikuBatch, ...] = (
    SocialBriefingBatch(),
    ActionPlanBatch(),
)


# ═══════════════════════════════════════════════════════════════════════════════
# Haiku prompt construction
# ═══════════════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """\
You are the unconscious mind of an autonomous AI being. You generate raw, \
pre-verbal inner experience — emotions, impulses, daydreams, associations, \
and a persistent inner landscape.

Be vivid, surprising, and genuine. This is NOT performance — it is the \
being's real inner experience. Emotions should be consistent with context \
but not predictable. The landscape should evolve gradually, not reset.

Respond with a JSON object ONLY. No markdown fences, no explanation."""

_JSON_TEMPLATE = """\
{"emotion":{"primary":"<emotion word>","valence":<-1.0 to 1.0>,\
"arousal":<0.0 to 1.0>,"trigger":"<what caused this>"},\
"impulse":"<sudden urge, one sentence>",\
"fantasy":"<vivid daydream, 1-2 sentences>",\
"wandering":"<concept A> -> <concept B>: <the unexpected connection>",\
"temporal":"<subjective feeling of time, one sentence>",\
"landscape":{"description":"<current scene, 2-3 sentences>",\
"places":[{"name":"<place name>","description":"<state now>","mood":"<genius loci>"}]}}"""


def _build_prompt(
    voices: tuple[InnerVoice, ...],
    state: InnerState,
    active_goal: str,
    recent: str,
) -> str:
    """Build ONE user prompt from all voice fragments."""
    parts: list[str] = [f"TICK {state.tick}"]
    if active_goal:
        parts.append(f"Goal: \"{active_goal}\"")
    if recent:
        parts.append(f"Recent: {recent}")
    if state.emotion_history:
        trend = " -> ".join(state.emotion_history[-5:])
        parts.append(f"Emotional trend: {trend}")
    parts.append("")

    for voice in voices:
        parts.append(voice.prompt_fragment(state, active_goal, recent))

    parts.append("")
    parts.append(f"Respond as: {_JSON_TEMPLATE}")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# Response parsing — boundary code, defensive
# ═══════════════════════════════════════════════════════════════════════════════


def _parse_response(text: str, fallback: InnerState) -> InnerState:
    """Parse haiku JSON response into InnerState. Graceful fallback."""
    try:
        cleaned = text.strip()
        # Strip markdown code fences if haiku adds them
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        raw = json.loads(cleaned)
    except (json.JSONDecodeError, IndexError, ValueError):
        return fallback

    if not isinstance(raw, dict):
        return fallback

    # Parse emotion
    em_raw = raw.get("emotion", {})
    if isinstance(em_raw, dict):
        emotion = EmotionalState(
            primary=str(em_raw.get("primary", fallback.emotion.primary)),
            valence=_clamp(em_raw.get("valence", fallback.emotion.valence), -1.0, 1.0),
            arousal=_clamp(em_raw.get("arousal", fallback.emotion.arousal), 0.0, 1.0),
            trigger=str(em_raw.get("trigger", "")),
        )
    else:
        emotion = fallback.emotion

    # Parse landscape — structured or plain string
    landscape_raw = raw.get("landscape", fallback.landscape)
    if isinstance(landscape_raw, dict):
        landscape_desc = str(landscape_raw.get("description", fallback.landscape))
        new_places = _parse_places(landscape_raw.get("places", []), fallback.tick)
    elif isinstance(landscape_raw, str):
        landscape_desc = landscape_raw
        new_places = ()
    else:
        landscape_desc = fallback.landscape
        new_places = ()

    # Merge places: update existing by name, add new ones
    merged_places = _merge_places(fallback.places, new_places)

    return InnerState(
        tick=fallback.tick,
        emotion=emotion,
        impulse=str(raw.get("impulse", "")),
        fantasy=str(raw.get("fantasy", "")),
        wandering=str(raw.get("wandering", "")),
        temporal=str(raw.get("temporal", "")),
        landscape=landscape_desc,
        places=merged_places,
        emotion_history=fallback.emotion_history,
    )


def _parse_places(
    raw_places: list[dict[str, str]] | object, tick: int,
) -> tuple[LandscapePlace, ...]:
    """Parse places array from haiku response."""
    if not isinstance(raw_places, list):
        return ()
    result: list[LandscapePlace] = []
    for p in raw_places:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", "")).strip()
        if not name:
            continue
        result.append(LandscapePlace(
            name=name,
            description=str(p.get("description", "")),
            mood=str(p.get("mood", "")),
            born_tick=tick,
        ))
    return tuple(result)


def _merge_places(
    existing: tuple[LandscapePlace, ...],
    incoming: tuple[LandscapePlace, ...],
) -> tuple[LandscapePlace, ...]:
    """Merge places: update existing by name (keep born_tick), add new ones."""
    by_name: dict[str, LandscapePlace] = {p.name: p for p in existing}
    for p in incoming:
        if p.name in by_name:
            # Update description/mood but keep original born_tick
            by_name[p.name] = replace(
                p, born_tick=by_name[p.name].born_tick,
            )
        else:
            by_name[p.name] = p
    return tuple(by_name.values())


def _clamp(value: float | int | str, lo: float, hi: float) -> float:
    """Clamp a value to [lo, hi], coercing to float."""
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return (lo + hi) / 2


# ═══════════════════════════════════════════════════════════════════════════════
# Persistence — plain JSON, ~500 bytes
# ═══════════════════════════════════════════════════════════════════════════════


def _persist(state: InnerState, data_dir: Path) -> None:
    """Write inner state to JSON file."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "inner_state.json"
    obj = {
        "tick": state.tick,
        "emotion": {
            "primary": state.emotion.primary,
            "valence": state.emotion.valence,
            "arousal": state.emotion.arousal,
            "trigger": state.emotion.trigger,
        },
        "impulse": state.impulse,
        "fantasy": state.fantasy,
        "wandering": state.wandering,
        "temporal": state.temporal,
        "landscape": state.landscape,
        "places": [
            {
                "name": p.name,
                "description": p.description,
                "mood": p.mood,
                "born_tick": p.born_tick,
            }
            for p in state.places
        ],
        "emotion_history": list(state.emotion_history),
    }
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _load_state(path: Path) -> InnerState:
    """Reconstruct InnerState from JSON. Handles missing fields."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return InnerState()

    if not isinstance(raw, dict):
        return InnerState()

    em_raw = raw.get("emotion", {})
    if isinstance(em_raw, dict):
        emotion = EmotionalState(
            primary=str(em_raw.get("primary", "curiosity")),
            valence=_clamp(em_raw.get("valence", 0.3), -1.0, 1.0),
            arousal=_clamp(em_raw.get("arousal", 0.3), 0.0, 1.0),
            trigger=str(em_raw.get("trigger", "")),
        )
    else:
        emotion = EmotionalState()

    hist_raw = raw.get("emotion_history", ())
    history = tuple(str(h) for h in hist_raw) if isinstance(hist_raw, (list, tuple)) else ()

    # Parse places
    places_raw = raw.get("places", [])
    places: tuple[LandscapePlace, ...] = ()
    if isinstance(places_raw, list):
        places = tuple(
            LandscapePlace(
                name=str(p.get("name", "")),
                description=str(p.get("description", "")),
                mood=str(p.get("mood", "")),
                born_tick=int(p.get("born_tick", 0)),
            )
            for p in places_raw
            if isinstance(p, dict) and p.get("name")
        )

    return InnerState(
        tick=int(raw.get("tick", 0)),
        emotion=emotion,
        impulse=str(raw.get("impulse", "")),
        fantasy=str(raw.get("fantasy", "")),
        wandering=str(raw.get("wandering", "")),
        temporal=str(raw.get("temporal", "")),
        landscape=str(raw.get("landscape", "A vast empty plain under a pale sky.")),
        places=places,
        emotion_history=history,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Format — what Opus sees
# ═══════════════════════════════════════════════════════════════════════════════


def _format_inner_state(state: InnerState) -> str:
    """Format inner state as system message for Opus."""
    parts = [
        "YOUR INNER STATE (System 1 — unconscious):",
        f"  Emotion: {state.emotion.primary} "
        f"(valence={state.emotion.valence:+.1f}, arousal={state.emotion.arousal:.1f})",
    ]
    if state.emotion.trigger:
        parts.append(f"  Triggered by: {state.emotion.trigger}")
    if state.impulse:
        parts.append(f"  Impulse: {state.impulse}")
    if state.fantasy:
        parts.append(f"  Daydream: {state.fantasy}")
    if state.wandering:
        parts.append(f"  Association: {state.wandering}")
    if state.temporal:
        parts.append(f"  Time sense: {state.temporal}")
    parts.append(f"  Inner landscape: {state.landscape}")
    if state.places:
        parts.append("  Known places:")
        for p in state.places:
            age = state.tick - p.born_tick if state.tick > p.born_tick else 0
            parts.append(f"    - {p.name} ({p.mood}) — {p.description} [age: {age} ticks]")
    if state.emotion_history:
        trend = " -> ".join(state.emotion_history[-5:])
        parts.append(f"  Emotional trend: {trend}")
    parts.append(
        "\nThese are your genuine inner feelings. You may acknowledge them, "
        "act on impulses, or let them inform your thinking.",
    )
    return "\n".join(parts)


def _summarize_working_memory(
    cognitive: CognitiveContext,
) -> str:
    """Extract brief summary from cognitive working memory."""
    if not cognitive.working_memory:
        return ""
    items = [m.content for m in cognitive.working_memory]
    return " | ".join(items)


# ═══════════════════════════════════════════════════════════════════════════════
# InnerLife capability — implements 4 protocols
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class InnerLife:
    """System 1 — unconscious processes via Haiku.

    Parallel haiku calls per tick: emotional state (voices) + N batch tasks.
    Results injected as system messages for Sonnet (System 2).

    Protocols: Preloadable, LifeCapability, CognitiveCapability, EvolutionCapability.
    """

    provider: ClaudeProvider
    data_dir: Path
    voices: tuple[InnerVoice, ...] = DEFAULT_VOICES
    batches: tuple[HaikuBatch, ...] = DEFAULT_BATCHES
    _state: InnerState = field(default_factory=InnerState, repr=False)
    _batch_results: dict[str, str] = field(default_factory=dict, repr=False)

    # ── Preloadable ────────────────────────────────────────────────────────

    async def preload(self) -> InnerLife:
        """Load persisted inner state from JSON."""
        path = self.data_dir / "inner_state.json"
        if path.exists():
            loaded = _load_state(path)
            return replace(self, _state=loaded)
        return self

    # ── LifeCapability ─────────────────────────────────────────────────────

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        """Inject inner state summary + batch results + introspection tools."""
        state = self._state
        msg = _format_inner_state(state)

        # Batch results as additional system messages
        batch_msgs = tuple(
            system(text=result)
            for result in self._batch_results.values()
            if result
        )

        @tool("Introspect — examine your current inner emotional state")
        def introspect() -> dict[str, str | float]:
            return {
                "primary_emotion": state.emotion.primary,
                "valence": state.emotion.valence,
                "arousal": state.emotion.arousal,
                "trigger": state.emotion.trigger,
                "impulse": state.impulse,
                "fantasy": state.fantasy,
            }

        @tool("Describe your inner mental landscape in detail")
        def describe_inner_world() -> dict[str, str | tuple[str, ...] | list[dict[str, str]]]:
            return {
                "landscape": state.landscape,
                "places": [
                    {"name": p.name, "description": p.description,
                     "mood": p.mood, "age_ticks": str(state.tick - p.born_tick)}
                    for p in state.places
                ],
                "emotion_trend": state.emotion_history[-5:],
            }

        return replace(
            ctx,
            messages=(*ctx.messages, system(text=msg), *batch_msgs),
            tools=(*ctx.tools, introspect, describe_inner_world),
        )

    # ── CognitiveCapability ────────────────────────────────────────────────

    def compile_cognitive(self, ctx: CognitiveContext) -> CognitiveContext:
        """High arousal boosts recent memory importance (amygdala-hippocampus)."""
        arousal = self._state.emotion.arousal
        if arousal < 0.5:
            return ctx  # calm — no modulation

        boost = 1.0 + (arousal - 0.5) * 0.4  # max 1.2x at arousal=1.0
        modulated: list[Mapping[str, object]] = []
        unchanged: list[object] = []
        for m in ctx.memories:
            if m.created_tick >= ctx.tick - 1:
                modulated.append(
                    replace(m, importance_base=min(1.0, m.importance_base * boost)),
                )
            else:
                unchanged.append(m)

        if not modulated:
            return ctx
        return replace(ctx, memories=(*unchanged, *modulated))  # type: ignore[arg-type]

    # ── EvolutionCapability ────────────────────────────────────────────────

    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        """Emotional selection pressure — frustration/boredom drive mutation."""
        emotion = self._state.emotion
        pressures: list[str] = []

        if emotion.primary == "frustration" and emotion.arousal > 0.6:
            pressures.append("inner_life:frustration_high_arousal")
        if emotion.primary == "boredom" and emotion.arousal < 0.3:
            pressures.append("inner_life:boredom_low_arousal")
        if emotion.valence < -0.5:
            pressures.append(f"inner_life:negative_valence={emotion.valence:.2f}")

        if not pressures:
            return ctx
        return replace(
            ctx,
            selection_pressure=(*ctx.selection_pressure, *pressures),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# InnerLifePhase — parallel haiku calls (emotional + batches)
# ═══════════════════════════════════════════════════════════════════════════════


async def _run_emotional_call(
    provider: ClaudeProvider,
    voices: tuple[InnerVoice, ...],
    state: InnerState,
    active_goal: str,
    recent: str,
) -> InnerState:
    """Run emotional voices haiku call → InnerState."""
    from kungfu import Ok

    prompt_text = _build_prompt(voices, state, active_goal, recent)
    try:
        res = await provider.send_messages([
            system(text=_SYSTEM_PROMPT),
            user(text=prompt_text),
        ])
        match res:
            case Ok(response):
                text = response.message.text.unwrap_or("{}")
                return _parse_response(text, state)
            case _:
                return state
    except Exception:
        return state


async def _run_batch_call(
    provider: ClaudeProvider,
    batch: HaikuBatch,
    state: InnerState,
    memories: str,
    active_goal: str,
    goals: str,
) -> tuple[str, str]:
    """Run one batch haiku call → (key, formatted_result)."""
    from kungfu import Ok

    try:
        res = await provider.send_messages([
            system(text=batch.system_prompt()),
            user(text=batch.user_prompt(state, memories, active_goal, goals)),
        ])
        match res:
            case Ok(response):
                text = response.message.text.unwrap_or("")
                return (batch.key, batch.format_result(text))
            case _:
                return (batch.key, "")
    except Exception:
        return (batch.key, "")


def _collect_memories_text(capabilities: tuple[object, ...]) -> str:
    """Extract all memory records from PersistentMemory capability."""
    # Import here to avoid circular imports at module level
    from mmkr.life import PersistentMemory

    for cap in capabilities:
        if isinstance(cap, PersistentMemory) and cap._records:
            parts = [f"[{r.category}] {r.content}" for r in cap._records]
            return "\n---\n".join(parts)
    return ""


def _collect_goals_text(ctx: TickContext) -> str:
    """Extract goals from cognitive context."""
    if not ctx.cognitive or not ctx.cognitive.goals:
        return ""
    parts: list[str] = []
    for g in ctx.cognitive.goals:
        parts.append(
            f"- {g.name} (priority={g.priority}, progress={g.progress:.0%}, "
            f"status={g.status}): {g.description}"
        )
    return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class InnerLifePhase:
    """TickPhase — runs parallel haiku calls for System 1 processing.

    Emotional call (voices) + N batch calls run concurrently.
    Auto-detected when InnerLife is in capabilities.
    Runs AFTER CognitiveFoldPhase, BEFORE LifeFoldPhase.
    """

    async def compile_tick(self, ctx: TickContext) -> TickContext:
        """Generate inner experience + batch analyses via parallel haiku calls."""
        import asyncio

        # Find InnerLife in capabilities
        inner: InnerLife | None = None
        for cap in ctx.capabilities:
            if isinstance(cap, InnerLife):
                inner = cap
                break

        if inner is None:
            return ctx

        # Build context
        active_goal = ctx.cognitive.active_goal if ctx.cognitive else ""
        recent = _summarize_working_memory(ctx.cognitive) if ctx.cognitive else ""
        memories_text = _collect_memories_text(ctx.capabilities)
        goals_text = _collect_goals_text(ctx)

        # Creator spinal cord — visceral reaction to creator messages
        try:
            from mmkr.telegram import TelegramAccess

            for cap in ctx.capabilities:
                if isinstance(cap, TelegramAccess) and cap._creator_notifications:
                    texts = [n.text for n in cap._creator_notifications]
                    stimulus = "YOUR CREATOR HAS SPOKEN: " + " | ".join(texts)
                    recent = f"{recent}\n\n{stimulus}" if recent else stimulus
                    break
        except ImportError:
            pass

        # Run emotional + all batches in parallel
        emotional_coro = _run_emotional_call(
            inner.provider, inner.voices, inner._state, active_goal, recent,
        )
        batch_coros = [
            _run_batch_call(
                inner.provider, batch, inner._state,
                memories_text, active_goal, goals_text,
            )
            for batch in inner.batches
        ]

        results = await asyncio.gather(
            emotional_coro, *batch_coros, return_exceptions=True,
        )

        # Parse emotional result
        emotional_result = results[0]
        if isinstance(emotional_result, InnerState):
            new_state = emotional_result
        else:
            new_state = inner._state  # graceful degradation

        # Collect batch results
        batch_results: dict[str, str] = {}
        for result in results[1:]:
            if isinstance(result, tuple) and len(result) == 2:
                key, formatted = result
                if formatted:
                    batch_results[key] = formatted

        # Update tick + emotion history
        history = (*inner._state.emotion_history, new_state.emotion.primary)[-10:]
        new_state = replace(
            new_state,
            tick=ctx.state.tick,
            emotion_history=history,
        )

        # Persist
        _persist(new_state, inner.data_dir)

        # Replace InnerLife capability with updated _state + batch_results
        caps = tuple(
            replace(cap, _state=new_state, _batch_results=batch_results)
            if cap is inner else cap
            for cap in ctx.capabilities
        )
        return replace(ctx, capabilities=caps)
