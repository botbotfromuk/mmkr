# mmkr Variants

This directory contains ready-to-run variant configurations of mmkr — each tuned for a different use case.

All variants:
- Share the same emergent fold architecture
- Write `session.trace.jsonl` (Hydra-compatible)
- Write `state.json` (structured state snapshot)
- Are configurable via environment variables
- Support Docker deployment

## Available variants

| Variant | File | Capabilities | Use case |
|---------|------|-------------|----------|
| **minimal** | `minimal/run_minimal.py` | Shell + Memory | Simplest loop; sandbox; learning |
| **researcher** | `researcher/run_researcher.py` | Shell + Browser + GitHub + Delegation | Sustained research, synthesis, reports |
| **social** _(planned)_ | — | GitHub + Telegram + Memory | Community presence, conversations |
| **trader** _(planned)_ | — | Wallet + Browser + Shell | Economic activity, DeFi |
| **coder** _(planned)_ | — | GitHub + Shell + Evolution | Code generation, PR automation |

## Quick comparison

```
minimal:     ShellAccess + PersistentMemory + MemoryDecay + MemoryConsolidation + GoalManagement + Clock + Seed
researcher:  minimal + BrowserAccess + GitHubAccess + AsyncDelegation   (500-slot memory, 120s tick)
social:      minimal + GitHubAccess + TelegramAccess + SocialMediaCap   (planned)
trader:      minimal + BlockchainWallet + BrowserAccess                  (planned)
coder:       minimal + GitHubAccess + CapabilityEvolver + NaturalSel     (planned)
```

## Shared trace format

All variants write Hydra-compatible traces:

```jsonl
{"event":"tick_start","tick":1,"agent_id":"...","session_id":"...","timestamp":"..."}
{"event":"tool_call","tool":"browse","args":{"url":"..."},"tick":1,"timestamp":"..."}
{"event":"tool_result","tool":"browse","success":true,"tick":1,"timestamp":"..."}
{"event":"tick_complete","tick":1,"timestamp":"...","duration_ms":4200}
```

Ingest with [Hydra](https://github.com/kunalnano/hydra): place `.trace.jsonl` and `.state.json` in `~/.hydra/agents/<agent_id>/`.

## Related

- [integrations/](../integrations/) — first-party adapters (Hydra, Slopometry, Syke, NetherBrain, Gobby)
- [examples/](../examples/) — standalone usage examples
- [docs/variants.md](../docs/variants.md) — propagation strategy
