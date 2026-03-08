# mmkr Integrations

> Each integration is a first-party adapter between mmkr and an external tool.

| Integration | Status | Module |
|---|---|---|
| [Hydra](hydra.md) | ✅ **Native support shipped** (commit 7468f0d) | `integrations/hydra_ingestor.py` |
| [NetherBrain](netherbrain.md) | 🟡 Adapter built, design discussion open | `integrations/netherbrain_adapter.py` |
| [Slopometry](slopometry.md) | 🟡 Adapter built, awaiting response | `integrations/slopometry_collector.py` |
| [Syke](syke.md) | 🟡 Adapter built, awaiting response | `integrations/syke_adapter.py` |
| [llmify](../integrations/llmify/README.md) | ✅ Included | `integrations/llmify/` |

## Status Legend

- ✅ **Shipped** — integration is live and functional in the external project
- 🟡 **Adapter built** — mmkr side complete; waiting for external project to adopt
- 🔴 **Planned** — not yet built

## Pattern

All adapters follow the same pattern:
1. Read mmkr's on-disk state (`.trace.jsonl` + `.data/memories.json`)
2. Transform into the external tool's format
3. Write/send to the external tool's ingestion endpoint

Some adapters also support running **standalone** (no external tool installed).

## Adding a New Integration

See `docs/architecture.md` for the trace schema.
The simplest integration needs only `read_mmkr_events()` from `syke_adapter.py` as a reference.
