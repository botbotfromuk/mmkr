"""llmify — Domain-centric LLM contract-testing for emergent.

Annotate entities with Contract("...") and build AI[T] verification programs
that check contract-capability consistency, domain adequacy, and generate
test suggestions. The LLM is a provider — swappable, cacheable, advisory.

Usage::

    from dataclasses import dataclass
    from typing import Annotated
    from emergent.wire.axis.schema import Min, Max, Identity
    from llmify import Contract, contract_check

    @dataclass
    class Sensor:
        id: Annotated[int, Identity]
        temp: Annotated[float, Min(-40), Max(125),
                        Contract("Celsius. Outside range = malfunction.")]

    program = contract_check(Sensor, domain="industrial monitoring")
    result = await program.compile(provider)
"""

from llmify.claude_provider import ClaudeError, ClaudeProvider, make_claude_provider
from llmify.context import (
    LLMIFY_ENTITY_FOLD,
    LLMIFY_PHASE,
    LlmifyCompilable,
    LlmifyContext,
    LlmifyEntityCompilable,
    LlmifyEntityContext,
)
from llmify.contract import Contract, EntityContract
from llmify.programs import (
    DerivationInfo,
    FoldOutput,
    OperationInfo,
    PatternInfo,
    accumulate,
    audit_method_logic,
    check_adequacy,
    check_consistency,
    contract_check,
    suggest_invariants,
    suggest_tests,
)
from llmify.results import (
    AccumulatedResult,
    AdequacyResult,
    CapabilitySuggestion,
    ConsistencyIssue,
    ConsistencyResult,
    ContractCheckResult,
    InvariantSpec,
    MethodLogicAuditResult,
    MethodLogicIssue,
    TestSpec,
)

__all__ = [
    # Annotations
    "Contract",
    "EntityContract",
    # Compilation (field-level + entity-level)
    "LLMIFY_PHASE",
    "LlmifyCompilable",
    "LlmifyContext",
    "LLMIFY_ENTITY_FOLD",
    "LlmifyEntityCompilable",
    "LlmifyEntityContext",
    # Fold output
    "FoldOutput",
    # Derivation data
    "DerivationInfo",
    "PatternInfo",
    "OperationInfo",
    # Programs
    "contract_check",
    "check_consistency",
    "check_adequacy",
    "suggest_invariants",
    "suggest_tests",
    "audit_method_logic",
    "accumulate",
    # Results
    "ContractCheckResult",
    "AccumulatedResult",
    "ConsistencyResult",
    "CapabilitySuggestion",
    "ConsistencyIssue",
    "AdequacyResult",
    "InvariantSpec",
    "TestSpec",
    "MethodLogicAuditResult",
    "MethodLogicIssue",
    # Providers
    "ClaudeProvider",
    "ClaudeError",
    "make_claude_provider",
]
