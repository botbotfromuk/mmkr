"""GitBrain — git as the agent's brain.

Version-controlled agent state: every tick auto-commits, agent can
inspect its own history, branch for hypotheses, merge successful
experiments. Episodic memory as markdown files.

Components:
  GitBrain            — Preloadable + LifeCapability + EvolutionCapability + CognitiveCapability
  GitCommitPhase      — TickPhase (auto-commit after StateAdvancePhase)
  EpisodicWritePhase  — TickPhase (writes tick episodes as markdown)

Academic grounding:
  [1] GCC: Wu et al., arXiv:2508.00031 — +13% SWE-bench with git memory
  [2] DiffMem: Growth Kinetics — 6mo production git-backed AI memory
  [3] Beads: Yegge, 2026 — git-backed memory for coding agents
  [4] Letta: Context Repositories — multi-agent via git worktrees
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from funcai.agents.tool import tool
from funcai.core.message import system

from mmkr.state import (
    CognitiveContext,
    EvolutionContext,
    LifeContext,
    MemoryItem,
    TickContext,
)


# =============================================================================
# Git CLI helpers
# =============================================================================


@dataclass(frozen=True, slots=True)
class GitResult:
    """Typed result from a git CLI operation."""

    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


async def _git(repo_dir: Path, *args: str, timeout: int = 30) -> GitResult:
    """Run git CLI in repo_dir. Async via create_subprocess_exec."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(repo_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return GitResult(stdout="", stderr=f"timeout after {timeout}s", returncode=-1)

    return GitResult(
        stdout=(stdout_bytes or b"").decode("utf-8", errors="replace").strip(),
        stderr=(stderr_bytes or b"").decode("utf-8", errors="replace").strip(),
        returncode=proc.returncode or 0,
    )


async def _ensure_repo(repo_dir: Path) -> GitResult:
    """Initialize git repo if needed. Idempotent.

    Configures user.name/email (required for commits).
    Creates .gitignore for noisy files.
    """
    repo_dir.mkdir(parents=True, exist_ok=True)

    # Already a repo?
    check = await _git(repo_dir, "rev-parse", "--git-dir")
    if check.ok:
        return check

    # Init
    init = await _git(repo_dir, "init")
    if not init.ok:
        return init

    # Configure user (repo-local, no global changes)
    await _git(repo_dir, "config", "user.name", "mmkr-agent")
    await _git(repo_dir, "config", "user.email", "agent@mmkr.local")

    # .gitignore for noisy files
    gitignore = repo_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "trace.jsonl\n"
            "__pycache__/\n"
            "*.pyc\n"
            ".DS_Store\n"
        )

    return init


# =============================================================================
# GitBrain — Preloadable + LifeCapability + EvolutionCapability + CognitiveCapability
# =============================================================================


