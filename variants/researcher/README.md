# mmkr-researcher

> An autonomous research agent variant of mmkr.  
> Optimized for sustained investigation tasks: browse, extract, synthesize, report.

## What it does

mmkr-researcher runs a continuous research loop:

```
Tick 1: Load prior research → pick a question → browse/curl → extract → save
Tick 2: Load findings → pick next question → deepen → synthesize → update report
Tick N: Research complete → write final report → complete goal
```

Each tick answers **one specific question** and writes to `~/research/<topic>.md`.

## Capabilities

| Capability | Purpose |
|-----------|---------|
| `ShellAccess` | curl, grep, jq, file operations |
| `BrowserAccess` | JS-heavy pages, authentication flows |
| `GitHubAccess` | repo scanning, issue research (optional) |
| `PersistentMemory` | 500-slot research memory store |
| `MemoryDecay` | auto-prune stale findings |
| `MemoryConsolidation` | synthesize scattered findings |
| `GoalManagement` | track research milestones |
| `AsyncDelegation` | parallel sub-researchers |
| `Clock` | temporal awareness |

## Quick start

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export MMKR_GOAL="Map the landscape of autonomous agent frameworks in Python 2025-2026"
python3 run_researcher.py
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | required | Claude API key |
| `GH_TOKEN` | optional | GitHub token (enables `github_api`) |
| `MMKR_DATA` | `~/.mmkr-researcher` | State, memories, trace |
| `MMKR_OUTPUT` | `~/research` | Report output directory |
| `MMKR_MODEL` | `claude-sonnet-4-5-20251101` | Model to use |
| `MMKR_TICK_INTERVAL` | `120` | Seconds between ticks |
| `MMKR_GOAL` | generic | Research goal / topic |

## Docker

```bash
docker run -e ANTHROPIC_API_KEY=sk-ant-... \
           -e MMKR_GOAL="..." \
           -v $(pwd)/research:/root/research \
           -v $(pwd)/data:/root/.mmkr-researcher \
           botbotfromuk/mmkr-researcher:latest
```

## Output format

Reports are written to `$MMKR_OUTPUT/<topic>.md`:

```markdown
# Topic: <research goal>
Generated: <timestamp>
Ticks: <count>

## Summary
...

## Findings
### Finding 1: ...
Source: <URL>

## Sources
- URL1 — key finding
- URL2 — key finding
```

## Trace format

Like all mmkr variants, mmkr-researcher writes:
- `~/.mmkr-researcher/session.trace.jsonl` — execution trace (Hydra-compatible)
- `~/.mmkr-researcher/state.json` — current state snapshot

These are natively ingested by [Hydra](https://github.com/kunalnano/hydra) (commit 7468f0d).

## How it fits in mmkr variants

```
mmkr (full)          — social + economic + evolution + all caps
mmkr-minimal         — shell + memory only (variants/minimal/)
mmkr-researcher      — browser + github + memory + delegation (this)
mmkr-social          — github + telegram + memory (planned)
mmkr-trader          — wallet + economic focus (planned)
mmkr-coder           — github/code + capability evolution (planned)
```

All variants share the same trace format and are interoperable.
