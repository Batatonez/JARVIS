"""`AIProvider`: interface que todo provider concreto (OpenRouter, e
futuramente Groq/Gemini/Mistral/NVIDIA) implementa. `ProviderRouter` só fala
com esta abstração — nunca com o SDK/API nativo de um provider específico."""

from abc import ABC, abstractmethod

from services.providers.types import ModelId, ModelCapability, ProviderId, ProviderStatus, RouteRequest, AIExecutionResult


class AIProvider(ABC):
    id: ProviderId

    @abstractmethod
    def is_configured(self) -> bool:
        """Existe uma credencial no ambiente para este provider? Nunca faz
        chamada de rede — só checa presença (ver `health()` para status real)."""

    @abstractmethod
    async def health(self) -> ProviderStatus:
        """Status atual — pode envolver uma checagem leve (ex.: endpoint de
        modelos), mas nunca gasta uma geração/completions real."""

    @abstractmethod
    def free_models(self) -> tuple[ModelId, ...]:
        """Modelos que este provider expõe como gratuitos, se algum."""

    @abstractmethod
    def supported_capabilities(self) -> frozenset[ModelCapability]:
        """Capacidades que este provider declara suportar (ver
        `docs/providers.md` — nem toda capability precisa estar implementada
        de verdade nesta etapa, só declarada corretamente)."""

    @abstractmethod
    async def execute(self, request: RouteRequest, *, model: ModelId) -> AIExecutionResult:
        """Executa `request` usando exatamente `model` (já decidido por
        `ProviderRouter.select()` — este método nunca escolhe modelo
        sozinho). Nunca levanta uma exceção crua do transporte HTTP: erros
        de rede/rate-limit viram `ProviderUnavailableError`/`RateLimitedError`
        (ver `services/providers/exceptions.py`)."""
