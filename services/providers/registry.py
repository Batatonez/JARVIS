"""`ProviderRegistry` — inventário de providers conhecidos.

**v1.4.0**: os 6 providers da cadeia de fallback (OpenRouter, NVIDIA,
Gemini, Groq, Cerebras, Mistral) têm classe real e são **sempre**
registrados por `build_default_registry()` — cada um decide sozinho se está
"configurado" (`is_configured()`), que já cobre tanto "sem API key" quanto
"desativado via `JARVIS_<PROVIDER>_ENABLED=0`" (ver
`services/providers/openai_compatible.py`). Isso simplifica o registry: ele
nunca precisa saber SE um provider está habilitado, só ITERAR os que estão
`is_configured()`.

Anthropic continua como entrada de inventário apenas (`NOT_IMPLEMENTED` no
sentido específico de "não integrado a ESTE router") — tem uma integração
real separada (`services/ai_service.py` + `services/claude_agent_provider.py`),
fora de escopo desta versão (ver `docs/ruflo-integration.md`)."""

from services.providers.base import AIProvider
from services.providers.types import ProviderDescriptor, ProviderId, ProviderStatus

# Providers sem implementação real nesta camada — só entrada de inventário,
# nunca instanciado.
_PLANNED_PROVIDER_IDS = (ProviderId.ANTHROPIC,)


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
    rede: nenhum `AIProvider.__init__` conecta nada, cada um só lê variável
    de ambiente). `register_openrouter=False` existe para os testes
    construírem um registry vazio de OpenRouter sem depender de
    `OPENROUTER_API_KEY` — os 5 providers novos sempre são registrados
    (cada um `is_configured()==False` sozinho quando sem key, então não
    afeta nenhum teste que espera "nenhum provider configurado")."""
    registry = ProviderRegistry()
    if register_openrouter:
        from services.providers.openrouter_provider import OpenRouterProvider

        registry.register(OpenRouterProvider())

    # v1.4.0 — cadeia de fallback. Import local (mesmo padrão de sempre
    # neste módulo): mantém o import de topo do arquivo livre de qualquer
    # dependência pesada, e cada provider só é importado quando o registry
    # de produção é de fato construído.
    from services.providers.cerebras_provider import CerebrasProvider
    from services.providers.gemini_provider import GeminiProvider
    from services.providers.groq_provider import GroqProvider
    from services.providers.mistral_provider import MistralProvider
    from services.providers.nvidia_provider import NvidiaProvider

    registry.register(NvidiaProvider())
    registry.register(GeminiProvider())
    registry.register(GroqProvider())
    registry.register(CerebrasProvider())
    registry.register(MistralProvider())
    return registry
