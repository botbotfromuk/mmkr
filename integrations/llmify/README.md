# llmify — domain-centric LLM contract-testing

> Part of the mmkr monorepo. Originally by [prostomarkeloff](https://github.com/prostomarkeloff/llmify).

Annotate [emergent](https://github.com/prostomarkeloff/emergent) entities with `Contract("...")`, run `contract_check()`, get AI-powered verification reports.

## Quick start

```python
from dataclasses import dataclass
from typing import Annotated
from emergent.wire.axis.schema import Min, Max, Identity
from integrations.llmify import Contract, contract_check, make_claude_provider

@dataclass
class Sensor:
    id: Annotated[int, Identity]
    temp: Annotated[float, Min(-40), Max(125),
                    Contract("Celsius. Outside range = malfunction.")]

provider = make_claude_provider(api_key="sk-ant-...")
result = await contract_check(Sensor, domain="industrial monitoring").compile(provider)

for issue in result.consistency.issues:
    print(f"[{issue.severity}] {issue.field}: {issue.message}")
```

## Programs

| Function | What it checks |
|----------|---------------|
| `contract_check()` | Full: consistency + adequacy + tests + invariants |
| `check_consistency()` | Are contracts internally consistent? |
| `check_adequacy()` | Do contracts cover all domain rules? |
| `suggest_invariants()` | Propose `@invariant` rules |
| `suggest_tests()` | Generate edge-case tests |
| `audit_method_logic()` | Verify method impl matches contracts |
| `accumulate()` | Multi-entity cross-domain analysis |

## Notes on sanitization

- Removed hardcoded z.ai API key from `make_zai_provider()` — now requires `api_key=` explicitly
- Removed `~/.openclaw` OAuth path dependency from `make_claude_provider()` — use `api_key=` from env
