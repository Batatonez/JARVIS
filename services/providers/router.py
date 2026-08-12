"""`ProviderRouter` — a única autoridade sobre "qual provider, qual modelo,
qual custo" no JARVIS (ver `docs/providers.md`). Ruflo não participa desta
decisão: nada aqui chama `agent_spawn`/`agent_execute` (ver
`services/providers/ruflo_coordinator.py` para a fronteira com o Ruflo, e
`docs/ruflo-integration.md` para o bug de model routing que motivou manter
essas duas responsabilidades separadas).

Defesa em duas camadas para `free_only=True` (nunca confiar só no que foi
pedido):
1. **Antes da chamada** — `select()` só deixa `requested_model` ser um slug
   presente em `provider.free_models()`; nunca um modelo pago é enviado.
2. **Depois da chamada** — `execute()` confere o que o provider *relatou*
   ter servido/cobrado (`served_model`/`cost`) e levanta
   `NoFreeModelAvailableError` se isso não bater, mesmo que a chamada já
   tenha acontecido. Isto é detecção, não prevenção total: uma única
   chamada HTTP já ocorreu antes de sabermos o custo real — por isso a
   camada 1 (nunca pedir um modelo não-gratuito) é a defesa principal.
"""

import logging

from services.providers.base import AIProvider
from services.providers.exceptions import NoFreeModelAvailableError, ProviderError, ProviderNotConfiguredError
from services.providers.registry import ProviderRegistry
from services.providers.types import AIExecutionResult, ModelId, ProviderId, ProviderStatus, RouteDecision, RouteRequest

logger = logging.getLogger(__name__)


class ProviderRouter:
    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    async def select(self, request: RouteRequest) -> RouteDecision:
        """Decisão pura — nunca faz chamada de rede."""
        provider = self._pick_provider(request)
        model = self._pick_model(provider, request)
        if request.free_only and model not in provider.free_models():
            raise NoFreeModelAvailableError(
                f"{provider.id.value} não tem um modelo gratuito compatível com este pedido."
            )
        reason = "preferred_model" if request.preferred_model else ("free_only" if request.free_only else "default")
        return RouteDecision(provider=provider.id, requested_model=model, reason=reason)

    async def execute(self, request: RouteRequest) -> AIExecutionResult:
        decision = await self.select(request)
        provider = self._registry.get(decision.provider)
        assert provider is not None  # select() só devolve um provider que existe no registry

        result = await provider.execute(request, model=decision.requested_model)

        if request.free_only and result.success:
            self._verify_free_or_raise(result, provider)

        return result

    async def health(self) -> dict[ProviderId, ProviderStatus]:
        result: dict[ProviderId, ProviderStatus] = {}
        for descriptor in self._registry.descriptors():
            provider = self._registry.get(descriptor.id)
            if provider is None:
                result[descriptor.id] = descriptor.status  # NOT_IMPLEMENTED — sem instância pra checar
                continue
            result[descriptor.id] = await provider.health()
        return result

    def _pick_provider(self, request: RouteRequest) -> AIProvider:
        if request.preferred_provider is not None:
            provider = self._registry.get(request.preferred_provider)
            if provider is None:
                raise ProviderError(f"Provider '{request.preferred_provider.value}' não implementado nesta etapa.")
            if not provider.is_configured():
                raise ProviderNotConfiguredError(f"Provider '{request.preferred_provider.value}' sem API key configurada.")
            return provider

        candidates = self._registry.configured_providers()
        if request.free_only:
            candidates = [p for p in candidates if p.free_models()]
        if not candidates:
            if request.free_only:
                raise NoFreeModelAvailableError("Nenhum provider configurado oferece rota gratuita.")
            raise ProviderNotConfiguredError("Nenhum provider configurado — configure OPENROUTER_API_KEY (ou outro) no ambiente.")
        return candidates[0]

    @staticmethod
    def _pick_model(provider: AIProvider, request: RouteRequest) -> ModelId:
        if request.preferred_model:
            return request.preferred_model
        if request.free_only:
            free = provider.free_models()
            if not free:
                raise NoFreeModelAvailableError(f"{provider.id.value} não declara nenhum modelo gratuito.")
            return free[0]
        # Sem `preferred_model` e sem `free_only`: nunca inventamos um
        # modelo pago padrão. Esta etapa só conhece o slug gratuito da
        # OpenRouter — pedir algo pago exige `preferred_model` explícito.
        free = provider.free_models()
        if free:
            return free[0]
        raise ProviderError(
            f"{provider.id.value} não tem modelo padrão nesta etapa — passe RouteRequest.preferred_model."
        )

    @staticmethod
    def _verify_free_or_raise(result: AIExecutionResult, provider: AIProvider) -> None:
        cost = result.cost
        served = result.served_model

        if cost is not None and cost.is_free is False:
            # Veto explícito: o provider relatou custo > 0. Nunca ignorar
            # isso mesmo que `served_model` pareça um slug gratuito.
            raise NoFreeModelAvailableError(
                f"OpenRouter cobrou por esta chamada (served_model={served!r}, cost={cost.amount} {cost.currency}) — bloqueado."
            )

        confirmed_free = (cost is not None and cost.is_free is True) or (
            served is not None and (served in provider.free_models() or served.endswith(":free"))
        )
        if not confirmed_free:
            logger.warning(
                "free_only=True mas a resposta não confirma custo zero (served_model=%r, cost=%r) — sinalizando.",
                served,
                cost,
            )
            raise NoFreeModelAvailableError(
                f"Não foi possível confirmar que a rota servida era gratuita (served_model={served!r}, cost={cost!r})."
            )
