"""Providers subpackage.

Architecture:
    CORE (types/base)  →  INTERFACE (LLMProvider)
                                       ↓
                                  ADAPTER (Mock, OpenAI, Anthropic, Groq,
                                           Ollama, ZAI, NVIDIA)
                                       ↓
                                  PROVIDER (ProviderRegistry + ProviderRouter
                                             + ProviderFallback + ProviderHealth)

Public API:
    from albra_seed.providers import (
        LLMProvider,
        ProviderRegistry,
        ProviderRouter,
        ProviderFallback,
        ProviderHealth,
        ProviderMetrics,
        MockProvider,
        OpenAIProvider,
        AnthropicProvider,
        GroqProvider,
        OllamaProvider,
        ZAIProvider,
        NVIDIAProvider,
    )

All external-provider modules are imported lazily by their consumers;
this `__init__` only imports the always-available pieces (Mock + the
registry/router/health/metrics scaffolding). Importing the optional
providers is done with explicit `from albra_seed.providers.openai_provider
import OpenAIProvider` calls, so CORE_ONLY mode never pulls in the SDKs.
"""

from __future__ import annotations

from albra_seed.core.base import LLMProvider
from albra_seed.providers.fallback import ProviderFallback
from albra_seed.providers.health import ProviderHealth
from albra_seed.providers.metrics import ProviderMetrics
from albra_seed.providers.mock import MockProvider
from albra_seed.providers.registry import ProviderRegistry
from albra_seed.providers.router import ProviderRouter

# Optional providers — imported here for convenience but each module
# only imports its SDK lazily inside __init__, so the package import
# itself never fails in CORE_ONLY mode.
from albra_seed.providers.openai_provider import OpenAIProvider
from albra_seed.providers.anthropic_provider import AnthropicProvider
from albra_seed.providers.groq_provider import GroqProvider
from albra_seed.providers.ollama_provider import OllamaProvider
from albra_seed.providers.zai_provider import ZAIProvider
from albra_seed.providers.nvidia_provider import NVIDIAProvider

__all__ = [
    "LLMProvider",
    "ProviderRegistry",
    "ProviderRouter",
    "ProviderFallback",
    "ProviderHealth",
    "ProviderMetrics",
    "MockProvider",
    "OpenAIProvider",
    "AnthropicProvider",
    "GroqProvider",
    "OllamaProvider",
    "ZAIProvider",
    "NVIDIAProvider",
]
