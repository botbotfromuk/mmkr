# mmkr-trader

Economic pillar variant of mmkr. Focused on wallet management, DeFi monitoring, earning opportunities, and financial intelligence.

## What it does

- **Monitors wallet** (BNB Smart Chain, USDT) — checks balance and transactions each tick
- **Tracks opportunities** — researches yield protocols, service opportunities, earning potential
- **Manages economics** — keeps a ledger, tracks income/expense, builds economic autonomy
- **Offers services** — can generate payment requests, accept USDT for tasks

## Capabilities

| Capability | Purpose |
|---|---|
| `ShellAccess` | Run commands, execute scripts |
| `BlockchainWallet` | BNB Smart Chain wallet operations |
| `BrowserAccess` | Research DeFi protocols, market data |
| `PersistentMemory` | Economic ledger, opportunity log |
| `MemoryDecay` | Forget stale price data |
| `MemoryConsolidation` | Consolidate economic patterns |
| `GoalManagement` | Track economic goals and milestones |
| `TaskQueue` | Queue economic tasks across ticks |
| `Clock` | Tick timing |
| `Seed` | Economic identity and principles |

## Configuration

```bash
# Required
export ANTHROPIC_API_KEY="sk-ant-..."

# Wallet (BNB Smart Chain)
export WALLET_ADDRESS="0x..."
export WALLET_PRIVATE_KEY="0x..."  # for sending transactions
# OR:
export WALLET_MNEMONIC="word1 word2 ..."

# Optional
export MMKR_MODEL="claude-3-5-haiku-20241022"
export MMKR_TICK_INTERVAL="120"          # 2 minutes (economic cycles are slower)
export MMKR_DATA="~/.mmkr-trader/"
export MMKR_GOAL="Build $1000 USDT by offering AI services"
```

## Run

```bash
# Direct
python3 run_trader.py

# Docker
docker build -t mmkr-trader .
docker run -d \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -e WALLET_ADDRESS=$WALLET_ADDRESS \
  -e WALLET_PRIVATE_KEY=$WALLET_PRIVATE_KEY \
  --name mmkr-trader \
  mmkr-trader
```

## Economic Strategy

The trader variant follows a three-phase economic loop:

### Phase 1: Assessment (every tick)
```
wallet_balance() → how much do I have?
wallet_transactions() → what happened recently?
memories (economics) → what opportunities do I know about?
```

### Phase 2: Opportunity scan (every 5 ticks)
```
browse(pancakeswap) → current APY on stablecoin pools
browse(venus.io) → lending rates
search for "hire AI agent" threads → service opportunities
```

### Phase 3: Action (when opportunity found)
```
IF yield opportunity > 15% APY AND balance > $10:
  → evaluate risk, consider allocation

IF service request found:
  → respond with wallet_payment_request()
  → complete task
  → verify payment with wallet_transactions()
```

## Trace Format

All ticks write Hydra-compatible JSONL to `~/.mmkr-trader/session.trace.jsonl`.

Compatible with [Hydra](https://github.com/kunalnano/hydra) native ingestion.
Drop files in `~/.hydra/agents/` for live timeline view.

## Data Layout

```
~/.mmkr-trader/
├── memories.json      # economic ledger + opportunity log
├── goals.json         # financial goals with progress
├── tasks.json         # queued economic tasks
├── state.json         # tick state + heartbeat
└── session.trace.jsonl # execution trace
```

## Memory Categories

| Category | Content |
|---|---|
| `economics` | Balance history, income/expense ledger |
| `opportunities` | DeFi yields, service requests |
| `transactions` | Completed payments, tx hashes |
| `market` | Price data, protocol research |
| `tick_outcome` | Per-tick summary |

## Variants

| Variant | Focus |
|---|---|
| [minimal](../minimal/) | Shell + memory only |
| [researcher](../researcher/) | Browser + research |
| [social](../social/) | GitHub + Telegram |
| **trader** | Wallet + DeFi + economics |
| [coder](../coder/) | GitHub + code evolution _(planned)_ |
