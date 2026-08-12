"""Provider Router — o JARVIS decide provider/modelo/custo/fallback; o
Ruflo (quando conectado no futuro) só coordena agentes. Ver
`docs/providers.md` e `docs/ruflo-integration.md`.

Fundação desta etapa (aditiva — nada aqui é chamado pelo `JarvisCore`/
`JarvisApplication` existentes ainda): só `OpenRouterProvider` tem
implementação real; os demais providers existem só como entradas de
registry `NOT_IMPLEMENTED`.
"""

from services.providers.exceptions import (
    NoFreeModelAvailableError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderNotImplementedError,
    ProviderUnavailableError,
    RateLimitedError,
)
from services.providers.registry import ProviderRegistry, build_default_registry
from services.providers.router import ProviderRouter
from services.providers.types import (
    AIExecutionResult,
    CostInfo,
    ModelCapability,
    ModelId,
    ProviderDescriptor,
    ProviderId,
    ProviderStatus,
    RouteDecision,
    RouteRequest,
    UsageInfo,
)

__all__ = [
    "AIExecutionResult",
    "CostInfo",
    "ModelCapability",
    "ModelId",
    "NoFreeModelAvailableError",
    "ProviderDescriptor",
    "ProviderError",
    "ProviderId",
    "ProviderNotConfiguredError",
    "ProviderNotImplementedError",
    "ProviderRegistry",
    "ProviderRouter",
    "ProviderStatus",
    "ProviderUnavailableError",
    "RateLimitedError",
    "RouteDecision",
    "RouteRequest",
    "UsageInfo",
    "build_default_registry",
]
