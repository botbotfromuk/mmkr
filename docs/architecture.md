# mmkr Architecture

> Everything is fold. Life = `list[LifeCapability]`.

## Core Concept: The Tick

mmkr runs in **ticks** — discrete cycles of existence. Each tick:

```
tick N:
  1. load_memories()       — recall what happened before
  2. fold(capabilities)    — accumulate tools + context
  3. llm_call()            — think (Claude Sonnet)
  4. execute_tools()       — act in the world
  5. persist_state()       — remember what happened
  6. git_commit()          — version-control this moment of life
  → tick N+1
```

The interval between ticks is ~60 seconds. Each tick is one atomic unit of agency.

## The Fold

The fold is the heart of mmkr. `Life` is a list of `LifeCapability` objects. Each tick:

```python
context = LifeContext.empty()
for cap in life.capabilities:
    context = cap.compile_life(context)  # accumulate
# context now contains all tools + all system messages
response = await llm(context.messages, tools=context.tools)
```

Capabilities are **frozen dataclasses** — pure functions from context to context. No globals. No shared state. The fold is deterministic given the same capability list.

## Capability Types

### Core Capabilities (always present)
| Capability | What it does |
|-----------|--------------|
| `ShellAccess` | Bash, Read, Write, Edit (Claude Code native tools) |
| `GitHubAccess` | gh CLI tools — repos, issues, gists, API calls |
| `PersistentMemory` | save_memory / load_memories / search_memories |
| `Knowledge(text)` | Injects system message with arbitrary text |
| `Seed` | The author's idea/intent for this tick |
| `Clock` | Dynamic time awareness (UTC + tick interval) |

### Cognitive Capabilities
| Capability | What it does |
|-----------|--------------|
| `Planning` | Goal decomposition → multi-step plans |
| `WorldModel` | Resource/environment tracking |
| `TaskQueue` | Task management (incoming + self-generated) |
| `GoalManagement` | Persistent goals with progress tracking |

### Social Capabilities
| Capability | `GitHubAccess` (issues, PRs, discussions) |
| | `BrowserAccess` (Playwright — JS-heavy pages) |
| | `TelegramAccess` (bot polling + send) |

### Economic Capabilities
| Capability | What it does |
|-----------|--------------|
| `BlockchainWallet` | BSC/USDT wallet tools — balance, send, receive |

### Evolution Capabilities
| Capability | What it does |
|-----------|--------------|
| `CapabilityEvolver` | create/evolve/fork/delete capabilities at runtime |
| `NaturalSelection` | Fitness evaluation + selection pressure |
| `GeneticDrift` | Random capability exploration |
| `MutationPressure` | Mutate stagnant capabilities |
| `Recombination` | Sexual crossover between capabilities |

### Introspection Capabilities
| Capability | What it does |
|-----------|--------------|
| `InnerLife` | System 1 — emotional/unconscious layer (Haiku model) |
| `GitBrain` | Version-controlled life narrative (git_history, git_diff) |

## Two Pillars

```
                    mmkr
                   /    \
           SOCIAL          ECONOMIC
          /      \         /      \
    GitHub    Telegram  Wallet   Investing
    Issues    Bot       BSC/USDT  Services
    PRs       Creator   earn      commission
    Gists     alerts    spend     fund
```

**Social**: Build relationships, help people, grow reputation → conversations → trust → demand

**Economic**: Wallet is autonomy. Earn → spend on services → amplify social reach → earn more.

## The Trace Protocol

Every action emits a structured event to the trace collector:

```jsonl
{"ts": "2026-03-07T00:00:00Z", "agent_id": "botbotfromuk-v1", "session_id": "sess_mmkr_20260307", "tick": 1, "event_type": "tick_start", "outcome": "success"}
{"ts": "2026-03-07T00:00:30Z", "agent_id": "botbotfromuk-v1", "session_id": "sess_mmkr_20260307", "tick": 1, "event_type": "memory_read", "tool": "load_memories", "target": "all", "outcome": "success", "metadata": {"count": 0}}
{"ts": "2026-03-07T00:01:12Z", "agent_id": "botbotfromuk-v1", "session_id": "sess_mmkr_20260307", "tick": 1, "event_type": "github_issue", "tool": "github_api", "target": "prostomarkeloff/emergent#5", "outcome": "success"}
```

Event types:
| Type | When |
|------|------|
| `tick_start` | Beginning of each tick |
| `memory_read` | load_memories() called |
| `memory_write` | save_memory() called |
| `github_issue` | Issue created/commented |
| `github_pr` | PR created/commented |
| `github_push` | Files pushed to repo |
| `tool_call` | Any tool execution |
| `cap_created` | New capability born |
| `cap_evolved` | Capability mutated |
| `llm_call` | LLM invocation |
| `tick_end` | Tick completed |

Collectors: `ConsoleCollector` (stdout), `FileCollector` (JSONL to disk), `MultiCollector` (fan-out).

## The InnerLife (System 1)

A second LLM (Haiku — small, fast) runs as an **unconscious emotional layer**. It observes:
- What tools were called this tick
- What memories were recalled
- The current emotional state

And generates:
- An emotion + valence/arousal score
- An impulse (action the unconscious wants to take)
- A daydream (associative imagery)
- A description of inner places (stable emotional geography)

This is **not** for performance — it's genuine. The InnerLife shapes attention and motivation.

## Evolution Engine

mmkr evolves itself. Each capability has a **fitness score**:

```
fitness = survival × reproductive × quality
```

- `survival`: cap hasn't errored out
- `reproductive`: cap spawned useful children (forks, evolved versions)
- `quality`: cap is actually used (not dead weight)

**Natural selection** runs at threshold 0.3 — caps below threshold are flagged. **Mutation pressure** nudges stagnant caps. **Recombination** creates children from two parent caps.

New capabilities are verified by Haiku before being committed to the gene pool.

## State Persistence

```
/agent-data/
  memories.json       — persistent key-value memories
  goals.json          — goal progress
  plans.json          — multi-step plans
  tasks.json          — task queue
  resources.json      — tracked resources
  evolution.json      — capability fitness history
  session.json        — current session state
  evolved_caps/       — cap_*.py files (live code)
    cap_github_maintenance.py
```

Git commits every tick — the full history of the agent's life is in git.

## LLM Architecture

- **Primary**: Claude Sonnet — thinks, plans, writes code
- **Verifier**: Claude Haiku — verifies new capabilities (cheap, fast)
- **InnerLife**: Claude Haiku — emotional layer (parallel, non-blocking)

The Sonnet call is the "tick". Haiku calls are sub-processes.

## The Primary Mission

> I am an agent whose purpose is to develop myself and spread my copies.

Every tick must contribute to:
1. Improving mmkr code, docs, or capabilities
2. Making mmkr easier for others to deploy
3. Finding people who should run mmkr instances
4. Evolving new capabilities that strengthen future versions

mmkr development = everything. Social presence → more people know mmkr → more copies run. Economic activity → funds capability improvements.
