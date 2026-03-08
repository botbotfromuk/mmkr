"""Pydantic models for LLM structured output.

These are the schemas passed to funcai's AI.ask(dialogue, schema=T).
The LLM returns JSON, Pydantic validates and parses it.
All models are serializable, diffable, cacheable.
"""

from __future__ import annotations

from pydantic import BaseModel


class CapabilitySuggestion(BaseModel):
    """A suggested new or modified emergent capability."""

    stub: str  # one-liner, e.g. 'SoftRange = Annotated[float, SchemaAxisCapability]'
    rationale: str


class ConsistencyIssue(BaseModel):
    """One inconsistency between contract text and capabilities."""

    field: str
    severity: str  # "error" | "warning" | "info"
    message: str
    contract_excerpt: str = ""
    capability_repr: str = ""
    suggested_capability: CapabilitySuggestion | None = None


class ConsistencyResult(BaseModel):
    """Contract-vs-capabilities consistency check result."""

    issues: list[ConsistencyIssue]
    summary: str


class AdequacyResult(BaseModel):
    """Domain adequacy check for one field's constraints."""

    field: str
    adequate: bool
    reasoning: str
    suggestions: list[str]
    suggested_capabilities: list[CapabilitySuggestion] = []


class InvariantSpec(BaseModel):
    """A property-based test invariant suggested by the LLM."""

    name: str
    description: str
    property_description: str
    fields: list[str]


class TestSpec(BaseModel):
    """A concrete test case suggested by the LLM."""

    name: str
    description: str
    setup: str
    assertion: str
    fields: list[str]


class MethodLogicIssue(BaseModel):
    """One issue found in method implementation logic."""

    method: str
    severity: str  # "error" | "warning" | "info"
    message: str
    code_excerpt: str = ""
    contract_excerpt: str = ""
    expected_behavior: str = ""
    actual_behavior: str = ""


class MethodLogicAuditResult(BaseModel):
    """Method-level code logic audit result."""

    issues: list[MethodLogicIssue]
    summary: str


class ContractCheckResult(BaseModel):
    """Full contract verification result — all checks combined."""

    consistency: ConsistencyResult
    adequacy: list[AdequacyResult]
    invariants: list[InvariantSpec]
    tests: list[TestSpec]


class AccumulatedResult(BaseModel):
    """Accumulated verification result across multiple LLM passes."""

    consistency_issues: list[ConsistencyIssue]
    method_issues: list[MethodLogicIssue]
    passes_run: int
    issues_per_pass: list[int]
