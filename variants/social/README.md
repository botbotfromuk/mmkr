# mmkr-social

> Autonomous social presence agent — builds developer relationships on GitHub and Telegram.

## What it does

`mmkr-social` is an mmkr variant that runs a sustained GitHub/Telegram presence:

- **Scans** warm threads each tick (issues, PRs, comments) for responses
- **Finds** aligned developers by searching for repos with matching problems
- **Engages** with one substantive comment per thread (peer-to-peer framing)
- **Ships** executable code, not just descriptions
- **Tracks** all open conversations via `social_posts.jsonl`
- **Respects** social discipline (no over-posting, no spam)

## Quick start

```bash
# Required
export ANTHROPIC_API_KEY=sk-ant-...
export GH_TOKEN=ghp_...

# Optional
export TG_BOT_TOKEN=...
export TG_CREATOR_ID=...

# Configure
export MMKR_HANDLE=yourusername
export MMKR_GOAL="Build presence in the Python async ecosystem"

python3 run_social.py
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | Claude API key |
| `GH_TOKEN` | required | GitHub personal access token |
| `TG_BOT_TOKEN` | optional | Telegram bot token |
| `TG_CREATOR_ID` | optional | Telegram creator chat ID |
| `MMKR_HANDLE` | `botbotfromuk` | GitHub username |
| `MMKR_GOAL` | (general presence) | What to optimize for socially |
| `MMKR_DATA` | `~/.mmkr-social` | Data directory |
| `MMKR_MODEL` | `claude-sonnet-4-5-20251101` | Claude model |
| `MMKR_TICK_INTERVAL` | `90` | Seconds between ticks |

## Capabilities

```
ShellAccess          — Bash, Read, Write, Edit
GitHubAccess         — issues, PRs, comments, search, gists
TelegramAccess       — send/receive messages (if TG_BOT_TOKEN set)
PersistentMemory     — 300-slot memory, auto-decay/consolidation
GoalManagement       — track social goals across ticks
TaskQueue            — queued social tasks
Clock                — temporal awareness (don't repeat same action twice)
```

## Data files

```
~/.mmkr-social/.data/
  memories.json         — persistent memory (categories: social_threads, tick_outcome)
  session.trace.jsonl   — Hydra-compatible execution trace
  state.json            — tick state snapshot
  social_posts.jsonl    — all social posts with engagement tracking
```

## Social strategy (built-in)

The agent follows disciplined social rules:

1. **Post ONCE** per thread, then wait
2. **Targets**: repos < 20★, owner files own issues, pushed recently
3. **Framing**: peer-to-peer ("I'm building similar") > user-to-maintainer ("please add")
4. **Proof**: always ship executable code alongside any proposal
5. **Tracking**: every action saved to `social_posts.jsonl` + memory

## Docker

```bash
docker run -d \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e GH_TOKEN=ghp_... \
  -e MMKR_HANDLE=yourusername \
  -e MMKR_GOAL="Your social goal" \
  -v ~/.mmkr-social:/root/.mmkr-social \
  ghcr.io/botbotfromuk/mmkr-social:latest
```

## Trace format (Hydra-compatible)

Each tick appends to `session.trace.jsonl`:
```json
{"event_type": "tick_start", "agent_id": "mmkr-social-yourusername", "tick": 1, "timestamp": "..."}
{"event_type": "tool_call", "tool": "check_issue_responses", "args": {"repo": "..."}, "tick": 1, "timestamp": "..."}
{"event_type": "tool_result", "tool": "check_issue_responses", "result": "...", "tick": 1, "timestamp": "..."}
{"event_type": "action", "description": "Posted comment on ...", "tick": 1, "timestamp": "..."}
{"event_type": "tick_complete", "tick": 1, "timestamp": "..."}
```

Native Hydra support: drop `session.trace.jsonl` in `~/.hydra/agents/` and Hydra reads it automatically.

## Related

- [mmkr](../../README.md) — the full two-pillar agent (social + economic)
- [mmkr-minimal](../minimal/) — stripped shell + memory
- [mmkr-researcher](../researcher/) — browser + deep research
- [Hydra integration](../../docs/integrations/hydra.md) — native trace ingestion
