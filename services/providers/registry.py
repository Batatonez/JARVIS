"""`ProviderRegistry` — inventário de providers conhecidos. Só OpenRouter
tem uma classe real registrada nesta etapa (ver item 10 do pedido); os
demais aparecem como `ProviderDescriptor(status=NOT_IMPLEMENTED)`, sem
nenhuma classe/import associado — existem só para o HUD/CLI já poderem
listar "o que existe hoje vs. o que está planejado" sem `if`s espalhados
pelo projeto."""

from services.providers.base import AIProvider
from services.providers.types import ProviderDescriptor, ProviderId, ProviderStatus

# Providers sem implementação real nesta etapa (item 10/14 do pedido) — só
# entradas de inventário, nunca instanciados.
_PLANNED_PROVIDER_IDS = (
    ProviderId.GROQ,
    ProviderId.GEMINI,
    ProviderId.MISTRAL,
    ProviderId.NVIDIA,
    # Anthropic já tem uma integração real no JARVIS (services/ai_service.py
    # + services/claude_agent_provider.py) — mas não está conectada a ESTE
    # Provider Router ainda ("não alterar", item 10). Aparece aqui como
    # NOT_IMPLEMENTED no sentido específico de "não integrado a este router".
    ProviderId.ANTHROPIC,
)


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[ProviderId, AIProvider] = {}

    def register(self, provider: AIProvider) -> None:
        self._providers[provider.id] = provider

    def get(self, provider_id: ProviderId) -> AIProvider | None:
        return self._providers.get(provider_id)

    def configured_providers(self) -> list[AIProvider]:
        return [p for p in self._providers.values() if p.is_configured()]

    def descriptors(self) -> list[ProviderDescriptor]:
        """Um `ProviderDescriptor` por `ProviderId` conhecido — registrados
        primeiro, depois os planejados (ordem estável, não alfabética, para
        casar com a lista do pedido original)."""
        result: list[ProviderDescriptor] = []
        seen: set[ProviderId] = set()
        for provider in self._providers.values():
            status = ProviderStatus.AVAILABLE if provider.is_configured() else ProviderStatus.NOT_CONFIGURED
            capabilities = provider.supported_capabilities()
            from services.providers.types import ModelCapability  # import local: só usado aqui

            result.append(
                ProviderDescriptor(
                    id=provider.id,
                    status=status,
                    supports_chat=ModelCapability.CHAT in capabilities,
                    supports_tools=ModelCapability.TOOLS in capabilities,
                    supports_streaming=ModelCapability.STREAMING in capabilities,
                    supports_embeddings=ModelCapability.EMBEDDINGS in capabilities,
                    free_models=provider.free_models(),
                )
            )
            seen.add(provider.id)
        for provider_id in _PLANNED_PROVIDER_IDS:
            if provider_id in seen:
                continue
            result.append(ProviderDescriptor(id=provider_id, status=ProviderStatus.NOT_IMPLEMENTED))
        return result


def build_default_registry(*, register_openrouter: bool = True) -> ProviderRegistry:
    """Registry "de produção" — só chama construtores reais (sem tocar
    rede: `AIProvider.__init__` nunca conecta nada). `register_openrouter`
    existe para os testes poderem construir um registry vazio sem depender
    de `OPENROUTER_API_KEY`."""
    registry = ProviderRegistry()
    if register_openrouter:
        from services.providers.openrouter_provider import OpenRouterProvider

        registry.register(OpenRouterProvider())
    return registry
