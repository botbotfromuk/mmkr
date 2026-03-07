"""mmkr — autonomous life on emergent.

Everything is fold. Life = list[LifeCapability].

Usage::

    from mmkr import Life, ShellAccess, BrowserAccess, Knowledge, Seed
    from mmkr.life import PersistentMemory, CapabilityEvolver
    from pathlib import Path

    life = Life(
        capabilities=(
            ShellAccess(),
            BrowserAccess(),
            Knowledge(text="You are autonomous."),
            PersistentMemory(memory_dir=Path(".data")),
            CapabilityEvolver(evolved_dir=Path(".data/caps")),
            Seed(text="Hello world"),
        ),
        memory_dir=Path(".data"),
    )
    state = await life.run(provider, max_ticks=10)
"""

# State & fold
from mmkr.state import AgentState, LifeCapability, LifeContext, VerifyPhaseSpec, fold_life

# Evolution axis
from mmkr.state import (
    EvolutionCapability,
    EvolutionContext,
    EvolutionEvent,
    FitnessRecord,
    Preloadable,
    compute_fitness,
    fold_evolution,
)

# Capabilities
from mmkr.caps import (
    AnthropicKey,
    BlockchainWallet,
    BrowserAccess,
    EmailAccess,
    GitHubAccess,
    GmailAccess,
    ShellAccess,
)

# Knowledge
from mmkr.knowledge import Clock, EmergentKnowledge, Knowledge, SecretKnowledge

# Life — the eternal fold
from mmkr.life import (
    AsyncDelegation,
    CapabilityEvolver,
    ConversationLog,
    DelegationCollectPhase,
    EvolutionStorage,
    Life,
    MemoryRecord,
    PersistentMemory,
    Seed,
    SubAgentCapability,
)

# Evolution — Modern Evolutionary Synthesis as capabilities
from mmkr.evolution import (
    AdaptiveLandscape,
    DevelopmentalBias,
    GeneticDrift,
    MutationPressure,
    NaturalSelection,
    NicheConstruction,
    Recombination,
)

# InnerLife — System 1 unconscious processes
from mmkr.inner_life import InnerLife, InnerLifePhase

# Telegram
from mmkr.telegram import TelegramAccess, TelegramNotifyPhase

# GitBrain
from mmkr.git_brain import EpisodicWritePhase, GitBrain, GitCommitPhase

# Trace
from mmkr.trace import ConsoleCollector, FileCollector, MultiCollector, TickTraceCollector

__all__ = [
    # State
    "AgentState",
    "LifeContext",
    "LifeCapability",
    "VerifyPhaseSpec",
    "fold_life",
    # Evolution
    "EvolutionContext",
    "EvolutionCapability",
    "EvolutionEvent",
    "FitnessRecord",
    "Preloadable",
    "compute_fitness",
    "fold_evolution",
    # Capabilities
    "ShellAccess",
    "BrowserAccess",
    "GmailAccess",
    "EmailAccess",
    "GitHubAccess",
    "BlockchainWallet",
    "AnthropicKey",
    # Knowledge
    "Knowledge",
    "SecretKnowledge",
    "Clock",
    "EmergentKnowledge",
    # Life
    "Life",
    "PersistentMemory",
    "CapabilityEvolver",
    "ConversationLog",
    "EvolutionStorage",
    "MemoryRecord",
    "Seed",
    "AsyncDelegation",
    "SubAgentCapability",
    "DelegationCollectPhase",
    # Evolution — Modern Synthesis
    "NaturalSelection",
    "GeneticDrift",
    "MutationPressure",
    "Recombination",
    "NicheConstruction",
    "DevelopmentalBias",
    "AdaptiveLandscape",
    # InnerLife
    "InnerLife",
    "InnerLifePhase",
    # Telegram
    "TelegramAccess",
    "TelegramNotifyPhase",
    # GitBrain
    "GitBrain",
    "GitCommitPhase",
    "EpisodicWritePhase",
    # Trace
    "TickTraceCollector",
    "ConsoleCollector",
    "FileCollector",
    "MultiCollector",
]
