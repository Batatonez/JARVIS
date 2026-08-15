"""`NvidiaProvider` — NVIDIA NIM (`https://integrate.api.nvidia.com/v1`),
OpenAI-compatible. Primeiro provider de fallback depois da OpenRouter
(v1.4.0, item 7).

Lê `NVIDIA_API_KEY` — uma única key para todos os modelos acessíveis pela
conta (nunca uma key por modelo). Desativável via `JARVIS_NVIDIA_ENABLED=0`.
Lista de modelos sobrescrevível via `JARVIS_NVIDIA_MODELS=modelo1,modelo2,...`
(ver `services/providers/env_config.py::resolve_model_override`).

--------------------------------------------------------------------------
CATÁLOGO — validado por chamada real durante o desenvolvimento (item 8)
--------------------------------------------------------------------------
Os 6 IDs desejados originalmente foram testados um a um contra a API real
(`GET /v1/models` + uma chamada mínima de chat completions). Resultado:

    nvidia/nemotron-3-ultra-550b-a55b   AVAILABLE  (200, ~5,6s)
    z-ai/glm-5.2                        AVAILABLE  (200, ~71s — modelo muito
                                         lento nesta conta; frequentemente vai
                                         estourar o timeout padrão e cair para
                                         o próximo candidato, o que é o
                                         comportamento CORRETO, não um bug)
    moonshotai/kimi-k2.6                UNAVAILABLE — HTTP 404:
                                         "Function ... Not found for account"
    mistralai/mistral-medium-3.5-128b   UNAVAILABLE — não existe no catálogo
                                         `GET /v1/models` desta conta
    nvidia/nemotron-3-super-120b-a12b   AVAILABLE  (200, ~0,7s)
    nvidia/nemotron-3-nano-30b-a3b      AVAILABLE  (200, ~0,7s)

Os dois indisponíveis foram REMOVIDOS da lista padrão — nenhum ID parecido
foi inventado no lugar deles (item 8 é explícito sobre isso). DeepSeek não foi
adicionado: o catálogo desta conta só expõe
`deepseek-ai/deepseek-coder-6.7b-instruct` e
`deepseek-ai/deepseek-v4-flash-0731`, nenhum dos quais foi pedido — e o
escopo proíbe adicionar DeepSeek "automaticamente" sem pedido explícito.
"""

from services.providers.openai_compatible import OpenAICompatibleProvider
from services.providers.types import ProviderId


class NvidiaProvider(OpenAICompatibleProvider):
    id = ProviderId.NVIDIA
    label = "NVIDIA NIM"
    env_key_name = "NVIDIA_API_KEY"
    env_enabled_name = "JARVIS_NVIDIA_ENABLED"
    env_models_name = "JARVIS_NVIDIA_MODELS"
    base_url = "https://integrate.api.nvidia.com/v1"
    default_models = (
        "nvidia/nemotron-3-ultra-550b-a55b",
        "z-ai/glm-5.2",
        "nvidia/nemotron-3-super-120b-a12b",
        "nvidia/nemotron-3-nano-30b-a3b",
    )