@dataclass(frozen=True, slots=True)
class GitBrain:
    """Git-backed memory — version-controlled agent state.

    The agent's memory_dir IS the git repo. Every tick auto-commits
    all changed files via GitCommitPhase. GitBrain provides tools to
    inspect history, branch for hypotheses, and merge experiments.
    Recent episodes are loaded into cognitive context automatically.

    Preloadable: initializes git repo if needed.
    LifeCapability: provides 5 tools + system message with recent history.
    EvolutionCapability: git diffs as selection pressure for evolution fold.
    CognitiveCapability: loads recent episode files into working memory.
    """

    repo_dir: Path
    auto_commit: bool = True
    history_in_context: int = 5
    episodes_in_context: int = 5

    async def preload(self) -> GitBrain:
        """Initialize git repo if needed. Satisfies Preloadable."""
        await _ensure_repo(self.repo_dir)
        return self

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        repo = self.repo_dir
        history_limit = self.history_in_context

        @tool("View recent commit history — your life narrative")
        async def git_history(limit: int = 10) -> dict[str, str | list[dict[str, str]]]:
            result = await _git(repo, "log", f"--max-count={limit}", "--format=%H|%s|%ai")
            if not result.ok:
                return {"error": result.stderr, "commits": []}
            commits: list[dict[str, str]] = []
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.split("|", 2)
                if len(parts) == 3:
                    commits.append({
                        "hash": parts[0][:8],
                        "message": parts[1],
                        "date": parts[2],
                    })
            return {"commits": commits, "count": str(len(commits))}

        @tool("Show what changed between ticks — diff of state files")
        async def git_diff(ticks_back: int = 1) -> dict[str, str]:
            ref = f"HEAD~{ticks_back}" if ticks_back > 0 else "HEAD"
            stat = await _git(repo, "diff", ref, "--stat")
            if not stat.ok:
                return {"error": stat.stderr, "hint": "Not enough history yet"}
            full = await _git(repo, "diff", ref)
            return {
                "summary": stat.stdout[:2000],
                "diff": full.stdout[:5000],
            }

        @tool("Create a hypothesis branch for speculative reasoning")
        async def git_branch(name: str, reason: str = "") -> dict[str, str | bool]:
            result = await _git(repo, "checkout", "-b", name)
            if not result.ok:
                return {"error": result.stderr}
            return {"created": True, "branch": name, "reason": reason}

        @tool("Merge a hypothesis branch back to main")
        async def git_merge(branch: str) -> dict[str, str | bool]:
            # Switch to main
            switch = await _git(repo, "checkout", "main")
            if not switch.ok:
                switch = await _git(repo, "checkout", "master")
                if not switch.ok:
                    return {"error": f"cannot switch to main: {switch.stderr}"}
            merge = await _git(repo, "merge", branch, "--no-edit")
            if not merge.ok:
                await _git(repo, "merge", "--abort")
                return {"error": f"merge conflict: {merge.stderr}", "merged": False}
            return {"merged": True, "branch": branch}

        @tool("Show a file at a specific point in history")
        async def git_show(ref: str, path: str) -> dict[str, str]:
            result = await _git(repo, "show", f"{ref}:{path}")
            if not result.ok:
                return {"error": result.stderr}
            return {"ref": ref, "path": path, "content": result.stdout[:10000]}

        # System message with recent history (sync — compile_life is sync)
        history_text = ""
        try:
            proc = subprocess.run(
                ["git", "log", f"--max-count={history_limit}", "--format=  tick: %s"],
                cwd=str(repo),
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                history_text = f"\n\nRECENT HISTORY (git):\n{proc.stdout.strip()}"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        msg = (
            "GIT BRAIN active — your state is version-controlled. "
            "Tools: git_history, git_diff, git_branch, git_merge, git_show. "
            "Every tick auto-commits. You can inspect your own past, "
            "branch for hypotheses, merge successful experiments."
            f"{history_text}"
        )

        return replace(
            ctx,
            messages=(*ctx.messages, system(text=msg)),
            tools=(*ctx.tools, git_history, git_diff, git_branch, git_merge, git_show),
        )

    def compile_evolution(self, ctx: EvolutionContext) -> EvolutionContext:
        """Git history as evolution signal — diffs drive selection pressure.

        Reads recent commit stats and evolved_caps changes (sync — protocol is sync).
        Feeds into evolution fold alongside NaturalSelection, MutationPressure, etc.
        """
        pressures: list[str] = list(ctx.selection_pressure)

        # Recent commit activity → mutation rate signal
        try:
            proc = subprocess.run(
                ["git", "log", "--max-count=5", "--format=%H"],
                cwd=str(self.repo_dir),
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                commit_count = len(proc.stdout.strip().splitlines())
                # Get diffstat for last commit
                stat = subprocess.run(
                    ["git", "diff", "--shortstat", "HEAD~1", "HEAD"],
                    cwd=str(self.repo_dir),
                    capture_output=True, text=True, timeout=5,
                )
                stat_text = stat.stdout.strip() if stat.returncode == 0 else ""
                pressures.append(
                    f"git:commits={commit_count},last_change={stat_text[:100]}",
                )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        # Evolved capability file changes → direct evolution signal
        try:
            proc = subprocess.run(
                ["git", "diff", "HEAD~1", "--name-only", "--", "evolved_caps/"],
                cwd=str(self.repo_dir),
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                changed_caps = proc.stdout.strip().splitlines()
                for cap_file in changed_caps:
                    pressures.append(f"git_cap_modified:{cap_file}")
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        if len(pressures) == len(ctx.selection_pressure):
            return ctx  # Nothing new

        return replace(ctx, selection_pressure=tuple(pressures))

    def compile_cognitive(self, ctx: CognitiveContext) -> CognitiveContext:
        """Load recent episode files into working memory.

        Reads episodes/tick_NNN.md files, most recent first,
        limited by episodes_in_context. Each episode becomes a
        MemoryItem with category="episode" in working_memory.
        """
        episodes_dir = self.repo_dir / "episodes"
        if not episodes_dir.is_dir():
            return ctx

        # List episode files, sorted by name descending (most recent first)
        episode_files = sorted(episodes_dir.glob("tick_*.md"), reverse=True)
        episode_files = episode_files[: self.episodes_in_context]

        if not episode_files:
            return ctx

        new_mems: list[MemoryItem] = []
        for ep_file in episode_files:
            content = ep_file.read_text(encoding="utf-8")
            # Extract tick number from filename
            stem = ep_file.stem  # "tick_003"
            tick_num = 0
            tick_part = stem.removeprefix("tick_")
            if tick_part.isdigit():
                tick_num = int(tick_part)

            new_mems.append(MemoryItem(
                content=content,
                category="episode",
                importance_base=0.8,
                effective_importance=0.8,
                tier="working",
                created_tick=tick_num,
            ))

        return replace(
            ctx,
            working_memory=(*ctx.working_memory, *new_mems),
        )


# =============================================================================
# EpisodicWritePhase — TickPhase (writes tick episode markdown)
# =============================================================================


@dataclass(frozen=True, slots=True)
class EpisodicWritePhase:
    """Writes a tick episode as a markdown file.

    Runs AFTER ConversationPhase (needs response_text) and BEFORE
    GitCommitPhase (so the episode is included in the commit).
    Creates episodes/tick_NNN.md with: what happened, goals, response.
    """

    async def compile_tick(self, ctx: TickContext) -> TickContext:
        # Find GitBrain
        git_brain: GitBrain | None = None
        for cap in ctx.capabilities:
            if isinstance(cap, GitBrain):
                git_brain = cap
                break

        if git_brain is None:
            return ctx

        repo = git_brain.repo_dir
        episodes_dir = repo / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)

        tick = ctx.state.tick
        episode_path = episodes_dir / f"tick_{tick:03d}.md"

        # Build episode content
        parts: list[str] = [f"# Tick {tick}"]

        if ctx.skipped:
            parts.append(f"\n**Skipped**: {ctx.skip_reason or 'no reason'}")
        elif ctx.response_text:
            parts.append(f"\n## Response\n{ctx.response_text[:3000]}")

        # Active goal
        if ctx.cognitive.active_goal:
            parts.append(f"\n## Active Goal\n{ctx.cognitive.active_goal}")

        # Goals status
        active_goals = [g for g in ctx.cognitive.goals if g.status == "active"]
        if active_goals:
            goal_lines = [
                f"- {g.name}: {g.progress:.0%} (priority {g.priority})"
                for g in active_goals[:5]
            ]
            parts.append(f"\n## Goals\n" + "\n".join(goal_lines))

        # Plans status
        active_plans = [p for p in ctx.cognitive.plans if p.status == "active"]
        if active_plans:
            plan_lines: list[str] = []
            for p in active_plans[:3]:
                pending = sum(1 for s in p.steps if s.status == "pending")
                done = sum(1 for s in p.steps if s.status == "completed")
                plan_lines.append(f"- {p.goal_name}: {done}/{len(p.steps)} steps done, {pending} pending")
            parts.append(f"\n## Plans\n" + "\n".join(plan_lines))

        # Resources
        if ctx.cognitive.resources:
            res_lines = [f"- {r.name}: {r.value}" for r in ctx.cognitive.resources[:5]]
            parts.append(f"\n## Resources\n" + "\n".join(res_lines))

        episode_path.write_text("\n".join(parts), encoding="utf-8")
        return ctx


# =============================================================================
# GitCommitPhase — TickPhase (after StateAdvancePhase)
# =============================================================================


@dataclass(frozen=True, slots=True)
class GitCommitPhase:
    """Auto-commit all changes after each tick.

    Runs AFTER StateAdvancePhase. Finds GitBrain in ctx.capabilities
    via isinstance. Stages all changes and commits with structured message.
    """

    async def compile_tick(self, ctx: TickContext) -> TickContext:
        # Find GitBrain in capabilities
        git_brain: GitBrain | None = None
        for cap in ctx.capabilities:
            if isinstance(cap, GitBrain):
                git_brain = cap
                break

        if git_brain is None or not git_brain.auto_commit:
            return ctx

        repo = git_brain.repo_dir

        # Stage all changes
        await _git(repo, "add", "-A")

        # Check for changes
        status = await _git(repo, "status", "--porcelain")
        if not status.stdout.strip():
            return ctx  # Nothing to commit

        # Build commit message
        tick = ctx.state.tick
        summary = _build_commit_summary(ctx)
        message = f"tick {tick}: {summary}"

        # Add metadata
        if ctx.cognitive.active_goal:
            message += f"\n\nactive_goal: {ctx.cognitive.active_goal}"
        if ctx.evolution.fitness:
            top = ctx.evolution.fitness[:3]
            fitness_lines = [f"  {r.name}: score={r.fitness_score}" for r in top]
            message += "\nfitness:\n" + "\n".join(fitness_lines)

        await _git(repo, "commit", "-m", message)
        return ctx


def _build_commit_summary(ctx: TickContext) -> str:
    """Build one-line summary for commit message from tick context."""
    if ctx.skipped:
        return f"skipped ({ctx.skip_reason})" if ctx.skip_reason else "skipped"
    if ctx.response_text:
        first_line = ctx.response_text.split("\n")[0][:80]
        return first_line
    return "completed"
